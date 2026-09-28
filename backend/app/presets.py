"""调色预设（创作说明模板）的读写入口。

预设文案独立存放在项目根目录的 color_presets.json：网页抽屉读取它，网页里新建与删除的
预设也写回同一个文件，所以新增调色方向不必改动前端代码，直接编辑 JSON 或交给 git 管理即可。

文件结构（version 2.0）::

    {
      "version": "2.0",
      "presets": [
        {
          "id": "film-portrait",
          "name": "胶片暖调人像",
          "tags": ["人像", "婚礼", "日常"],
          "default_intensity": 0.8,
          "summary": "暖调肤色、微微褪色的阴影与轻颗粒，适合日常与婚礼照片。",
          "prompt": {
            "steps": ["高光加暖，阴影稍微抬起并带轻微褪色感"],
            "constraints": ["肤色干净通透带奶油感，避免发黄发橙"],
            "params_hint": {"highlights": "+5~+10", "grain": "10~15"}
          }
        }
      ]
    }

字段约定：
    id                稳定标识；省略时按名称生成，与前一条重复会被拒绝
    name              名称（≤24 字），同名会被拒绝
    tags              分类标签数组（每个 ≤8 字，最多 4 个）；旧写法 tag: "人像" 也接受
    default_intensity 建议的整体力度（0~1 的小数），可省略；省略时创作说明里不出现强度
    summary           一句话描述（≤60 字），留空则取创作说明前 48 字
    prompt            创作说明，两种写法都支持：
                        * 对象：steps（调色步骤）或 text（一整段正文）二选一，另可带
                          constraints（必须守住的约束）与 params_hint（建议的参数区间）；
                          网页表单填了「参数参考」写的就是 {text, params_hint} 这种对象
                        * 字符串：一整段自由文本（与旧结构一致，表单没填参数参考时写的就是这种）
    created_at        网页新建时自动写入

每条预设读出来都会带一句合成的 instruction（创作说明）：steps 与 constraints 合成正文，
params_hint 合成「参考参数（建议，非强制）」一行，用 settings.py 的中文标签与 Lightroom
键名一起标注（例如「高光(Highlights2012) +5~+10」）。网页把 instruction 填进输入框，
用户可继续改写，再由 POST /api/suggestions 原样发给模型；「参考参数只是建议、要按画面实际
判断、但也不能整段忽略」这条规则写在 services/llm.py 的 REFERENCE_PARAMS_RULE 里。
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.settings import DEVELOP_CONTROLS

PRESET_FILE_VERSION = "2.0"
# 纯文本创作说明与合成后的 instruction 共用同一个上限，前端 PROMPT_MAX_LENGTH 与之一致。
PRESET_PROMPT_MAX_LENGTH = 500
PRESET_NAME_MAX_LENGTH = 24
PRESET_TAG_MAX_LENGTH = 8
PRESET_TAG_MAX_COUNT = 4
PRESET_SUMMARY_MAX_LENGTH = 60
PRESET_SUMMARY_FALLBACK_LENGTH = 48
PRESET_STEP_MAX_LENGTH = 80
PRESET_STEPS_MAX_COUNT = 8
PRESET_CONSTRAINT_MAX_LENGTH = 80
PRESET_CONSTRAINTS_MAX_COUNT = 6
PRESET_HINT_MAX_COUNT = 16
PRESET_HINT_KEY_MAX_LENGTH = 24
PRESET_HINT_VALUE_MAX_LENGTH = 32
MAX_PRESETS = 50
DEFAULT_TAG = "自建"

# 合成创作说明时的固定措辞：PARAMS_HINT_LABEL 同时是 llm.py 判断「这次请求带了建议区间」的标记。
PARAMS_HINT_LABEL = "参考参数"
PARAMS_HINT_NOTE = "建议，非强制"
PARAMS_HINT_TITLE = f"{PARAMS_HINT_LABEL}（{PARAMS_HINT_NOTE}）："
CONSTRAINT_LABEL = "约束："
INTENSITY_LABEL = "整体强度"

# params_hint 的键既能写中文短名（「高光」这类直接原样输出），也能写这里的英文短名或
# Lightroom 键名。映射到注册表后，合成的创作说明会同时带上中文标签与键名，模型就不必猜
# 「颗粒」对应哪个滑块；认不出来的键原样保留，不会让整份文件加载失败。
HINT_ALIASES: dict[str, str] = {
    "exposure": "Exposure2012",
    "contrast": "Contrast2012",
    "highlights": "Highlights2012",
    "shadows": "Shadows2012",
    "whites": "Whites2012",
    "blacks": "Blacks2012",
    "texture": "Texture",
    "clarity": "Clarity2012",
    "dehaze": "Dehaze",
    "vibrance": "Vibrance",
    "saturation": "Saturation",
    "temp": "Temperature",
    "temperature": "Temperature",
    "tint": "Tint",
    "grain": "GrainAmount",
    "sharpening": "Sharpness",
    "vignette": "PostCropVignetteAmount",
    "shadow_tint": "ColorGradeShadowHue",
    "midtone_tint": "ColorGradeMidtoneHue",
    "highlight_tint": "ColorGradeHighlightHue",
    "red_sat": "SaturationAdjustmentRed",
    "orange_sat": "SaturationAdjustmentOrange",
    "yellow_sat": "SaturationAdjustmentYellow",
    "green_sat": "SaturationAdjustmentGreen",
    "aqua_sat": "SaturationAdjustmentAqua",
    "blue_sat": "SaturationAdjustmentBlue",
    "purple_sat": "SaturationAdjustmentPurple",
    "magenta_sat": "SaturationAdjustmentMagenta",
    "red_hue": "HueAdjustmentRed",
    "orange_hue": "HueAdjustmentOrange",
    "yellow_hue": "HueAdjustmentYellow",
    "green_hue": "HueAdjustmentGreen",
    "aqua_hue": "HueAdjustmentAqua",
    "blue_hue": "HueAdjustmentBlue",
    "purple_hue": "HueAdjustmentPurple",
    "magenta_hue": "HueAdjustmentMagenta",
    "red_lum": "LuminanceAdjustmentRed",
    "orange_lum": "LuminanceAdjustmentOrange",
    "yellow_lum": "LuminanceAdjustmentYellow",
    "green_lum": "LuminanceAdjustmentGreen",
    "aqua_lum": "LuminanceAdjustmentAqua",
    "blue_lum": "LuminanceAdjustmentBlue",
    "purple_lum": "LuminanceAdjustmentPurple",
    "magenta_lum": "LuminanceAdjustmentMagenta",
    "grain_size": "GrainSize",
    "grain_frequency": "GrainFrequency",
    "sharpen_radius": "SharpenRadius",
    "sharpen_detail": "SharpenDetail",
    "sharpen_masking": "SharpenEdgeMasking",
}

# backend/app/presets.py -> parents[2] 即项目根目录，与 uvicorn 的启动目录无关。
PRESETS_PATH = Path(__file__).resolve().parents[2] / "color_presets.json"


class PresetError(ValueError):
    """预设内容不合法，属于用户输入问题（接口返回 400）。"""


class PresetFileError(PresetError):
    """预设文件缺失或无法解析（接口返回 500）。"""


class PresetPrompt(BaseModel):
    """一条预设的创作说明：对象写法（steps 或 text，另可带 constraints / params_hint）或一整段文本。

    structured 记录它在 JSON 里的形态，写回文件时按原样输出，所以「表单新建的纯文本预设」
    与「手写 / 带参数参考的预设」可以共存，互不改造；steps 为空时正文取 text。
    """

    steps: list[str] = []
    constraints: list[str] = []
    params_hint: dict[str, str] = {}
    text: str = ""
    structured: bool = True


class ColorPreset(BaseModel):
    id: str = ""
    name: str
    tags: list[str] = []
    summary: str = ""
    prompt: PresetPrompt
    # 由 name / prompt / default_intensity 合成的创作说明，前端直接拿它填输入框。
    instruction: str = ""
    default_intensity: float | None = None
    created_at: str = ""


class PresetDraft(BaseModel):
    """网页新建预设时提交的内容：提示词是一整段纯文本，id / created_at / 强度由后端生成。

    params_hint 是表单里「参数参考」那一块（参数名 -> 建议区间），可以整块留空；留空时
    预设按纯文本形态写入，填了就写成 {"text": ..., "params_hint": {...}} 对象。
    """

    name: str = ""
    tag: str = ""
    tags: list[str] | None = None
    summary: str = ""
    prompt: str = ""
    params_hint: dict[str, str] = {}


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_text_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()]


def _hint_value(raw: object) -> str:
    """建议区间既可能写成字符串（"+5~+10"），也可能写成数字（10），统一成字符串。"""
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, (int, float)):
        return f"{float(raw):g}"
    return _clean(raw)


def _hint_label(alias: str) -> str:
    """一个建议参数的写法：认识的中文 / 英文短名补上注册表标签与 Lightroom 键名。"""
    key = HINT_ALIASES.get(alias, alias)
    control = DEVELOP_CONTROLS.get(key)
    return f"{control.label}({key})" if control else alias


def _validate_name(name: str, position: str = "") -> str:
    if not name:
        raise PresetError(f"{position}预设名称不能为空。")
    if len(name) > PRESET_NAME_MAX_LENGTH:
        raise PresetError(f"{position}预设名称最多 {PRESET_NAME_MAX_LENGTH} 个字。")
    return name


def _validate_tags(raw_tags: object, raw_tag: object, position: str = "") -> list[str]:
    """分类标签：新写法 tags 数组优先，旧写法 tag 字符串退化成单元素数组。"""
    tags: list[str] = []
    for candidate in [*_clean_text_list(raw_tags), _clean(raw_tag)]:
        if not candidate:
            continue
        if len(candidate) > PRESET_TAG_MAX_LENGTH:
            raise PresetError(f"{position}分类标签最多 {PRESET_TAG_MAX_LENGTH} 个字。")
        if candidate not in tags:
            tags.append(candidate)
    if len(tags) > PRESET_TAG_MAX_COUNT:
        raise PresetError(f"{position}分类标签最多 {PRESET_TAG_MAX_COUNT} 个。")
    return tags


def _validate_intensity(raw: object, position: str = "") -> float | None:
    """建议的整体力度：省略不写，或 0~1 之间的小数（1 表示完整力度）。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    message = f"{position}default_intensity 需要是 0 到 1 之间的小数。"
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PresetError(message)
    value = float(raw)
    if not 0 < value <= 1:
        raise PresetError(message)
    return value


