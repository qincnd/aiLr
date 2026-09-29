from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.settings import validate_develop_settings


HEARTBEAT_TTL_SECONDS = 15
# Queued *and* processing jobs count against this cap: the queue length alone let a
# slow plug-in pile up unbounded work behind the jobs it had already taken.
MAX_ACTIVE_JOBS = 20
MAX_RESULT_BYTES = 300 * 1024 * 1024
MAX_REPORT_CHARS = 4000
# A job the plug-in claimed but never answered (Lightroom was closed, the render
# crashed, the result upload was lost) must not stay "processing" forever: the web
# page polls for at most four minutes, so after this window the job is turned into a
# failure carrying a readable reason instead of leaving the table stuck. Finished jobs
# keep their rendered image (still downloadable, same window) and are dropped after it,
# so a long session cannot grow the job table or hold a 300 MB result indefinitely.
JOB_PROCESSING_TIMEOUT_SECONDS = 900
JOB_RETENTION_SECONDS = 900


class LightroomJobRequest(BaseModel):
    action: Literal["preview", "export", "probe"]
    settings: dict[str, Any] = Field(default_factory=dict)
    format: Literal["JPEG", "PNG", "TIFF"] = "JPEG"
    quality: int = Field(default=90, ge=1, le=100)
    max_dimension: int = Field(default=2560, ge=256, le=12000)

    @model_validator(mode="after")
    def validate_settings(self) -> LightroomJobRequest:
        # A probe job only asks Lightroom which develop parameters it accepts, so
        # it is allowed to carry no settings at all.
        if self.action == "probe":
            self.settings = {}
            return self
        self.settings = validate_develop_settings(self.settings)
        return self


@dataclass
class LightroomJob:
    job_id: str
    request: LightroomJobRequest
    status: str = "queued"
    filename: str = ""
    content_type: str = ""
    image: bytes | None = None
    error: str = ""
    report: str = ""
    created_at: float = 0.0
    # When the plug-in claimed the job / when it reached a final state; both feed the
    # stale job reaper in _reclaim(), which is what stops a lost render from leaving
    # the table stuck in "processing".
    started_at: float = 0.0
    finished_at: float = 0.0


class LightroomBridge:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._jobs: dict[str, LightroomJob] = {}
        self._queue: deque[str] = deque()
        self._last_seen = 0.0
        self._selected_filename = ""

    def status(self) -> dict[str, object]:
        online = time.monotonic() - self._last_seen <= HEARTBEAT_TTL_SECONDS
        return {
            "connected": online,
            "selected_filename": self._selected_filename if online else "",
            "last_seen_seconds": round(time.monotonic() - self._last_seen, 1)
            if self._last_seen
            else None,
        }

    async def heartbeat(self, selected_filename: str) -> None:
        self._last_seen = time.monotonic()
        self._selected_filename = selected_filename.strip()[:260]

    async def create_job(self, request: LightroomJobRequest) -> LightroomJob:
        async with self._lock:
            self._reclaim()
            if not self.status()["connected"]:
                raise RuntimeError("Lightroom 插件未连接；请先在 LrC 中启动 aiLr Bridge")
            # Probing only reads develop values, so it works without a selected photo.
            if request.action != "probe" and not self._selected_filename:
                raise RuntimeError("请先在 Lightroom Classic 中选中一张照片")
            if self._active_job_count() >= MAX_ACTIVE_JOBS:
                raise RuntimeError("Lightroom 渲染队列已满，请稍后重试")
            job = LightroomJob(
                job_id=uuid.uuid4().hex,
                request=request,
                created_at=time.time(),
            )
            self._jobs[job.job_id] = job
            self._queue.append(job.job_id)
            return job

    async def take_next_job(self) -> LightroomJob | None:
        async with self._lock:
            self._reclaim()
            while self._queue:
                job = self._jobs.get(self._queue.popleft())
                if job and job.status == "queued":
                    job.status = "processing"
                    job.started_at = time.time()
                    return job
            return None

    async def get_job(self, job_id: str) -> LightroomJob | None:
        async with self._lock:
            self._reclaim()
            return self._jobs.get(job_id)

    async def consume_image(self, job_id: str) -> tuple[LightroomJob, bytes] | None:
        async with self._lock:
            self._reclaim()
            job = self._jobs.get(job_id)
            if not job or job.status != "completed" or job.image is None:
                return None
            image = job.image
            # The bytes are handed to the caller and dropped here, so a preview that
            # nobody downloads does not keep 300 MB alive until the retention window.
            job.image = None
            return job, image

    async def complete_job(
        self,
        job_id: str,
        image: bytes,
        content_type: str,
        filename: str,
    ) -> LightroomJob | None:
        if len(image) > MAX_RESULT_BYTES:
            raise ValueError("Lightroom 导出结果超过 300 MB")
        if content_type not in {"image/jpeg", "image/png", "image/tiff"}:
            raise ValueError("Lightroom 返回了不支持的图片格式")
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "processing":
                return None
            job.image = image
            job.content_type = content_type
            leaf_name = PurePosixPath(filename.replace("\\", "/")).name
            job.filename = re.sub(r"[\r\n\x00]", "", leaf_name)[:260] or f"ailr-{job_id}.{content_type.rsplit('/', 1)[-1]}"
            job.status = "completed"
            job.finished_at = time.time()
            return job

    async def fail_job(self, job_id: str, error: str) -> LightroomJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "processing":
                return None
            job.error = error.strip()[:1000] or "Lightroom 渲染失败"
            job.status = "failed"
            job.finished_at = time.time()
            return job

    async def record_report(self, job_id: str, report: str) -> LightroomJob | None:
        """Store a plug-in report: accepted/rejected develop keys or a probe result."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "processing":
                return None
            job.report = report.strip()[:MAX_REPORT_CHARS]
            return job

    def _active_job_count(self) -> int:
        """Jobs the plug-in still has to answer, queued or already claimed."""
        return sum(1 for job in self._jobs.values() if job.status in {"queued", "processing"})

    def _reclaim(self) -> None:
        """Fail stale in-flight jobs and drop finished ones. Call with the lock held.

        Called from every path that reads the table, so a render whose result never
        arrives becomes a readable failure (``job.error``) instead of leaving the web
        page polling until its own timeout, and a long session does not accumulate job
        entries or hold rendered images forever.
        """
        now = time.time()
        for job_id, job in list(self._jobs.items()):
            if job.status == "processing":
                started = job.started_at or job.created_at
                if now - started > JOB_PROCESSING_TIMEOUT_SECONDS:
                    job.status = "failed"
                    job.error = (
                        "Lightroom 渲染超时：插件在 "
                        f"{JOB_PROCESSING_TIMEOUT_SECONDS // 60} 分钟内没有回传结果，"
                        "请确认 LrC 仍打开、aiLr Bridge 正在运行且已选中同名照片，然后重试。"
                    )
                    job.image = None
                    job.finished_at = now
            elif job.status != "queued":
                if now - (job.finished_at or job.created_at) > JOB_RETENTION_SECONDS:
                    self._jobs.pop(job_id, None)


lightroom_bridge = LightroomBridge()
