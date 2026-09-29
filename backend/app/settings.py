from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal


DevelopKind = Literal["number", "enum", "bool", "curve"]


@dataclass(frozen=True)
class DevelopControl:
    """One Lightroom Classic develop control exposed by aiLr.

    key           Lightroom XMP (crs) setting name; used on the wire, in the API
                  and in the web UI
    label         Chinese label shown in the browser
    group         Panel id from DEVELOP_GROUPS
    controller    LrDevelopController.setValue()/getValue() parameter name that
                  the Lightroom plug-in hands to the host
    kind          number | enum | bool | curve
    minimum       slider lower bound (kind = number)
    maximum       slider upper bound (kind = number)
    step          slider step (kind = number)
    default       Lightroom default value; also the manual UI default
    choices       (value, label) pairs for kind = enum
    core          keys the model prompt mentions first
    experimental  version sensitive; kept out of the model schema and flagged in
                  the web UI so a host that rejects it cannot break a render
    """

    key: str
    label: str
    group: str
    controller: str
    kind: DevelopKind = "number"
    minimum: float = -100.0
    maximum: float = 100.0
    step: float = 1.0
    default: Any = 0.0
    choices: tuple[tuple[Any, str], ...] = ()
    core: bool = False
    experimental: bool = False
    description: str = ""


# Panel order mirrors the Lightroom Classic develop module.
DEVELOP_GROUPS: tuple[tuple[str, str], ...] = (
    ("basic", "基本"),
    ("white_balance", "白平衡"),
    ("tone_curve", "色调曲线"),
    ("hsl", "混色器"),
    ("color_grade", "颜色分级"),
    ("split_toning", "分离色调"),
    ("detail", "细节"),
    ("effects", "效果"),
    ("lens", "镜头校正"),
    ("transform", "变换"),
    ("extras", "其它"),
)

HSL_COLORS: tuple[tuple[str, str], ...] = (
    ("Red", "红"),
    ("Orange", "橙色"),
    ("Yellow", "黄"),
    ("Green", "绿"),
    ("Aqua", "浅绿色"),
    ("Blue", "蓝"),
    ("Purple", "紫色"),
    ("Magenta", "洋红"),
)

CURVE_MAX_POINTS = 16

# --- 「伪蒙版」护栏 -----------------------------------------------------------
#
# The Post Crop vignette is the only control that can draw something the eye reads as
# a region of the photo: a positive roundness turns the vignette from an ellipse that
# hugs the frame into a circle inside it, and a feather near zero leaves a hard edge,
# so the two together look like a circular mask pasted onto the photo instead of a
# vignette. The model likes to push exactly those sliders to their ends, so model
# answers are additionally held to the window below. Values the user drags by hand or
# sends through the Lightroom bridge never take this path, so deliberate circle
# vignettes stay possible.
VIGNETTE_SAFE_WINDOW: dict[str, tuple[float, float]] = {
    "PostCropVignetteAmount": (-60.0, 60.0),
    "PostCropVignetteMidpoint": (20.0, 80.0),
    "PostCropVignetteFeather": (25.0, 100.0),
    "PostCropVignetteRoundness": (-100.0, 25.0),
}
VIGNETTE_WINDOW_REASONS: dict[str, str] = {
    "PostCropVignetteAmount": "数量拉到极端时不再收束视线，而是变成一圈明显的光晕或黑圈",
    "PostCropVignetteMidpoint": "中点太靠边会让暗角缩成画面中央的一小圈",
    "PostCropVignetteFeather": "羽化为 0 会留下硬边，看起来像贴上去的一块蒙版",
    "PostCropVignetteRoundness": "圆度为正会把暗角画成画面里的圆，横构图两侧会露出一圈弧线",
}
# 绘制叠加把暗角铺成一层纯色，看着最像一块蒙版。
VIGNETTE_OVERLAY_STYLE = 3
# 强度低于这个值时暗角在画面上几乎看不出来，护栏不必介入。
VIGNETTE_VISIBLE_AMOUNT = 5.0