def _validate_summary(summary: str, instruction: str, position: str = "") -> str:
    if len(summary) > PRESET_SUMMARY_MAX_LENGTH:
        raise PresetError(f"{position}一句话描述最多 {PRESET_SUMMARY_MAX_LENGTH} 个字。")
    return summary or instruction[:PRESET_SUMMARY_FALLBACK_LENGTH]


def _validate_text_prompt(text: str, position: str = "") -> str:
    """纯文本写法：一整段自由发挥的创作说明，网页新建预设走的就是这条。"""
    if not text:
        raise PresetError(f"{position}提示词不能为空。")
    if len(text) < 4:
        raise PresetError(f"{position}提示词至少 4 个字，太短会让模型没有稳定的调色方向。")
    if len(text) > PRESET_PROMPT_MAX_LENGTH:
        raise PresetError(f"{position}提示词最多 {PRESET_PROMPT_MAX_LENGTH} 个字，请精简后再保存。")
    return text


def _validate_hints(raw_hints: object, position: str = "") -> dict[str, str]:
    """params_hint：参数名 -> 建议区间。键名与取值都有长度上限，整块可以省略。"""
    hints: dict[str, str] = {}
    if raw_hints is None:
        return hints
    if not isinstance(raw_hints, dict):
        example = '{"exposure": "+0.3~+0.7"}'
        raise PresetError(f"{position}params_hint 需要是一个对象，例如 {example}。")
    for alias, raw_value in raw_hints.items():
        key = _clean(alias)
        value = _hint_value(raw_value)
        if not key:
            raise PresetError(f"{position}params_hint 的键名不能为空。")
        if len(key) > PRESET_HINT_KEY_MAX_LENGTH:
            raise PresetError(
                f"{position}params_hint 的键名最多 {PRESET_HINT_KEY_MAX_LENGTH} 个字符。"
            )
        if not value:
            raise PresetError(f"{position}params_hint.{key} 的取值不能为空。")
        if len(value) > PRESET_HINT_VALUE_MAX_LENGTH:
            raise PresetError(
                f"{position}params_hint.{key} 的取值最多 {PRESET_HINT_VALUE_MAX_LENGTH} 个字符。"
            )
        hints[key] = value
    if len(hints) > PRESET_HINT_MAX_COUNT:
        raise PresetError(f"{position}params_hint 最多 {PRESET_HINT_MAX_COUNT} 项。")
    return hints


