from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.services.llm import generate_suggestion


app = FastAPI(title="aiLr", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "provider": settings.model_provider,
        "model": settings.model_name,
        "lightroom": "plugin bridge not connected",
    }


@app.post("/api/suggestions")
async def suggest(
    prompt: str = Form(""),
    photo: UploadFile = File(...),
) -> dict[str, object]:
    if not photo.content_type or not photo.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="请上传图片文件")

    image = await photo.read(settings.max_image_mb * 1024 * 1024 + 1)
    if len(image) > settings.max_image_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"图片不能超过 {settings.max_image_mb} MB")

    try:
        result = await generate_suggestion(prompt, image, photo.content_type)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"生成建议失败: {error}") from error

    return result