def _slider(
    key: str,
    label: str,
    group: str,
    minimum: float,
    maximum: float,
    default: float,
    step: float = 1.0,
    controller: str = "",
    core: bool = False,
    experimental: bool = False,
) -> DevelopControl:
    # Sliders carry no description: the numeric ones are self-explanatory in the web UI,
    # so the few controls that need an explanation use DevelopControl directly.
    return DevelopControl(
        key=key,
        label=label,
        group=group,
        controller=controller or key,
        minimum=minimum,
        maximum=maximum,
        step=step,
        default=default,
        core=core,
        experimental=experimental,
    )


def _controls() -> list[DevelopControl]:
    controls: list[DevelopControl] = [
        # --- 基本 ---------------------------------------------------------
        _slider("Exposure2012", "曝光", "basic", -5, 5, 0, 0.05, "Exposure", core=True),
        _slider("Contrast2012", "对比度", "basic", -100, 100, 0, 1, "Contrast", core=True),
        _slider("Highlights2012", "高光", "basic", -100, 100, 0, 1, "Highlights", core=True),
        _slider("Shadows2012", "阴影", "basic", -100, 100, 0, 1, "Shadows", core=True),
        _slider("Whites2012", "白色色阶", "basic", -100, 100, 0, 1, "Whites", core=True),
        _slider("Blacks2012", "黑色色阶", "basic", -100, 100, 0, 1, "Blacks", core=True),
        _slider("Texture", "纹理", "basic", -100, 100, 0, 1, controller="Texture", core=True),
        _slider("Clarity2012", "清晰度", "basic", -100, 100, 0, 1, "Clarity", core=True),
        _slider("Dehaze", "去朦胧", "basic", -100, 100, 0, 1, controller="Dehaze", core=True),
        _slider("Vibrance", "自然饱和度", "basic", -100, 100, 0, 1, controller="Vibrance", core=True),
        _slider("Saturation", "饱和度", "basic", -100, 100, 0, 1, controller="Saturation", core=True),
        DevelopControl(
            key="ConvertToGrayscale",
            label="转换为黑白",
            group="basic",
            controller="ConvertToGrayscale",
            kind="bool",
            default=False,
            description="打开后照片按黑白处理，色彩滑块仍参与影调计算。",
        ),
        # --- 白平衡 -------------------------------------------------------
        _slider("Temperature", "色温", "white_balance", 2000, 50000, 5500, 50, "Temperature", core=True),
        _slider("Tint", "色调", "white_balance", -150, 150, 0, 1, "Tint", core=True),
        DevelopControl(
            key="WhiteBalance",
            label="白平衡预设",
            group="white_balance",
            controller="WhiteBalance",
            kind="enum",
            default="As Shot",
            choices=(
                ("As Shot", "原照设置"),
                ("Auto", "自动"),
                ("Daylight", "日光"),
                ("Cloudy", "阴天"),
                ("Shade", "阴影"),
                ("Tungsten", "白炽灯"),
                ("Fluorescent", "荧光灯"),
                ("Flash", "闪光灯"),
            ),
        ),
        # --- 色调曲线 -----------------------------------------------------
        DevelopControl(
            key="ToneCurveName",
            label="曲线预设",
            group="tone_curve",
            controller="ToneCurveName",
            kind="enum",
            default="Linear",
            choices=(
                ("Linear", "线性"),
                ("Medium Contrast", "中等对比度"),
                ("Strong Contrast", "强对比度"),
                ("Custom", "自定义"),
            ),
        ),
        DevelopControl(
            key="ToneCurvePV2012",
            label="点曲线 RGB",
            group="tone_curve",
            controller="ToneCurvePV2012",
            kind="curve",
            default=[[0, 0], [255, 255]],
            description="2-16 个控制点，写成 0,0;64,52;128,132;255,255。",
        ),
        DevelopControl(key="ToneCurvePV2012Red", label="点曲线 红", group="tone_curve", controller="ToneCurvePV2012Red", kind="curve", default=[[0, 0], [255, 255]]),
        DevelopControl(key="ToneCurvePV2012Green", label="点曲线 绿", group="tone_curve", controller="ToneCurvePV2012Green", kind="curve", default=[[0, 0], [255, 255]]),
        DevelopControl(key="ToneCurvePV2012Blue", label="点曲线 蓝", group="tone_curve", controller="ToneCurvePV2012Blue", kind="curve", default=[[0, 0], [255, 255]]),
        _slider("ParametricShadows", "阴影", "tone_curve", -100, 100, 0, 1, "ParametricShadows"),
        _slider("ParametricDarks", "暗色调", "tone_curve", -100, 100, 0, 1, "ParametricDarks"),
        _slider("ParametricLights", "亮色调", "tone_curve", -100, 100, 0, 1, "ParametricLights"),
        _slider("ParametricHighlights", "高光", "tone_curve", -100, 100, 0, 1, "ParametricHighlights"),
        _slider("ParametricShadowSplit", "阴影分离", "tone_curve", 0, 100, 25, 1, "ParametricShadowSplit"),
        _slider("ParametricMidtoneSplit", "中间调分离", "tone_curve", 0, 100, 50, 1, "ParametricMidtoneSplit"),
        _slider("ParametricHighlightSplit", "高光分离", "tone_curve", 0, 100, 75, 1, "ParametricHighlightSplit"),
    ]

    # --- 混色器：八种颜色 × 色相/饱和度/明亮度 ----------------------------
    for prefix, label in (
        ("HueAdjustment", "色相"),
        ("SaturationAdjustment", "饱和度"),
        ("LuminanceAdjustment", "明亮度"),
    ):
        for color, color_label in HSL_COLORS:
            controls.append(
                _slider(
                    f"{prefix}{color}",
                    f"{label} {color_label}",
                    "hsl",
                    -100,
                    100,
                    0,
                    1,
                    f"{prefix}{color}",
                )
            )

    controls.extend(
        [
            # --- 颜色分级 -------------------------------------------------
            _slider("ColorGradeGlobalHue", "全局 色相", "color_grade", 0, 360, 0),
            _slider("ColorGradeGlobalSat", "全局 饱和度", "color_grade", 0, 100, 0),
            _slider("ColorGradeGlobalLum", "全局 明亮度", "color_grade", -100, 100, 0),
            _slider("ColorGradeShadowHue", "阴影 色相", "color_grade", 0, 360, 0),
            _slider("ColorGradeShadowSat", "阴影 饱和度", "color_grade", 0, 100, 0),
            _slider("ColorGradeShadowLum", "阴影 明亮度", "color_grade", -100, 100, 0),
            _slider("ColorGradeMidtoneHue", "中间调 色相", "color_grade", 0, 360, 0),
            _slider("ColorGradeMidtoneSat", "中间调 饱和度", "color_grade", 0, 100, 0),
            _slider("ColorGradeMidtoneLum", "中间调 明亮度", "color_grade", -100, 100, 0),
            _slider("ColorGradeHighlightHue", "高光 色相", "color_grade", 0, 360, 0),
            _slider("ColorGradeHighlightSat", "高光 饱和度", "color_grade", 0, 100, 0),
            _slider("ColorGradeHighlightLum", "高光 明亮度", "color_grade", -100, 100, 0),
            _slider("ColorGradeBlending", "混合", "color_grade", 0, 100, 50),
            # --- 分离色调 -------------------------------------------------
            _slider("SplitToningShadowHue", "阴影色相", "split_toning", 0, 360, 0),
            _slider("SplitToningShadowSaturation", "阴影饱和度", "split_toning", 0, 100, 0),
            _slider("SplitToningHighlightHue", "高光色相", "split_toning", 0, 360, 0),
            _slider("SplitToningHighlightSaturation", "高光饱和度", "split_toning", 0, 100, 0),
            _slider("SplitToningBalance", "平衡", "split_toning", -100, 100, 0),
            # --- 细节 -----------------------------------------------------
            _slider("Sharpness", "锐化 数量", "detail", 0, 150, 40),
            _slider("SharpenRadius", "锐化 半径", "detail", 0.5, 3, 1, 0.1),
            _slider("SharpenDetail", "锐化 细节", "detail", 0, 100, 25),
            _slider("SharpenEdgeMasking", "锐化 蒙版", "detail", 0, 100, 0),
            _slider("LuminanceSmoothing", "减少杂色 明亮度", "detail", 0, 100, 0),
            _slider("LuminanceDetail", "明亮度细节", "detail", 0, 100, 50),
            _slider("LuminanceContrast", "明亮度对比", "detail", 0, 100, 0),
            _slider("ColorNoiseReduction", "减少杂色 颜色", "detail", 0, 100, 25),
            _slider("ColorNoiseReductionDetail", "颜色细节", "detail", 0, 100, 50),
            _slider("ColorNoiseReductionSmoothness", "颜色平滑度", "detail", 0, 100, 50),
            # --- 效果 -----------------------------------------------------
            _slider("PostCropVignetteAmount", "裁剪后暗角 数量", "effects", -100, 100, 0),
            _slider("PostCropVignetteMidpoint", "暗角 中点", "effects", 0, 100, 50),
            _slider("PostCropVignetteFeather", "暗角 羽化", "effects", 0, 100, 50),
            _slider("PostCropVignetteRoundness", "暗角 圆度", "effects", -100, 100, 0),
            DevelopControl(
                key="PostCropVignetteStyle",
                label="暗角 样式",
                group="effects",
                controller="PostCropVignetteStyle",
                kind="enum",
                default=1,
                choices=((1, "高光优先"), (2, "颜色优先"), (3, "绘制叠加")),
            ),
            _slider("GrainAmount", "颗粒 数量", "effects", 0, 100, 0),
            _slider("GrainSize", "颗粒 大小", "effects", 0, 100, 25),
            _slider("GrainFrequency", "颗粒 粗糙度", "effects", 0, 100, 50),
        ]
    )
    # --- 镜头校正：随镜头配置文件与 LrC 版本变化，标记为实验性 -------------
    controls.extend(
        [
            DevelopControl(key="AutoLateralCA", label="删除色差", group="lens", controller="AutoLateralCA", kind="bool", default=False, experimental=True),
            DevelopControl(key="LensProfileEnable", label="启用配置文件校正", group="lens", controller="LensProfileEnable", kind="bool", default=False, experimental=True),
            _slider("LensManualDistortionAmount", "扭曲度", "lens", -100, 100, 0, 1, "LensManualDistortionAmount", experimental=True),
            _slider("ChromaticAberrationR", "去边 红/青", "lens", -100, 100, 0, 1, "ChromaticAberrationR", experimental=True),
            _slider("ChromaticAberrationB", "去边 蓝/黄", "lens", -100, 100, 0, 1, "ChromaticAberrationB", experimental=True),
            _slider("DefringePurpleAmount", "紫色去边 数量", "lens", 0, 20, 0, 1, "DefringePurpleAmount", experimental=True),
            _slider("DefringePurpleHueLo", "紫色去边 色相低", "lens", 0, 100, 30, 1, "DefringePurpleHueLo", experimental=True),
            _slider("DefringePurpleHueHi", "紫色去边 色相高", "lens", 0, 100, 70, 1, "DefringePurpleHueHi", experimental=True),
            _slider("DefringeGreenAmount", "绿色去边 数量", "lens", 0, 20, 0, 1, "DefringeGreenAmount", experimental=True),
            _slider("DefringeGreenHueLo", "绿色去边 色相低", "lens", 0, 100, 40, 1, "DefringeGreenHueLo", experimental=True),
            _slider("DefringeGreenHueHi", "绿色去边 色相高", "lens", 0, 100, 60, 1, "DefringeGreenHueHi", experimental=True),
            # --- 变换：同样随版本变化，标记为实验性 ----------------------
            DevelopControl(
                key="PerspectiveUpright",
                label="Upright 模式",
                group="transform",
                controller="PerspectiveUpright",
                kind="enum",
                default=0,
                choices=((0, "关闭"), (1, "自动"), (2, "水平"), (3, "垂直"), (4, "完全"), (5, "引导")),
                experimental=True,
            ),
            _slider("PerspectiveVertical", "垂直", "transform", -100, 100, 0, 1, "PerspectiveVertical", experimental=True),
            _slider("PerspectiveHorizontal", "水平", "transform", -100, 100, 0, 1, "PerspectiveHorizontal", experimental=True),
            _slider("PerspectiveRotate", "旋转", "transform", -45, 45, 0, 0.1, "PerspectiveRotate", experimental=True),
            _slider("PerspectiveScale", "缩放", "transform", 50, 150, 100, 1, "PerspectiveScale", experimental=True),
            _slider("PerspectiveX", "X 位移", "transform", -100, 100, 0, 1, "PerspectiveX", experimental=True),
            _slider("PerspectiveY", "Y 位移", "transform", -100, 100, 0, 1, "PerspectiveY", experimental=True),
            _slider("PerspectiveAspect", "长宽比", "transform", -100, 100, 0, 1, "PerspectiveAspect", experimental=True),
            # --- 其它 -----------------------------------------------------
            DevelopControl(
                key="AutoTone",
                label="自动调整色调",
                group="extras",
                controller="AutoTone",
                kind="bool",
                default=False,
                description="打开时 Lightroom 会按自身算法重新计算基础影调。",
            ),
        ]
    )
    return controls


