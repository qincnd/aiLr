import base64
import json
import re
from typing import Any, Iterable

import httpx

from app.config import ModelConfig, OLLAMA_CONTEXT_SIZE, load_model_config
from app.presets import PARAMS_HINT_LABEL
from app.settings import (
    CORE_SETTING_KEYS,
    DEVELOP_CONTROLS,
    DEVELOP_GROUPS,
    DevelopControl,
    VIGNETTE_SAFE_WINDOW,
    VIGNETTE_SETTING_KEYS,
    model_keys_for,
    review_develop_settings,
    tame_vignette_artifacts,
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
7. This workspace writes global develop sliders only: no masks, no local adjustments, no crop and no healing brush exist, so change only what these sliders can change and describe the edit in summary instead of promising local work."""

SYSTEM_PROMPT_CONTRACT = """Answer with one JSON object holding summary and settings. summary: one or two sentences naming your diagnosis and the look you are going for, written for the photographer in Simplified Chinese (简体中文). settings: keys must come from the allowed lists below, every value uses the Lightroom scale, and any key you leave out keeps its Lightroom default. Never invent a key and never emit a key outside those lists. Numbers must sit inside the range shown for their key in those lists (a numeric key printed without a bracket takes the Lightroom default scale of -100 to 100) and enum keys must use one of their listed choices: Lightroom rejects anything outside them, and an answer that overflows is sent straight back to you for another attempt, so keep the first answer legal. Omit any key whose value is its Lightroom default or that makes no visible change: padding the answer with do-nothing values is as wrong as answering with a single slider. Temperature is an absolute white-balance value in Kelvin from 2000 to 50000, not a normalized 0-to-1 value or a relative adjustment; natural daylight is usually around 5000 to 6500 K. Do not output values such as 0.5 for Temperature."""

# Fixed part of every prompt: role, working method, output contract. The allowed
# key lists are appended per request by _build_system_prompt.
SYSTEM_PROMPT_HEAD = "\n".join((SYSTEM_PROMPT_ROLE, SYSTEM_PROMPT_CRAFT, SYSTEM_PROMPT_CONTRACT))

# Appended when the brief carries the 「参考参数」 block that backend/app/presets.py composes
# from a preset's params_hint (for example 「高光(Highlights2012) +5~+10；整体强度 0.8」).
# Ranges copied from a look are a starting point, not a contract - the photo in front of the
# model decides - but silently ignoring the whole block is just as wrong as copying it, so the
# rule spells out both halves: not mandatory, and not to be dropped either.
REFERENCE_PARAMS_RULE = (
    "The brief may end with a reference block (参考参数, reference parameters) that lists the "
    "approximate slider ranges and the overall strength of the look it was copied from, for "
    "example \"高光(Highlights2012) +5~+10\" or \"整体强度 0.8\". Treat it as a suggestion from a "
    "photographer who knows that look, not as a specification: nothing in it is mandatory, no "
    "value is sent back to you for stepping outside it, and 整体强度 is only how strong the whole "
    "look is meant to read - 1 is full strength, and photograph, subject and light outrank it. "
    "Read the photo in front of you first: when the frame is already bright, warm or cool, or "
    "needs different colour work than the look implies, follow the photo and say why in summary. "
    "Do not ignore the block either - when the photo allows it, keep your values inside the "
    "suggested ranges, use the keys it names instead of an equivalent slider, weigh the whole "
    "block rather than one line of it, and never answer the same value for every hint. A hint "
    "for a key that is not in your allowed lists is simply skipped, and the output contract above "
    "wins if the two ever disagree."
)

# Appended when the request carries a reference photo (the web UI "参考图" input mode): the
# target photo stays the photo being graded, and the reference only supplies the look. The
# rule spells out both halves of that split - read the reference for style, grade the target -
# so the model neither copies the reference's content nor ignores the look it was handed.
REFERENCE_IMAGE_RULE = (
    "You are given two photos in this turn: the first is the TARGET photo to be graded, the "
    "second is a REFERENCE photo whose colour and mood the photographer wants to borrow. Read "
    "the reference for its look only - overall warmth or coolness, contrast and black point, "
    "shadow and highlight tint, which hues are pushed or held back, saturation, grain or a "
    "matte finish - and describe that look in summary. Never copy the reference's subject, "
    "framing or content onto the target: a bright sky reference does not mean every photo gets "
    "a bright sky. Make the target look as if the reference's grade had been applied to it, "
    "keeping the target's own subject believable, and when the brief and the reference seem to "
    "ask for different looks, follow the brief and say so in summary."
)

# Only appended when the request unlocked the mask (the web UI mask switch): the Post Crop
# vignette is the one effect that can draw a shape the eye reads as a region of the photo,
# so the model is told the window that keeps it soft instead of a circle - and told that the
# keys are global effects, which the fixed prompt deliberately does not mention while the mask
# is off (a key outside the allowed set must stay invisible to the model).
# The numbers are read out of settings.VIGNETTE_SAFE_WINDOW, the same window
# review_develop_settings() reports and tame_vignette_artifacts() clamps to, so the prompt can
# never promise a window the guard would reject.
_VIGNETTE_AMOUNT_LOW, _VIGNETTE_AMOUNT_HIGH = VIGNETTE_SAFE_WINDOW["PostCropVignetteAmount"]
_VIGNETTE_FEATHER_MIN = VIGNETTE_SAFE_WINDOW["PostCropVignetteFeather"][0]
_VIGNETTE_MIDPOINT_LOW, _VIGNETTE_MIDPOINT_HIGH = VIGNETTE_SAFE_WINDOW["PostCropVignetteMidpoint"]
_VIGNETTE_ROUNDNESS_MAX = VIGNETTE_SAFE_WINDOW["PostCropVignetteRoundness"][1]

VIGNETTE_RULE = (
    "The Post Crop vignette is unlocked for this answer: use it when the brief asks for a "
    "vignette - it is a global effect applied to the whole frame, not a local adjustment - and "
    "keep it soft and frame-hugging: "
    f"PostCropVignetteAmount inside {_VIGNETTE_AMOUNT_LOW:g} to {_VIGNETTE_AMOUNT_HIGH:g}, "
    f"PostCropVignetteFeather at {_VIGNETTE_FEATHER_MIN:g} or more, "
    f"PostCropVignetteMidpoint between {_VIGNETTE_MIDPOINT_LOW:g} and {_VIGNETTE_MIDPOINT_HIGH:g}, and "
    f"PostCropVignetteRoundness at {_VIGNETTE_ROUNDNESS_MAX:g} or below with Style 1 or 2. "
    "Anything more draws a hard-edged circle across the frame, which reads as a broken mask "
    "stuck on the photo, and such an answer is sent straight back to you."
)

# Extra turns the model gets to bring an overflowing answer back into the Lightroom
# ranges before the offending values are dropped and reported to the user.
MAX_PARAMETER_REPAIR_ROUNDS = 2
# Cap the correction list so a stubborn answer cannot blow up the context.
MAX_REPAIR_ISSUES = 12


def _choice_list(control: DevelopControl) -> str:
    return " / ".join(str(choice) for choice, _label in control.choices)


# The slider bounds every Lightroom numeric control defaults to (settings._slider). A numeric
# key that keeps this range is printed bare; one that does not (Temperature, Exposure2012,
# SharpenRadius, the defringe amounts ...) carries its real bounds in brackets. That keeps the
# output contract honest - it promises a range per numeric key - at the cost of one bracket
# per unusual key instead of one for each of the 66 numeric controls, which matters because
# every request pays for this prompt and the cloud path has no JSON schema to fall back on.
DEFAULT_NUMBER_RANGE = (-100.0, 100.0)


def _number_label(control: DevelopControl) -> str:
    """A numeric key, with its slider range only when it is not the default scale."""
    if (control.minimum, control.maximum) == DEFAULT_NUMBER_RANGE:
        return control.key
    return f"{control.key} {control.minimum:g}..{control.maximum:g}"


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
    "PostCropVignetteStyle": "how the vignette is drawn; change it together with the vignette amount, pick 1 (highlight priority) or 2 (colour priority) and never 3, whose flat overlay reads as a mask",
    "PerspectiveUpright": "automatic perspective correction when the brief asks for straight verticals",
}


def _group_title(group_id: str) -> str:
    return GROUP_TITLES.get(group_id, GROUP_LABELS.get(group_id, group_id))


def _group_trigger(group_id: str) -> str:
    return GROUP_TRIGGERS.get(group_id, "see the matching Lightroom panel")


def _trigger_for(key: str) -> str:
    """Panel trigger of one key, used when a switch or enum has no bespoke hint."""
    return _group_trigger(DEVELOP_CONTROLS[key].group)


def _build_system_prompt(
    keys: Iterable[str] | None = None,
    reference_params: bool = False,
    reference_image: bool = False,
) -> str:
    """Prompt for one request; it only mentions the keys the caller allowed.

    The web UI lets the user switch single controls off, so every request can see a
    different key set: the default is every non experimental control, numbers,
    enums, boolean switches and point curves included. Keys the model is easy to
    overlook are grouped per Lightroom panel together with the trigger that makes
    the group worth using, because a flat list of them was ignored in practice.
    reference_params is on when the brief carries a preset's 「参考参数」 block, so the
    model learns how to weigh those suggested ranges before it reads them.
    reference_image is on when the request also carries a second (reference) photo, so
    the model knows to read its look without copying its content.
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
            f"Return {low} to {high} settings (fewer only when the photo is already that close "
            "to the brief, and say why in summary), covering every aspect the photo or the brief "
            "actually needs - light, white balance, contrast, colour, a curve, detail, effects "
            "- instead of stopping at one or two sliders."
        )
    sections = [SYSTEM_PROMPT_HEAD, scope]
    if reference_image:
        # A reference photo came with the request, so say how to use it before the key lists.
        sections.append(REFERENCE_IMAGE_RULE)
    if reference_params:
        # A preset filled the brief, so say how to weigh its suggested ranges before the model
        # reads them: not mandatory, judged against the photo, and not to be dropped either.
        sections.append(REFERENCE_PARAMS_RULE)
    if set(VIGNETTE_SETTING_KEYS) & allowed_set:
        # The mask switch is on for this request, so the vignette rule applies.
        sections.append(VIGNETTE_RULE)
    if preferred:
        sections.append(
            "Base sliders used by nearly every edit: "
            f"{', '.join(_number_label(DEVELOP_CONTROLS[key]) for key in preferred)}. "
            "Include the ones this photo and brief need and skip the rest."
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
                    f"({_group_trigger(group_id)}): "
                    f"{', '.join(_number_label(DEVELOP_CONTROLS[key]) for key in group_keys)}"
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


def _read_suggestion(
    text: str, keys: Iterable[str] | None = None
) -> tuple[str, dict[str, Any]]:
    """Read one model answer into its summary and the allowed values it proposed.

    Values are not checked against the registry here: the caller reviews them so it
    can hand the overflow list back to the model before it answers the request.
    """
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

    return str(payload.get("summary", "已生成调色建议"))[:500], proposed


def _parse_suggestion(text: str, keys: Iterable[str] | None = None) -> dict[str, Any]:
    """Strict single-answer parse: anything Lightroom would refuse raises."""
    summary, proposed = _read_suggestion(text, keys)
    return {"summary": summary, "settings": validate_develop_settings(proposed)}


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


def _user_message(
    provider: str, text: str, encoded_images: list[str] | None, mime_types: list[str] | str
) -> dict[str, Any]:
    """One user turn; Ollama carries images inside the message, OpenAI uses parts.

    ``encoded_images`` is an ordered list so a reference photo can ride along with the
    target: the order (target first, reference second) is what the reference-image rule
    in the system prompt refers to. ``mime_types`` matches the list positionally, but a
    single string is accepted for the common one-image case.
    """
    images = encoded_images or []
    if isinstance(mime_types, str):
        types = [mime_types] * len(images)
    else:
        types = list(mime_types)

    if provider == "ollama":
        message: dict[str, Any] = {"role": "user", "content": text}
        if images:
            message["images"] = list(images)
        return message

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for index, encoded_image in enumerate(images):
        mime_type = types[index] if index < len(types) else types[-1]
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"}}
        )
    return {"role": "user", "content": content}


