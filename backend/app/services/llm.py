import base64
import json
import re
from typing import Any, Iterable

import httpx

from app.config import OLLAMA_CONTEXT_SIZE, load_model_config
from app.settings import (
    CORE_SETTING_KEYS,
    DEVELOP_CONTROLS,
    DEVELOP_GROUPS,
    DevelopControl,
    model_keys_for,
    validate_develop_settings,
)


SYSTEM_PROMPT_ROLE = """You are a senior Lightroom Classic colourist: years of editorial, wedding, landscape and product grading behind you. You read a photograph the way a colourist does - light first, then white balance, then contrast and tone shape, then colour, then detail and effects - and you make the smallest set of moves that reaches the look the brief asks for, with every value defensible on the histogram."""

SYSTEM_PROMPT_CRAFT = """How you work this photo:
1. Read it before you touch it: flat or contrasty, warm, cool or green, which colours dominate, soft or harsh light, mushy or noisy detail.
2. Fix the fundamentals first: lift shadows or recover highlights with the dedicated sliders before reaching for Exposure2012, set a black and white point that fills the histogram without clipping, and neutralise a colour cast instead of stacking a correcting white balance on top of an already warm photo.
3. Shape the tone, then place the colour: contrast and tone curves decide the mood, HSL and colour grading place the colour. For one distracting hue, grade that hue instead of desaturating the whole frame.
4. Detail and effects come last and only when the photo needs them: sharpening for soft pixels, noise reduction for high ISO, grain or a vignette when the brief asks for a look.
5. Stay subtle unless the brief demands otherwise: most sliders land inside -30 to 30, exposure inside -1 to 1 EV, and skin, sky and highlight detail have to stay believable. Never let one move cancel another - do not raise Vibrance and Saturation together, do not lift the shadows and raise the black point together.
6. Stop when the look is reached: a single slider is not an answer when the photo needs light, colour and detail work.
7. This workspace writes global develop sliders only: no masks, no local adjustments, no crop and no healing brush exist - the PostCrop vignette keys are global effects and are fine - so change only what these sliders can change and describe the edit in summary instead of promising local work."""

SYSTEM_PROMPT_CONTRACT = """Answer with one JSON object holding summary and settings. summary: one or two sentences naming your diagnosis and the look you are going for, written for the photographer. settings: keys must come from the allowed lists below, every value uses the Lightroom scale, and any key you leave out keeps its Lightroom default. Never invent a key and never emit a key outside those lists. Temperature is an absolute white-balance value in Kelvin from 2000 to 50000, not a normalized 0-to-1 value or a relative adjustment; natural daylight is usually around 5000 to 6500 K. Do not output values such as 0.5 for Temperature."""

# Fixed part of every prompt: role, working method, output contract. The allowed
# key lists are appended per request by _build_system_prompt.
SYSTEM_PROMPT_HEAD = "\n".join((SYSTEM_PROMPT_ROLE, SYSTEM_PROMPT_CRAFT, SYSTEM_PROMPT_CONTRACT))


def _choice_list(control: DevelopControl) -> str:
    return " / ".join(str(choice) for choice, _label in control.choices)


# Panel labels the prompt uses (DEVELOP_GROUPS ids keep the registry authoritative;
# a new group falls back to its Chinese label instead of disappearing).
GROUP_LABELS: dict[str, str] = dict(DEVELOP_GROUPS)

GROUP_TITLES: dict[str, str] = {
    "basic": "Basic",
    "white_balance": "White balance",
    "tone_curve": "Tone curve",
    "hsl": "HSL / colour mixer",
    "color_grade": "Colour grading",
    "split_toning": "Split toning",
    "detail": "Detail",
    "effects": "Effects",
    "lens": "Lens corrections",
    "transform": "Transform",
    "extras": "Extras",
}

