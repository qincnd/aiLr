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
MAX_PENDING_JOBS = 20
MAX_RESULT_BYTES = 300 * 1024 * 1024
MAX_REPORT_CHARS = 4000
SUPPORTED_EXPORT_FORMATS = {"JPEG", "PNG", "TIFF"}


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
            if not self.status()["connected"]:
                raise RuntimeError("Lightroom 插件未连接；请先在 LrC 中启动 aiLr Bridge")
            # Probing only reads develop values, so it works without a selected photo.
            if request.action != "probe" and not self._selected_filename:
                raise RuntimeError("请先在 Lightroom Classic 中选中一张照片")
            if len(self._queue) >= MAX_PENDING_JOBS:
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
            while self._queue:
                job = self._jobs.get(self._queue.popleft())
                if job and job.status == "queued":
                    job.status = "processing"
                    return job
            return None

    async def get_job(self, job_id: str) -> LightroomJob | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def consume_image(self, job_id: str) -> tuple[LightroomJob, bytes] | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "completed" or job.image is None:
                return None
            image = job.image
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
            return job

    async def fail_job(self, job_id: str, error: str) -> LightroomJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "processing":
                return None
            job.error = error.strip()[:1000] or "Lightroom 渲染失败"
            job.status = "failed"
            return job

    async def record_report(self, job_id: str, report: str) -> LightroomJob | None:
        """Store a plug-in report: accepted/rejected develop keys or a probe result."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "processing":
                return None
            job.report = report.strip()[:MAX_REPORT_CHARS]
            return job

    async def clear(self) -> None:
        async with self._lock:
            self._jobs.clear()
            self._queue.clear()
            self._last_seen = 0.0
            self._selected_filename = ""


lightroom_bridge = LightroomBridge()