def _validate_prompt(raw: object, position: str = "") -> PresetPrompt:
    """prompt 的两种写法：一整段字符串，或 steps / text + constraints / params_hint 对象。"""
    if raw is None:
        raise PresetError(f"{position}提示词不能为空。")
    if isinstance(raw, str):
        return PresetPrompt(text=_validate_text_prompt(_clean(raw), position), structured=False)
    if not isinstance(raw, dict):
        raise PresetError(f"{position}prompt 需要是一段文字，或一个包含 steps 的对象。")

    steps = _clean_text_list(raw.get("steps"))
    text = _clean(raw.get("text"))
    if steps:
        if len(steps) > PRESET_STEPS_MAX_COUNT:
            raise PresetError(f"{position}prompt.steps 最多 {PRESET_STEPS_MAX_COUNT} 条。")
        if any(len(step) > PRESET_STEP_MAX_LENGTH for step in steps):
            raise PresetError(f"{position}prompt 的每条步骤最多 {PRESET_STEP_MAX_LENGTH} 个字。")
    elif text:
        # 网页表单没有分步输入，正文就是那一段话，与纯文本写法共用同一套长度限制。
        text = _validate_text_prompt(text, position)
    else:
        raise PresetError(
            f"{position}prompt.steps 至少要写 1 条调色步骤，或者写一段 text 作为正文。"
        )

    constraints = _clean_text_list(raw.get("constraints"))
    if len(constraints) > PRESET_CONSTRAINTS_MAX_COUNT:
        raise PresetError(f"{position}prompt.constraints 最多 {PRESET_CONSTRAINTS_MAX_COUNT} 条。")
    if any(len(item) > PRESET_CONSTRAINT_MAX_LENGTH for item in constraints):
        raise PresetError(f"{position}prompt 的每条约束最多 {PRESET_CONSTRAINT_MAX_LENGTH} 个字。")

    hints = _validate_hints(raw.get("params_hint"), position)

    return PresetPrompt(
        steps=steps,
        text=text,
        constraints=constraints,
        params_hint=hints,
        structured=True,
    )