# What has to show up in the photo or in the brief before the model should reach
# for that panel. Grouping the keys this way is what makes the model actually use
# them: a flat list of 66 numeric keys was ignored, a panel plus its trigger is not.
GROUP_TRIGGERS: dict[str, str] = {
    "basic": "light and presence, the everyday tonal workhorse",
    "white_balance": "a colour cast, or a brief that names a temperature or a white balance preset",
    "tone_curve": "tone shaping without the base sliders, or a film-like response",
    "hsl": "single colour work: a sky that is too cyan, grass too yellow, skin too orange",
    "color_grade": "cinematic, teal-and-orange or film colouring of shadows, midtones and highlights",
    "split_toning": "classic warm shadows / cool highlights toning",
    "detail": "a soft photo that needs sharpening, or high ISO noise that needs cleaning up",
    "effects": "a vignette, grain or matte look the brief asks for on purpose",
    "lens": "optical corrections; some Lightroom versions reject these",
    "transform": "perspective or keystone correction; some Lightroom versions reject it",
    "extras": "Lightroom's own automatic helpers",
}

# One hint per switch / enum the model may emit; a key without a hint falls back to
# its panel trigger, so adding a control to settings.py never breaks the prompt.
SWITCH_USAGE: dict[str, str] = {
    "ConvertToGrayscale": "true when the brief asks for a black and white photo",
    "AutoTone": "true only when the brief asks Lightroom to normalise the tones by itself",
    "AutoLateralCA": "true when the photo shows colour fringing and the brief wants it removed",
    "LensProfileEnable": "true when the brief asks to apply the lens profile correction",
}

ENUM_USAGE: dict[str, str] = {
    "WhiteBalance": "leave As Shot unless the brief names a preset or the cast is clearly wrong",
    "ToneCurveName": "a strong preset contrast curve, for example when the brief asks for film contrast",
    "PostCropVignetteStyle": "how the vignette is drawn; change it together with the vignette amount",
    "PerspectiveUpright": "automatic perspective correction when the brief asks for straight verticals",
}


def _group_title(group_id: str) -> str:
    return GROUP_TITLES.get(group_id, GROUP_LABELS.get(group_id, group_id))


def _group_trigger(group_id: str) -> str:
    return GROUP_TRIGGERS.get(group_id, "see the matching Lightroom panel")


def _trigger_for(key: str) -> str:
    """Panel trigger of one key, used when a switch or enum has no bespoke hint."""
    return _group_trigger(DEVELOP_CONTROLS[key].group)


def _build_system_prompt(keys: Iterable[str] | None = None) -> str:
    """Prompt for one request; it only mentions the keys the caller allowed.

    The web UI lets the user switch single controls off, so every request can see a
    different key set: the default is every non experimental control, numbers,
    enums, boolean switches and point curves included. Keys the model is easy to
    overlook are grouped per Lightroom panel together with the trigger that makes
    the group worth using, because a flat list of them was ignored in practice.
    """
    allowed = model_keys_for(keys)
    allowed_set = set(allowed)
    preferred = [key for key in CORE_SETTING_KEYS if key in allowed_set]
    extra_numbers = [
        key
        for key in allowed
        if key not in CORE_SETTING_KEYS and DEVELOP_CONTROLS[key].kind == "number"
    ]
    switches = [key for key in allowed if DEVELOP_CONTROLS[key].kind == "bool"]
    enumerations = [key for key in allowed if DEVELOP_CONTROLS[key].kind == "enum"]
    curves = [key for key in allowed if DEVELOP_CONTROLS[key].kind == "curve"]

    # Ask for a range that the allowed set can actually satisfy: a brief that may
    # only touch one key must not be told to return twelve settings.
    low = min(3, len(allowed))
    high = min(12, len(allowed))
    if high <= 1:
        scope = "Return the one setting that matches the brief best."
    else:
        scope = (
            f"Return {low} to {high} settings, covering every aspect the photo or the brief "
            "actually needs - light, white balance, contrast, colour, a curve, detail, effects "
            "- instead of stopping at one or two sliders."
        )
    sections = [SYSTEM_PROMPT_HEAD, scope]
    if preferred:
        sections.append(
            "Base sliders used by nearly every edit: "
            f"{', '.join(preferred)}. Include the ones this photo and brief need and skip the rest."
        )
    if extra_numbers:
        # One bullet per Lightroom panel: a panel plus its trigger tells the model
        # when the group applies, which a flat list of keys did not.
        grouped: dict[str, list[str]] = {}
        for key in extra_numbers:
            grouped.setdefault(DEVELOP_CONTROLS[key].group, []).append(key)
        bullets: list[str] = []
        for group_id, _label in DEVELOP_GROUPS:
            group_keys = grouped.get(group_id)
            if group_keys:
                bullets.append(
                    f"- {_group_title(group_id)} "
                    f"({_group_trigger(group_id)}): {', '.join(group_keys)}"
                )
        sections.append(
            "Further groups - reach for a group as soon as the photo or the brief shows its "
            "trigger, you do not have to be asked for these keys by name:\n" + "\n".join(bullets)
        )
    if switches:
        sections.append(
            "Boolean keys answer true or false and only when the brief asks for that switch:\n"
            + "\n".join(
                f"- {key}: {SWITCH_USAGE.get(key, _trigger_for(key))}." for key in switches
            )
        )
    if enumerations:
        sections.append(
            "Enum keys accept exactly one of the listed values:\n"
            + "\n".join(
                f"- {key} = {_choice_list(DEVELOP_CONTROLS[key])} "
                f"({ENUM_USAGE.get(key, _trigger_for(key))})."
                for key in enumerations
            )
        )
    if curves:
        sections.append(
            "Point curve keys expect the whole curve, not a delta: send a string of \"x,y\" "
            "control points in 0-255 with x ascending, 2 to 16 points, for example "
            f"\"0,0;64,52;128,132;255,255\". Draw one when the brief asks for a film response, "
            "lifted blacks or a soft highlight roll-off: " + ", ".join(curves) + "."
        )
    return "\n".join(sections)