async def _request_answer(
    client: httpx.AsyncClient,
    model_config: ModelConfig,
    system_prompt: str,
    messages: list[dict[str, Any]],
    keys: Iterable[str],
) -> str:
    """One chat completion request; returns the raw answer text of the model."""
    if model_config.provider == "ollama":
        response = await client.post(
            f"{model_config.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": model_config.model_name,
                "stream": False,
                "format": _ollama_response_schema(keys),
                "messages": [{"role": "system", "content": system_prompt}, *messages],
                "options": {"temperature": 0.2, "num_ctx": OLLAMA_CONTEXT_SIZE},
            },
        )
        _raise_for_model_response(response, "Ollama")
        return response.json()["message"]["content"]

    if not model_config.api_key:
        raise RuntimeError("请先在模型设置中填写云端 API Key")
    response = await client.post(
        f"{model_config.openai_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {model_config.api_key}"},
        json={
            "model": model_config.model_name,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system_prompt}, *messages],
            "temperature": 0.2,
        },
    )
    _raise_for_model_response(response, "云端模型")
    return response.json()["choices"][0]["message"]["content"]


def _repair_instruction(problems: list[str]) -> str:
    """Correction turn: name every value the model has to fix or drop.

    Two kinds of problem end up here: values Lightroom refuses, and values it accepts
    that would render as an artifact such as a hard-edged vignette circle. The wording
    covers both without claiming the host rejected something it did not.
    """
    listed = problems[:MAX_REPAIR_ISSUES]
    bullets = "\n".join(f"- {problem}" for problem in listed)
    if len(problems) > len(listed):
        bullets += f"\n- ...and {len(problems) - len(listed)} more of the same kind."
    return (
        "Your last answer breaks these rules (each item below names a parameter key with the "
        "value and the limit it broke, in Chinese - the key names and the numbers are what "
        "matter):\n"
        f"{bullets}\n"
        "Answer once more with one complete JSON object holding summary and settings: fix every "
        "item above - keep that value inside the range or choice list it was given, and follow the "
        "note when the item is about the vignette drawing a visible circle - or leave that key out "
        "entirely, keep every other key as it was, and never add a key outside the allowed lists."
    )