def _compose_instruction(name: str, prompt: PresetPrompt, intensity: float | None) -> str:
    """把一条预设合成一段创作说明：正文（步骤）+ 约束 + 参考参数（建议，非强制）。

    纯文本预设直接用它自己的那段话，所以「不选预设、纯文本调色」这条路径完全不受影响；
    结构化预设才拼步骤与建议区间，建议区间的标题行同时是 llm.py 识别建议块的标记。
    """
    if prompt.steps:
        lines = [f"{name}：" + "；".join(prompt.steps)]
    else:
        # 只有一段正文（纯文本写法，或表单填了参数参考的 text）原样使用，不加名称前缀。
        lines = [prompt.text]
    if prompt.constraints:
        lines.append(CONSTRAINT_LABEL + "；".join(prompt.constraints))

    hints = [f"{_hint_label(alias)} {value}" for alias, value in prompt.params_hint.items()]
    if intensity is not None:
        hints.insert(0, f"{INTENSITY_LABEL} {intensity:g}")
    if hints:
        lines.append(PARAMS_HINT_TITLE + "；".join(hints))
    return "\n".join(lines)


def _validate_instruction(instruction: str, position: str = "") -> str:
    if len(instruction) > PRESET_PROMPT_MAX_LENGTH:
        raise PresetError(
            f"{position}创作说明合成后最多 {PRESET_PROMPT_MAX_LENGTH} 个字"
            f"（当前 {len(instruction)} 个字），请精简 prompt 的正文、constraints 或 params_hint。"
        )
    return instruction


