from __future__ import annotations

from typing import Any


DEVELOP_RANGES: dict[str, tuple[float, float]] = {
    "Exposure2012": (-5.0, 5.0),
    "Contrast2012": (-100.0, 100.0),
    "Highlights2012": (-100.0, 100.0),
    "Shadows2012": (-100.0, 100.0),
    "Whites2012": (-100.0, 100.0),
    "Blacks2012": (-100.0, 100.0),
    "Temperature": (2000.0, 50000.0),
    "Tint": (-150.0, 150.0),
    "Vibrance": (-100.0, 100.0),
    "Saturation": (-100.0, 100.0),
    "Texture": (-100.0, 100.0),
    "Clarity2012": (-100.0, 100.0),
    "Dehaze": (-100.0, 100.0),
}


class SettingsValidationError(ValueError):
    pass


def validate_develop_settings(settings: dict[str, Any]) -> dict[str, float]:
    """Return supported Lightroom values after rejecting invalid model output."""
    if not isinstance(settings, dict) or not settings:
        raise SettingsValidationError("至少需要一个调色参数")

    normalized: dict[str, float] = {}
    for name, raw_value in settings.items():
        if name not in DEVELOP_RANGES:
            raise SettingsValidationError(f"不支持的 Lightroom 参数: {name}")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise SettingsValidationError(f"参数 {name} 必须是数字")

        value = float(raw_value)
        lower, upper = DEVELOP_RANGES[name]
        if not lower <= value <= upper:
            raise SettingsValidationError(
                f"参数 {name} 超出范围 [{lower:g}, {upper:g}]: {value:g}"
            )
        normalized[name] = value

    return normalized