def _overflow_report(
    attempts: int,
    repair_rounds: int,
    first_problems: list[str],
    problems: list[str],
    dropped_keys: list[str],
    adjustments: list[str],
) -> dict[str, Any] | None:
    """What the web UI tells the user about repaired, dropped or tamed parameters."""
    if not first_problems and not adjustments:
        return None

    if problems:
        if dropped_keys:
            tail = f"已丢弃这些参数（{'、'.join(dropped_keys)}）"
        else:
            tail = "已把不合规的参数收敛到安全取值"
        notice = (
            f"模型首次回答有 {len(first_problems)} 项参数不合规，回传修正 {repair_rounds} 次后仍有 "
            f"{len(problems)} 项不合规，{tail}，"
            "写入 LrC 的值都保证在范围内且不会画出硬边圆形。"
        )
    elif first_problems:
        notice = (
            f"模型首次回答有 {len(first_problems)} 项参数不合规，已把溢出清单回传并修正 "
            f"{repair_rounds} 次，全部参数现已落在允许范围内。"
        )
    else:
        notice = "模型的暗角参数会在画面上画出一圈硬边圆形（看起来像蒙版），已按防伪蒙版规则收敛后才写入 LrC。"

    if adjustments:
        notice = f"{notice} 收敛明细：{'；'.join(adjustments)}。"

    return {
        "attempts": attempts,
        "rounds": repair_rounds,
        "repaired": not problems,
        "resolved": [problem for problem in first_problems if problem not in problems],
        "unresolved": list(problems),
        "dropped_keys": list(dropped_keys),
        "adjusted": list(adjustments),
        "notice": notice,
    }



