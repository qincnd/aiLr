import json

import httpx
from typing import Any
from urllib.parse import quote, unquote
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.config import (
    ModelConfig,
    OLLAMA_CONTEXT_SIZE,
    load_model_config,
    public_model_config,
    save_model_config,
    settings,
)
from app.model_runtime import runtime
from app.presets import (
    PresetDraft,
    PresetError,
    PresetFileError,
    create_preset,
    delete_preset,
    load_presets,
    presets_payload,
)
from app.services.image_processing import (
    ImagePreparationError,
    is_raw_image,
    prepare_image_for_model,
)
from app.services.llm import generate_suggestion
from app.services.lightroom_bridge import (
    MAX_RESULT_BYTES,
    LightroomJobRequest,
    lightroom_bridge,
)
from app.settings import develop_controls_payload, model_keys_for


app = FastAPI(title="aiLr", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/lightroom/status")
def lightroom_status() -> dict[str, object]:
    return lightroom_bridge.status()


@app.post("/api/lightroom/heartbeat")
async def lightroom_heartbeat(request: Request) -> dict[str, object]:
    selected_filename = (await request.body()).decode("utf-8", errors="replace")
    await lightroom_bridge.heartbeat(selected_filename)
    return lightroom_bridge.status()


@app.post("/api/lightroom/jobs")
async def create_lightroom_job(payload: LightroomJobRequest) -> dict[str, str]:
    try:
        job = await lightroom_bridge.create_job(payload)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"job_id": job.job_id, "status": job.status, "action": payload.action}