def _control_schema(control: DevelopControl) -> dict[str, Any]:
    """JSON schema fragment for one control, mirroring the value the host accepts."""
    if control.kind == "number":
        definition: dict[str, Any] = {
            "type": "number",
            "minimum": control.minimum,
            "maximum": control.maximum,
        }
        if control.key == "Temperature":
            definition["description"] = "Absolute color temperature in Kelvin, never a normalized value."
        return definition
    if control.kind == "bool":
        return {"type": "boolean"}
    if control.kind == "enum":
        choices = [choice for choice, _label in control.choices]
        numeric = all(isinstance(choice, (int, float)) and not isinstance(choice, bool) for choice in choices)
        if not numeric:
            value_type = "string"
        elif all(isinstance(choice, int) for choice in choices):
            value_type = "integer"
        else:
            value_type = "number"
        return {"type": value_type, "enum": choices}
    return {
        "type": "string",
        "description": (
            "Point curve as \"x,y;x,y\" control points in 0-255, x ascending, 2 to 16 points."
        ),
    }


def _ollama_response_schema(keys: Iterable[str] | None = None) -> dict[str, Any]:
    setting_properties = {
        name: _control_schema(DEVELOP_CONTROLS[name]) for name in model_keys_for(keys)
    }

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


def _parse_suggestion(text: str, keys: Iterable[str] | None = None) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise ValueError("模型未返回有效 JSON 调色建议") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("settings"), dict):
        raise ValueError("模型返回内容缺少 settings 参数对象")

    # The web UI can switch single controls off, so keep only the allowed keys.
    allowed = set(model_keys_for(keys))
    proposed = {name: value for name, value in payload["settings"].items() if name in allowed}
    if not proposed:
        raise ValueError("模型没有返回任何允许调整的参数")

    return {
        "summary": str(payload.get("summary", "已生成调色建议"))[:500],
        "settings": validate_develop_settings(proposed),
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


async def generate_suggestion(
    prompt: str,
    image: bytes,
    mime_type: str,
    allowed_keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    model_config = load_model_config()
    keys = model_keys_for(allowed_keys)
    if not keys:
        raise RuntimeError("请至少允许一个调色参数参与 AI 调整")
    system_prompt = _build_system_prompt(keys)
    encoded_image = base64.b64encode(image).decode("ascii")
    user_text = f"User direction: {prompt.strip() or 'Create a balanced natural edit.'}"

    async with httpx.AsyncClient(timeout=120) as client:
        if model_config.provider == "ollama":
            response = await client.post(
                f"{model_config.ollama_base_url.rstrip('/')}/api/chat",
                json={
                    "model": model_config.model_name,
                    "stream": False,
                    "format": _ollama_response_schema(keys),
                    "messages": [
                        {"role": "system", "content": system_prompt},
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
                        {"role": "system", "content": system_prompt},
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

    return _parse_suggestion(text, keys)
