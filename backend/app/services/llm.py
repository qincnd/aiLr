import base64
import json
import re
from typing import Any

import httpx

from app.config import OLLAMA_CONTEXT_SIZE, load_model_config
from app.settings import DEVELOP_RANGES, validate_develop_settings


SYSTEM_PROMPT = """You are a Lightroom Classic photo-coloring assistant. Inspect the attached photo and user brief. Return only a JSON object with keys summary (short string) and settings (object). Use only these Lightroom develop keys: Exposure2012, Contrast2012, Highlights2012, Shadows2012, Whites2012, Blacks2012, Temperature, Tint, Vibrance, Saturation, Texture, Clarity2012, Dehaze. Every setting must use the Lightroom value scale. Temperature is an absolute white-balance value in Kelvin from 2000 to 50000, not a normalized 0-to-1 value or a relative adjustment; natural daylight is usually around 5000 to 6500 K. Do not output values such as 0.5 for Temperature. Other settings must stay within their Lightroom numeric ranges. Do not invent keys. Prefer restrained, reversible edits."""


def _ollama_response_schema() -> dict[str, Any]:
    setting_properties: dict[str, dict[str, int | float | str]] = {}
    for name, (minimum, maximum) in DEVELOP_RANGES.items():
        definition: dict[str, int | float | str] = {
            "type": "number",
            "minimum": minimum,
            "maximum": maximum,
        }
        if name == "Temperature":
            definition["description"] = "Absolute color temperature in Kelvin, never a normalized value."
        setting_properties[name] = definition

    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "settings": {
                "type": "object",
                "properties": setting_properties,
                "additionalProperties": False,
            },
        },
        "required": ["summary", "settings"],
        "additionalProperties": False,
    }


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


def _raise_for_model_response(response: httpx.Response, provider: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        try:
            payload = response.json()
            detail = payload.get("error", response.text) if isinstance(payload, dict) else response.text
        except ValueError:
            detail = response.text
        raise RuntimeError(f"{provider} 返回 HTTP {response.status_code}: {str(detail)[:500]}") from error


async def generate_suggestion(prompt: str, image: bytes, mime_type: str) -> dict[str, Any]:
    model_config = load_model_config()
    encoded_image = base64.b64encode(image).decode("ascii")
    user_text = f"User direction: {prompt.strip() or 'Create a balanced natural edit.'}"

    async with httpx.AsyncClient(timeout=120) as client:
        if model_config.provider == "ollama":
            response = await client.post(
                f"{model_config.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": model_config.model_name,
                    "stream": False,
                    "format": _ollama_response_schema(),
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text, "images": [encoded_image]},
                    ],
                    "options": {"temperature": 0.2, "num_ctx": OLLAMA_CONTEXT_SIZE},
                },
            )
            _raise_for_model_response(response, "Ollama")
            text = response.json()["message"]["content"]
        else:
            if not model_config.api_key:
                raise RuntimeError("请先在模型设置中填写云端 API Key")
            response = await client.post(
                f"{model_config.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {model_config.api_key}"},
                json={
                    "model": model_config.model_name,
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
            _raise_for_model_response(response, "云端模型")
            text = response.json()["choices"][0]["message"]["content"]

    return _parse_suggestion(text)