def _encode_setting_value(value: Any) -> str:
    """Serialize one develop value for the line based plug-in protocol.

    Numbers stay readable (``Temperature=5600``), while booleans, enum names and
    point curves are percent-encoded so the plug-in can decode them without
    guessing which parameter it is looking at.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{float(value):g}"
    if isinstance(value, str):
        return quote(value, safe="")
    if isinstance(value, list):
        # Point curves travel as "x,y;x,y" so the plug-in can rebuild the table.
        return quote(
            ";".join(f"{float(point[0]):g},{float(point[1]):g}" for point in value),
            safe="",
        )
    raise ValueError(f"无法编码的调色参数值: {value!r}")


@app.get("/api/lightroom/jobs/next")
async def next_lightroom_job() -> Response:
    job = await lightroom_bridge.take_next_job()
    if not job:
        return Response(status_code=204)

    request = job.request
    lines = [job.job_id, request.action, request.format, str(request.quality), str(request.max_dimension)]
    lines.extend(f"{key}={_encode_setting_value(value)}" for key, value in request.settings.items())
    return Response("\n".join(lines), media_type="text/plain; charset=utf-8")


@app.post("/api/lightroom/jobs/{job_id}/result")
async def complete_lightroom_job(job_id: str, request: Request) -> dict[str, str]:
    body = await request.body()
    if not body or len(body) > MAX_RESULT_BYTES:
        raise HTTPException(status_code=413, detail=f"Lightroom 图片为空或超过 {MAX_RESULT_BYTES // (1024 * 1024)} MB")
    try:
        job = await lightroom_bridge.complete_job(
            job_id,
            body,
            request.headers.get("content-type", "").split(";", 1)[0].lower(),
            unquote(request.headers.get("x-ailr-filename", "")),
        )
    except ValueError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    if not job:
        raise HTTPException(status_code=404, detail="Lightroom 渲染任务不存在或已结束")
    return {"job_id": job_id, "status": job.status}


@app.post("/api/lightroom/jobs/{job_id}/failed")
async def fail_lightroom_job(job_id: str, request: Request) -> dict[str, str]:
    error = (await request.body()).decode("utf-8", errors="replace")
    job = await lightroom_bridge.fail_job(job_id, error)
    if not job:
        raise HTTPException(status_code=404, detail="Lightroom 渲染任务不存在或已结束")
    return {"job_id": job_id, "status": job.status}


@app.post("/api/lightroom/jobs/{job_id}/report")
async def report_lightroom_job(job_id: str, request: Request) -> dict[str, str]:
    """Collect a plug-in report: rejected develop keys or a parameter probe result."""
    report = (await request.body()).decode("utf-8", errors="replace")
    job = await lightroom_bridge.record_report(job_id, report)
    if not job:
        raise HTTPException(status_code=404, detail="Lightroom 渲染任务不存在或已结束")
    return {"job_id": job_id, "status": job.status}


@app.get("/api/lightroom/jobs/{job_id}")
async def get_lightroom_job(job_id: str) -> dict[str, object]:
    job = await lightroom_bridge.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到 Lightroom 渲染任务")
    return {
        "job_id": job.job_id,
        "action": job.request.action,
        "status": job.status,
        "filename": job.filename,
        "content_type": job.content_type,
        "error": job.error,
        "report": job.report,
    }


@app.get("/api/lightroom/jobs/{job_id}/image")
async def get_lightroom_image(job_id: str, download: bool = False) -> Response:
    result = await lightroom_bridge.consume_image(job_id)
    if not result:
        job = await lightroom_bridge.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="找不到 Lightroom 渲染任务")
        if job.status == "completed":
            raise HTTPException(status_code=410, detail="此 Lightroom 图片已下载并从缓存释放")
        if job.status == "failed":
            # A render that timed out or was rejected has no image; report why instead
            # of claiming it is still running.
            raise HTTPException(status_code=409, detail=job.error or "Lightroom 渲染失败")
        raise HTTPException(status_code=409, detail="Lightroom 渲染尚未完成")
    job, image = result
    disposition = "attachment" if download else "inline"
    filename = "".join(
        character if character.isascii() and character not in '";\r\n\\' else "_"
        for character in job.filename
    ) or "ailr-render"
    encoded_filename = quote(job.filename, safe="")
    return Response(
        image,
        media_type=job.content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename=\"{filename}\"; filename*=UTF-8''{encoded_filename}"
        },
    )


async def read_photo_upload(photo: UploadFile) -> tuple[bytes, str, bool]:
    filename = photo.filename or ""
    is_raw = is_raw_image(filename)
    content_type = photo.content_type or "application/octet-stream"
    if not is_raw and not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="请上传图片或受支持的相机 RAW 文件")

    max_size_mb = settings.max_raw_image_mb if is_raw else settings.max_image_mb
    image_data = await photo.read(max_size_mb * 1024 * 1024 + 1)
    if len(image_data) > max_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"图片不能超过 {max_size_mb} MB")
    if not image_data:
        raise HTTPException(status_code=400, detail="上传的图片为空")
    return image_data, content_type, is_raw


@app.get("/api/health")
def health() -> dict[str, object]:
    model_config = load_model_config()
    bridge = lightroom_bridge.status()
    # The bridge state is reported from its heartbeat, not guessed: the web UI uses
    # /api/lightroom/status for the same numbers, and this keeps the two in step.
    if bridge["connected"]:
        lightroom = f"plugin connected · {bridge['selected_filename'] or '未选中照片'}"
    else:
        lightroom = "plugin bridge not connected"
    return {
        "status": "ok",
        "provider": model_config.provider,
        "model": model_config.model_name,
        "model_active": runtime.active,
        "model_message": runtime.message,
        "lightroom": lightroom,
        # Upload caps come from .env (AILR_MAX_IMAGE_MB / AILR_MAX_RAW_IMAGE_MB) so the
        # web UI can print the limits the backend actually enforces.
        "max_image_mb": settings.max_image_mb,
        "max_raw_image_mb": settings.max_raw_image_mb,
    }


@app.get("/api/develop/controls")
def develop_controls() -> dict[str, object]:
    """Every Lightroom Classic develop parameter aiLr can drive, grouped by panel."""
    return develop_controls_payload()


@app.get("/api/presets")
def list_presets() -> dict[str, object]:
    """调色预设（创作说明模板）：来自项目根目录的 color_presets.json。"""
    try:
        return presets_payload()
    except PresetFileError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except PresetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/presets", status_code=201)
def add_preset(draft: PresetDraft) -> dict[str, object]:
    """在网页里新建一条预设，写回 color_presets.json 并返回最新列表。"""
    try:
        created = create_preset(draft)
        presets = load_presets()
    except PresetFileError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except PresetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "preset": created.model_dump(),
        "presets": [preset.model_dump() for preset in presets],
    }


@app.delete("/api/presets/{preset_id}")
def remove_preset(preset_id: str) -> dict[str, object]:
    """删除一条预设并返回最新列表；预设文件本身也可以直接手工编辑。"""
    try:
        removed = delete_preset(preset_id)
        presets = load_presets()
    except PresetFileError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except PresetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not removed:
        raise HTTPException(status_code=404, detail=f"没有找到 id 为 {preset_id} 的预设。")
    return {"presets": [preset.model_dump() for preset in presets]}


@app.get("/api/model/config")
def get_model_configuration() -> dict[str, str | bool]:
    return public_model_config()


@app.get("/api/models/local")
async def list_local_models() -> dict[str, list[dict[str, object]]]:
    model_config = load_model_config()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{model_config.ollama_base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=503,
            detail=f"无法读取 Ollama 本地模型列表，请确认 Ollama 正在运行: {error}",
        ) from error

    try:
        payload = response.json()
    except ValueError as error:
        raise HTTPException(status_code=502, detail="Ollama 返回了无效的模型清单") from error

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Ollama 返回了无效的模型清单")

    listed = payload.get("models", [])
    models = listed if isinstance(listed, list) else []

    result: list[dict[str, object]] = []
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            continue
        raw_details = model.get("details")
        details = raw_details if isinstance(raw_details, dict) else {}
        result.append(
            {
                "name": model["name"],
                "size": int(model.get("size", 0) or 0),
                "modified_at": str(model.get("modified_at", "")),
                "family": str(details.get("family", "")),
                "parameter_size": str(details.get("parameter_size", "")),
            }
        )
    return {"models": sorted(result, key=lambda model: str(model["name"]).casefold())}


@app.put("/api/model/config")
def update_model_configuration(config: ModelConfig) -> dict[str, str | bool]:
    current = load_model_config()
    if not config.api_key and current.api_key:
        config = config.model_copy(update={"api_key": current.api_key})
    saved = save_model_config(config)
    runtime.active = False
    runtime.message = "配置已保存，等待启动"
    return public_model_config(saved)


@app.post("/api/model/start")
async def start_model() -> dict[str, str | bool]:
    model_config = load_model_config()
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            if model_config.provider == "ollama":
                response = await client.post(
                    f"{model_config.ollama_base_url.rstrip('/')}/api/generate",
                    json={
                        "model": model_config.model_name,
                        "prompt": "Reply with READY.",
                        "stream": False,
                        "keep_alive": "5m",
                        "options": {"num_predict": 1, "num_ctx": OLLAMA_CONTEXT_SIZE},
                    },
                )
            else:
                if not model_config.api_key:
                    raise HTTPException(status_code=422, detail="请先填写云端 API Key 并保存")
                response = await client.get(
                    f"{model_config.openai_base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {model_config.api_key}"},
                )
            response.raise_for_status()
    except HTTPException:
        raise
    except httpx.HTTPError as error:
        runtime.active = False
        runtime.message = "模型启动失败"
        raise HTTPException(status_code=503, detail=f"模型启动或连接失败: {error}") from error

    runtime.active = True
    runtime.message = "本地模型已加载" if model_config.provider == "ollama" else "云端模型连接已验证"
    return {"active": True, "message": runtime.message}


@app.post("/api/model/stop")
async def stop_model() -> dict[str, str | bool]:
    model_config = load_model_config()
    if runtime.active and model_config.provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{model_config.ollama_base_url.rstrip('/')}/api/generate",
                    json={"model": model_config.model_name, "prompt": "", "keep_alive": 0},
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail=f"无法停止本地模型: {error}") from error
    runtime.active = False
    runtime.message = "模型已停止"
    return {"active": False, "message": runtime.message}


@app.post("/api/images/preview")
async def preview_image(photo: UploadFile = File(...)) -> Response:
    image_data, content_type, is_raw = await read_photo_upload(photo)
    if not is_raw:
        return Response(content=image_data, media_type=content_type)
    try:
        preview, preview_type = prepare_image_for_model(image_data, photo.filename, content_type)
    except ImagePreparationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(content=preview, media_type=preview_type)


def _parse_allowed_keys(raw: str) -> tuple[str, ...] | None:
    """Read the "allowed for the model" set the web UI sends with a suggestion request.

    Empty input means the user did not narrow anything, so the registry default is
    used: every control except the version sensitive (experimental) ones. A non
    empty list is intersected with the registry, so unknown names are ignored and
    asking for no usable key at all is a client error.
    """
    text = raw.strip()
    if not text:
        return None

    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]
    if isinstance(payload, str):
        payload = [payload]
    if not isinstance(payload, list) or not all(isinstance(name, str) for name in payload):
        raise ValueError("allowed_keys 需要是参数名数组")

    keys = model_keys_for(payload)
    if not keys:
        raise ValueError("请至少允许一个调色参数参与 AI 调整")
    return keys


@app.post("/api/suggestions")
async def suggest(
    prompt: str = Form(""),
    photo: UploadFile = File(...),
    allowed_keys: str = Form(""),
    reference: UploadFile | None = File(None),
) -> dict[str, object]:
    if not runtime.active:
        raise HTTPException(status_code=409, detail="请先在模型设置中启动模型")
    image_data, content_type, is_raw = await read_photo_upload(photo)

    try:
        parsed_keys = _parse_allowed_keys(allowed_keys)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    # 参考图模式：把用户上传的第二张图作为"参考风格"，只借用它的调色氛围，不照搬内容。
    # The upload is stored together with its own file object, so the code below never has
    # to reach back through the optional `reference` parameter to read its filename.
    reference_upload: tuple[UploadFile, bytes, str, bool] | None = None
    if reference is not None and (reference.filename or ""):
        reference_file = reference
        ref_data, ref_type, ref_is_raw = await read_photo_upload(reference_file)
        reference_upload = (reference_file, ref_data, ref_type, ref_is_raw)

    try:
        image, mime_type = prepare_image_for_model(
            image_data,
            photo.filename if is_raw else None,
            content_type,
        )
        reference_image = reference_mime_type = None
        if reference_upload is not None:
            reference_file, ref_data, ref_type, ref_is_raw = reference_upload
            reference_image, reference_mime_type = prepare_image_for_model(
                ref_data,
                reference_file.filename if ref_is_raw else None,
                ref_type,
            )
        result = await generate_suggestion(
            prompt,
            image,
            mime_type,
            parsed_keys,
            reference_image=reference_image,
            reference_mime_type=reference_mime_type,
        )
    except ImagePreparationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"生成建议失败: {error}") from error

    return result