DEVELOP_CONTROLS: dict[str, DevelopControl] = {control.key: control for control in _controls()}

# Numeric ranges kept for callers that only need slider bounds (MCP tools, tests).
DEVELOP_RANGES: dict[str, tuple[float, float]] = {
    control.key: (control.minimum, control.maximum)
    for control in DEVELOP_CONTROLS.values()
    if control.kind == "number"
}

# The five Post Crop vignette keys are the only controls that can draw something the eye
# reads as a region of the photo (see VIGNETTE_SAFE_WINDOW above). They are this
# workspace's "mask": the web UI keeps them switched off behind one 蒙版 master switch
# plus strength presets, so a suggestion only uses them once the user asked for a mask.
VIGNETTE_SETTING_KEYS: tuple[str, ...] = tuple(
    key for key in DEVELOP_CONTROLS if key.startswith("PostCropVignette")
)


def _model_default(control: DevelopControl) -> bool:
    """Default state of the web UI's "allowed for the model" switch of one control."""
    return not control.experimental and control.key not in VIGNETTE_SETTING_KEYS


# Keys the vision model may adjust by default: every control the registry exposes, minus
# the ones whose meaning depends on the Lightroom version or a per-lens profile, and minus
# the vignette, which stays opt in per request through the mask switch (allowed_keys).
# Values keep their natural JSON type, so the model may also answer with enum names,
# boolean switches and "x,y;x,y" point curves.
MODEL_SETTING_KEYS: tuple[str, ...] = tuple(
    control.key for control in DEVELOP_CONTROLS.values() if _model_default(control)
)