def _document_path(path: Path | None) -> Path:
    return path or PRESETS_PATH


def _slugify(name: str) -> str:
    """把预设名称转成稳定 id：ASCII 名称用连字符连接，中文名称回落到 preset。"""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return slug or "preset"


def _unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def _build_preset(entry: dict[str, Any], position: str) -> ColorPreset:
    """一行 JSON -> 一条预设：所有可编辑字段在这里按同一套限制校验并合成创作说明。"""
    name = _validate_name(_clean(entry.get("name")), position)
    tags = _validate_tags(entry.get("tags"), entry.get("tag"), position)
    intensity = _validate_intensity(entry.get("default_intensity"), position)
    prompt = _validate_prompt(entry.get("prompt"), position)
    instruction = _validate_instruction(_compose_instruction(name, prompt, intensity), position)
    return ColorPreset(
        id=_clean(entry.get("id")) or _slugify(name),
        name=name,
        tags=tags,
        summary=_validate_summary(_clean(entry.get("summary")), instruction, position),
        prompt=prompt,
        instruction=instruction,
        default_intensity=intensity,
        created_at=_clean(entry.get("created_at")),
    )


def _read_document(path: Path) -> tuple[str, list[ColorPreset]]:
    """读取整份文件，返回 (版本号, 预设列表)。"""
    if not path.is_file():
        raise PresetFileError(f"找不到预设文件 {path.name}，请确认它位于项目根目录。")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise PresetFileError(f"无法读取 {path.name}: {error}") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PresetFileError(
            f"{path.name} 不是合法的 JSON（第 {error.lineno} 行）: {error.msg}"
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("presets"), list):
        raise PresetFileError(f'{path.name} 需要一个 presets 数组，例如 {{"presets": [...]}}。')

    version = _clean(payload.get("version")) or PRESET_FILE_VERSION
    presets: list[ColorPreset] = []
    taken: set[str] = set()
    for index, entry in enumerate(payload["presets"]):
        position = f"{path.name} 第 {index + 1} 项"
        if not isinstance(entry, dict):
            raise PresetFileError(f"{position}需要是一个对象。")
        try:
            preset = _build_preset(entry, position)
        except PresetError as error:
            # 文件内容出错属于「这份 JSON 有问题」，接口返回 500；网页提交的内容走 PresetError（400）。
            raise PresetFileError(str(error)) from error
        if preset.id in taken:
            raise PresetFileError(f"{position}的 id「{preset.id}」与前面重复。")
        taken.add(preset.id)
        presets.append(preset)
    return version, presets


def _prompt_payload(prompt: PresetPrompt) -> str | dict[str, object]:
    """按读到的形态写回：纯文本预设不会因为一次新增 / 删除就被改写成对象写法。"""
    if not prompt.structured:
        return prompt.text
    payload: dict[str, object] = {}
    if prompt.steps:
        payload["steps"] = list(prompt.steps)
    else:
        # 表单填了参数参考、正文只有一段话：写作 {"text": ..., "params_hint": {...}}。
        payload["text"] = prompt.text
    if prompt.constraints:
        payload["constraints"] = list(prompt.constraints)
    if prompt.params_hint:
        payload["params_hint"] = dict(prompt.params_hint)
    return payload