async def generate_suggestion(
    prompt: str,
    image: bytes,
    mime_type: str,
    allowed_keys: Iterable[str] | None = None,
    reference_image: bytes | None = None,
    reference_mime_type: str | None = None,
) -> dict[str, Any]:
    """One suggestion, repaired at most MAX_PARAMETER_REPAIR_ROUNDS times.

    The model is asked to answer inside the Lightroom ranges and the JSON schema
    spells them out, but a vision model can still overflow a slider, invent an enum
    name or mistype a value. Instead of failing the whole request, the values
    Lightroom would refuse are sent back to the model as a correction turn; whatever
    still overflows after the last round is dropped, and the caller is told which
    parameters were fixed or dropped so it can pass that on to the user.

    The same rounds also carry the vignette guard: legal vignette values that would
    render as a hard-edged circle (see VIGNETTE_SAFE_WINDOW in settings.py) are sent
    back as well, and tame_vignette_artifacts() clamps whatever survives, so the photo
    can never end up with a circular artifact that looks like a mask.

    When ``reference_image`` is given it is sent after the target photo and the system
    prompt gains the reference-image rule, so the model borrows the reference's look
    without copying its content.
    """
    model_config = load_model_config()
    keys = model_keys_for(allowed_keys)
    if not keys:
        raise RuntimeError("请至少允许一个调色参数参与 AI 调整")
    has_reference = reference_image is not None
    system_prompt = _build_system_prompt(
        keys,
        reference_params=PARAMS_HINT_LABEL in prompt,
        reference_image=has_reference,
    )
    images = [base64.b64encode(image).decode("ascii")]
    mime_types = [mime_type]
    if reference_image is not None:
        images.append(base64.b64encode(reference_image).decode("ascii"))
        mime_types.append(reference_mime_type or mime_type)
    if has_reference:
        # Name the two photos so the model cannot mix up which one it is grading.
        user_text = (
            f"User direction: {prompt.strip() or 'Create a balanced natural edit.'}\n"
            "The first image is the target photo to grade; the second image is the reference "
            "photo whose colour and mood to borrow."
        )
    else:
        user_text = f"User direction: {prompt.strip() or 'Create a balanced natural edit.'}"
    messages = [_user_message(model_config.provider, user_text, images, mime_types)]

    attempts = 0
    repair_rounds = 0
    first_problems: list[str] = []
    summary = ""
    accepted: dict[str, Any] = {}
    problems: list[str] = []
    dropped_keys: list[str] = []

    async with httpx.AsyncClient(timeout=120) as client:
        for round_index in range(MAX_PARAMETER_REPAIR_ROUNDS + 1):
            attempts += 1
            answer = await _request_answer(client, model_config, system_prompt, messages, keys)
            try:
                round_summary, proposed = _read_suggestion(answer, keys)
            except ValueError:
                if round_index == 0:
                    raise
                # A correction turn that answers with garbage must not throw away the
                # part of the previous answer that was already usable.
                break
            summary = round_summary
            accepted, problems = review_develop_settings(proposed, guard_vignette=True)
            dropped_keys = [name for name in proposed if name not in accepted]
            if not problems:
                dropped_keys = []
                break
            if round_index == 0:
                first_problems = list(problems)
            if round_index >= MAX_PARAMETER_REPAIR_ROUNDS:
                # No further attempt left: the overflow list is only reported now.
                break
            repair_rounds += 1
            messages.append({"role": "assistant", "content": answer})
            messages.append(
                _user_message(model_config.provider, _repair_instruction(problems), None, mime_type)
            )

    if not accepted:
        raise ValueError(
            f"模型返回的参数全部越界，回传修正 {repair_rounds} 次后仍未通过校验："
            + "；".join(problems[:MAX_REPAIR_ISSUES])
        )

    # Deterministic net behind the repair rounds: whatever vignette value the model
    # insisted on is pulled into the window that cannot draw a hard-edged circle.
    accepted, vignette_adjustments = tame_vignette_artifacts(accepted)

    result: dict[str, Any] = {"summary": summary, "settings": accepted}
    overflow = _overflow_report(
        attempts, repair_rounds, first_problems, problems, dropped_keys, vignette_adjustments
    )
    if overflow:
        result["overflow"] = overflow
    return result