CORE_SETTING_KEYS: tuple[str, ...] = tuple(
    control.key for control in DEVELOP_CONTROLS.values() if control.core
)


def model_keys_for(allowed: Iterable[str] | None = None) -> tuple[str, ...]:
    """Registry ordered keys one suggestion request may use.

    ``None`` means the caller did not narrow anything, so the default set is used
    (every non experimental control). An explicit collection is intersected with
    the registry in panel order, so unknown names are ignored and an experimental
    control the user allowed by hand in the web UI is unlocked again.
    """
    if allowed is None:
        return MODEL_SETTING_KEYS
    requested = {name for name in allowed if isinstance(name, str)}
    return tuple(name for name in DEVELOP_CONTROLS if name in requested)


class SettingsValidationError(ValueError):
    pass


def _format_choice(choice: Any) -> str:
    return choice if isinstance(choice, str) else f"{float(choice):g}"


def _validate_number(control: DevelopControl, raw_value: Any) -> float:
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise SettingsValidationError(f"参数 {control.key} 必须是数字")
    value = float(raw_value)
    if not control.minimum <= value <= control.maximum:
        raise SettingsValidationError(
            f"参数 {control.key} 超出范围 [{control.minimum:g}, {control.maximum:g}]: {value:g}"
        )
    return value


