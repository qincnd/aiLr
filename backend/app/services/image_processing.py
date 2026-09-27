from io import BytesIO
from pathlib import Path

import rawpy
from PIL import Image


RAW_EXTENSIONS = frozenset(
    {
        ".3fr", ".ari", ".arw", ".bay", ".cap", ".cr2", ".cr3", ".crw",
        ".dcs", ".dcr", ".dng", ".drf", ".eip", ".erf", ".fff", ".gpr",
        ".iiq", ".k25", ".kdc", ".mef", ".mos", ".mrw", ".nef", ".nrw",
        ".obm", ".orf", ".pef", ".ptx", ".pxn", ".raf", ".raw", ".rwl",
        ".rw2", ".rwz", ".sr2", ".srf", ".srw", ".x3f",
    }
)
MAX_PREVIEW_DIMENSION = 2048


class ImagePreparationError(ValueError):
    pass


def is_raw_image(filename: str | None) -> bool:
    return Path(filename or "").suffix.lower() in RAW_EXTENSIONS


def prepare_image_for_model(
    image_data: bytes, filename: str | None, mime_type: str | None
) -> tuple[bytes, str]:
    if not is_raw_image(filename):
        return image_data, mime_type or "application/octet-stream"

    try:
        with rawpy.imread(BytesIO(image_data)) as raw_image:
            rgb = raw_image.postprocess(
                use_camera_wb=True,
                half_size=True,
                output_bps=8,
            )
        preview = Image.fromarray(rgb)
        preview.thumbnail(
            (MAX_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        preview.save(output, format="JPEG", quality=90, optimize=True)
        return output.getvalue(), "image/jpeg"
    except (rawpy.LibRawError, OSError, TypeError, ValueError) as error:
        raise ImagePreparationError(
            "无法解码此 RAW 文件，请确认相机型号受 LibRaw 支持且文件完整"
        ) from error
