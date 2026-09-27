import base64
import json
import re
from typing import Any

import httpx

from app.config import settings
from app.settings import validate_develop_settings


SYSTEM_PROMPT = """You are a Lightroom Classic photo-coloring assistant. Inspect the attached photo and user brief. Return only a JSON object with keys summary (short string) and settings (object). Use only these Lightroom develop keys: Exposure2012, Contrast2012, Highlights2012, Shadows2012, Whites2012, Blacks2012, Temperature, Tint, Vibrance, Saturation, Texture, Clarity2012, Dehaze. Use numeric values within normal Lightroom ranges. Do not invent keys. Prefer restrained, reversible edits."""


def _parse_suggestion(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise ValueError("模型未返回有效 JSON 调色建议") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("settings"), dict):
        raise ValueError("模型返回内容缺少 settings 参数对象")

    return {
        "summary": str(payload.get("summary", "已生成调色建议"))[:500],
        "settings": validate_develop_settings(payload["settings"]),
    }


async def generate_suggestion(prompt: str, image: bytes, mime_type: str) -> dict[str, Any]:
    encoded_image = base64.b64encode(image).decode("ascii")
    user_text = f"User direction: {prompt.strip() or 'Create a balanced natural edit.'}"

    async with httpx.AsyncClient(timeout=120) as client:
        if settings.model_provider == "ollama":
            response = await client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": settings.model_name,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text, "images": [encoded_image]},
                    ],
                    "options": {"temperature": 0.2},
                },
            )
            response.raise_for_status()
            text = response.json()["message"]["content"]
        else:
            if not settings.openai_api_key:
                raise RuntimeError("云端模型未配置 AILR_OPENAI_API_KEY")
            response = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.model_name,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_text},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{encoded_image}"
                                    },
                                },
                            ],
                        },
                    ],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]

    return _parse_suggestion(text)