def _validate_enum(control: DevelopControl, raw_value: Any) -> Any:
    for choice, _label in control.choices:
        if raw_value == choice:
            return choice
        if isinstance(choice, (int, float)) and not isinstance(choice, bool):
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                if float(raw_value) == float(choice):
                    return choice
    allowed = ", ".join(_format_choice(choice) for choice, _label in control.choices)
    raise SettingsValidationError(f"参数 {control.key} 只接受以下取值: {allowed}")


def _validate_bool(control: DevelopControl, raw_value: Any) -> bool:
    if not isinstance(raw_value, bool):
        raise SettingsValidationError(f"参数 {control.key} 必须是 true 或 false")
    return raw_value


def _curve_points(control: DevelopControl, raw_value: Any) -> list[list[float]]:
    key = control.key
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            raise SettingsValidationError(f"参数 {key} 的曲线不能为空")
        parts: list[Any] = [part for part in text.replace(";", " ").split() if part]
    elif isinstance(raw_value, (list, tuple)):
        parts = list(raw_value)
    else:
        raise SettingsValidationError(f"参数 {key} 必须是控制点列表")

    normalized: list[list[float]] = []
    for point in parts:
        if isinstance(point, str):
            point = [part for part in point.split(",") if part != ""]
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise SettingsValidationError(f"参数 {key} 的每个控制点都需要 x 与 y")
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError) as error:
            raise SettingsValidationError(f"参数 {key} 的控制点必须是数字") from error
        if not 0 <= x <= 255 or not 0 <= y <= 255:
            raise SettingsValidationError(f"参数 {key} 的控制点必须位于 0-255")
        normalized.append([x, y])

    if len(normalized) < 2:
        raise SettingsValidationError(f"参数 {key} 至少需要两个控制点")
    if len(normalized) > CURVE_MAX_POINTS:
        raise SettingsValidationError(f"参数 {key} 最多支持 {CURVE_MAX_POINTS} 个控制点")
    if any(
        normalized[index][0] >= normalized[index + 1][0]
        for index in range(len(normalized) - 1)
    ):
        raise SettingsValidationError(f"参数 {key} 的控制点需要按 x 从小到大排列")
    return normalized