def _write_document(
    presets: list[ColorPreset], path: Path, version: str = PRESET_FILE_VERSION
) -> None:
    """整份重写：先写临时文件再替换，避免中途失败留下半截 JSON。"""
    entries: list[dict[str, object]] = []
    for preset in presets:
        entry: dict[str, object] = {
            "id": preset.id,
            "name": preset.name,
            "tags": list(preset.tags),
        }
        if preset.default_intensity is not None:
            entry["default_intensity"] = preset.default_intensity
        entry["summary"] = preset.summary
        entry["prompt"] = _prompt_payload(preset.prompt)
        if preset.created_at:
            entry["created_at"] = preset.created_at
        entries.append(entry)

    # 仓库统一使用 UTF-8 无 BOM + CRLF，手写与程序写入的行尾保持一致。
    text = json.dumps({"version": version, "presets": entries}, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(text, encoding="utf-8", newline="\r\n")
    temporary_path.replace(path)


def params_hint_options() -> list[dict[str, str]]:
    """「参数参考」表单的候选名（短名 + 注册表键名），每个都带一份后端同款标注。

    前端拿它渲染 datalist 与合成预览，标注由 _hint_label() 给出，所以预览里看到的就是
    load_presets() 最终写进创作说明的那一行，不再各写一套映射。
    """
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in [*HINT_ALIASES, *DEVELOP_CONTROLS]:
        if name in seen:
            continue
        seen.add(name)
        options.append({"name": name, "label": _hint_label(name)})
    return options


def load_presets(path: Path | None = None) -> list[ColorPreset]:
    return _read_document(_document_path(path))[1]


def presets_payload(path: Path | None = None) -> dict[str, object]:
    """抽屉需要的完整载荷：预设列表（含合成好的创作说明）+ 版本号 + 实际读取的文件。"""
    target = _document_path(path)
    version, presets = _read_document(target)
    return {
        "version": version,
        "presets": [preset.model_dump() for preset in presets],
        # 新建表单的 datalist 与预览都依赖这份候选表，随预设一起返回，省一次请求。
        "params_hint_options": params_hint_options(),
        "file": target.name,
        "path": str(target),
    }


def create_preset(draft: PresetDraft, path: Path | None = None) -> ColorPreset:
    """追加一条预设并写回文件，返回新建的预设（含生成的 id 与日期）。

    表单只填一段纯文本提示词时，预设按纯文本形态写入，与旧结构一致；表单里填了「参数参考」
    就写成 {"text": ..., "params_hint": {...}} 对象，不去硬拆自由文本。想写分步骤（steps）
    或约束（constraints）预设直接编辑 JSON，几种写法可以共存。
    """
    target = _document_path(path)
    version, presets = _read_document(target)
    if len(presets) >= MAX_PRESETS:
        raise PresetError(f"预设最多 {MAX_PRESETS} 条，请先删除一些再新建。")

    name = _validate_name(_clean(draft.name))
    tags = _validate_tags(draft.tags, draft.tag) or [DEFAULT_TAG]
    text = _validate_text_prompt(_clean(draft.prompt))
    hints = _validate_hints(draft.params_hint)
    prompt = PresetPrompt(text=text, params_hint=hints, structured=bool(hints))
    if any(preset.name == name for preset in presets):
        raise PresetError(f"已经有同名预设「{name}」，换个名字或直接改写现有预设。")

    instruction = _validate_instruction(_compose_instruction(name, prompt, None))
    preset = ColorPreset(
        id=_unique_id(_slugify(name), {item.id for item in presets}),
        name=name,
        tags=tags,
        summary=_validate_summary(_clean(draft.summary), instruction),
        prompt=prompt,
        instruction=instruction,
        default_intensity=None,
        created_at=date.today().isoformat(),
    )
    _write_document([*presets, preset], target, version)
    return preset


def delete_preset(preset_id: str, path: Path | None = None) -> bool:
    """按 id 删除一条预设，返回是否真的删掉了。"""
    target = _document_path(path)
    version, presets = _read_document(target)
    remaining = [preset for preset in presets if preset.id != preset_id]
    if len(remaining) == len(presets):
        return False
    _write_document(remaining, target, version)
    return True