def _validate_control(control: DevelopControl, raw_value: Any) -> Any:
    """One value checked against its registry entry; raises on anything the host refuses."""
    if control.kind == "number":
        return _validate_number(control, raw_value)
    if control.kind == "enum":
        return _validate_enum(control, raw_value)
    if control.kind == "bool":
        return _validate_bool(control, raw_value)
    return _curve_points(control, raw_value)


def _vignette_problems(settings: dict[str, Any]) -> list[str]:
    """Reasons a model answer would render a hard-edged circle instead of a vignette.

    These values are all legal Lightroom values, so the range check lets them through
    and the artifact only shows up on the photo. The suggestion chain sends the
    reasons back to the model, and ``tame_vignette_artifacts()`` is the net that stops
    the ones the model insists on from reaching the photo.
    """
    problems: list[str] = []
    if settings.get("PostCropVignetteStyle") == VIGNETTE_OVERLAY_STYLE:
        problems.append(
            f"参数 PostCropVignetteStyle 取值 {VIGNETTE_OVERLAY_STYLE}（绘制叠加）会把暗角铺成一层纯色，"
            "在画面上看起来像一块蒙版；请改用 1（高光优先）或 2（颜色优先），或者不要这个键"
        )
    amount = settings.get("PostCropVignetteAmount")
    if (
        isinstance(amount, (int, float))
        and not isinstance(amount, bool)
        and abs(float(amount)) < VIGNETTE_VISIBLE_AMOUNT
    ):
        # The answer's own amount is invisible, so its other vignette knobs cannot
        # draw a visible shape on top of it.
        return problems
    for key, (low, high) in VIGNETTE_SAFE_WINDOW.items():
        value = settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if low <= float(value) <= high:
            continue
        problems.append(
            f"参数 {key} 的值 {float(value):g} 会让暗角在画面上画出一圈硬边圆形（看起来像一块蒙版）："
            f"{VIGNETTE_WINDOW_REASONS[key]}，请改到 {low:g} 至 {high:g} 之间"
        )
    return problems


def review_develop_settings(
    settings: dict[str, Any], *, guard_vignette: bool = False
) -> tuple[dict[str, Any], list[str]]:
    """Split one model answer into the values Lightroom accepts plus the overflow reasons.

    The vision model can answer outside a slider range, with an enum name the host
    does not know, with a wrongly typed value or with a key the registry does not
    define. The suggestion chain sends those reasons back to the model for one more
    answer instead of failing the whole request, so this returns both halves
    separately: the usable values and one Chinese sentence per rejected value, ready
    to be shown to the user. Only the accepted half ever reaches Lightroom.

    ``guard_vignette`` additionally reports vignette values that are inside the slider
    ranges but render as a hard-edged circle on the photo (see VIGNETTE_SAFE_WINDOW).
    The suggestion chain turns it on for model answers; the strict path used by the
    bridge and the MCP tools leaves it off, so hand-made values pass through as sent.
    """
    if not isinstance(settings, dict) or not settings:
        return {}, ["至少需要一个调色参数"]

    normalized: dict[str, Any] = {}
    problems: list[str] = []
    for name, raw_value in settings.items():
        control = DEVELOP_CONTROLS.get(name)
        if control is None:
            problems.append(f"不支持的 Lightroom 参数: {name}")
            continue
        try:
            normalized[name] = _validate_control(control, raw_value)
        except SettingsValidationError as error:
            problems.append(str(error))

    if guard_vignette:
        problems.extend(_vignette_problems(normalized))

    return normalized, problems


def validate_develop_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return supported Lightroom values after rejecting invalid model output."""
    normalized, problems = review_develop_settings(settings)
    if problems:
        raise SettingsValidationError(problems[0])
    return normalized


def tame_vignette_artifacts(settings: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Pull the vignette values that would draw a fake mask into the safe window.

    Last-resort net behind the repair rounds: the model is asked to fix those values
    first, and whatever it still insists on is clamped here, so no answer that renders
    as a hard-edged circle can reach the photo. Each change is spelled out for the
    user, and a value inside the window is never touched.
    """
    if not _vignette_problems(settings):
        return dict(settings), []

    tamed = dict(settings)
    changes: list[str] = []
    if tamed.get("PostCropVignetteStyle") == VIGNETTE_OVERLAY_STYLE:
        tamed.pop("PostCropVignetteStyle")
        changes.append("PostCropVignetteStyle 绘制叠加 已去掉（会把暗角铺成一块纯色蒙版）")
    for key, (low, high) in VIGNETTE_SAFE_WINDOW.items():
        value = tamed.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if low <= float(value) <= high:
            continue
        clamped = min(max(float(value), low), high)
        tamed[key] = clamped
        changes.append(f"{key} 由 {float(value):g} 收敛为 {clamped:g}（{VIGNETTE_WINDOW_REASONS[key]}）")
    return tamed, changes


def _control_payload(control: DevelopControl) -> dict[str, Any]:
    return {
        "key": control.key,
        "label": control.label,
        "group": control.group,
        "controller": control.controller,
        "kind": control.kind,
        "minimum": control.minimum,
        "maximum": control.maximum,
        "step": control.step,
        "default": control.default,
        "choices": [[choice, label] for choice, label in control.choices],
        "core": control.core,
        "experimental": control.experimental,
        "model_default": _model_default(control),
        "description": control.description,
    }


def develop_controls_payload() -> dict[str, Any]:
    """Single source of truth shared by the web UI, the MCP tools and the docs."""
    return {
        "groups": [
            {
                "id": group_id,
                "label": group_label,
                "controls": [
                    _control_payload(control)
                    for control in DEVELOP_CONTROLS.values()
                    if control.group == group_id
                ],
            }
            for group_id, group_label in DEVELOP_GROUPS
        ],
        "model_keys": list(MODEL_SETTING_KEYS),
        "core_keys": list(CORE_SETTING_KEYS),
        "mask_keys": list(VIGNETTE_SETTING_KEYS),
        "total": len(DEVELOP_CONTROLS),
    }
