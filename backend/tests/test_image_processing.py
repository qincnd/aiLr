import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.services.image_processing import is_raw_image, prepare_image_for_model


class FakeRawImage:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def postprocess(self, **options):
        assert options["use_camera_wb"] is True
        assert options["half_size"] is True
        assert options["output_bps"] == 8
        return np.zeros((400, 600, 3), dtype=np.uint8)


class RawImageProcessingTests(unittest.TestCase):
    def test_detects_common_raw_extensions(self) -> None:
        for filename in ("camera.CR3", "scan.DNG", "portrait.NEF", "frame.RAF"):
            with self.subTest(filename=filename):
                self.assertTrue(is_raw_image(filename))
        self.assertFalse(is_raw_image("photo.jpg"))

    def test_converts_raw_to_bounded_jpeg_and_passes_through_raster(self) -> None:
        with patch("app.services.image_processing.rawpy.imread", return_value=FakeRawImage()):
            preview, mime_type = prepare_image_for_model(
                b"raw-data", "camera.cr3", "application/octet-stream"
            )

        image = Image.open(BytesIO(preview))
        self.assertEqual(mime_type, "image/jpeg")
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.size, (600, 400))

        original, original_type = prepare_image_for_model(b"jpeg-data", "photo.jpg", "image/jpeg")
        self.assertEqual(original, b"jpeg-data")
        self.assertEqual(original_type, "image/jpeg")

    def test_preview_api_returns_jpeg_for_raw(self) -> None:
        client = TestClient(app)
        with patch("app.services.image_processing.rawpy.imread", return_value=FakeRawImage()):
            response = client.post(
                "/api/images/preview",
                files={"photo": ("camera.dng", b"raw-data", "application/octet-stream")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(Image.open(BytesIO(response.content)).size, (600, 400))

    def test_preview_api_rejects_undecodable_raw(self) -> None:
        client = TestClient(app)
        with patch("app.services.image_processing.rawpy.imread", side_effect=ValueError("bad raw")):
            response = client.post(
                "/api/images/preview",
                files={"photo": ("broken.nef", b"not raw", "application/octet-stream")},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("无法解码", response.json()["detail"])

    def test_raw_preview_accepts_octet_stream_and_uses_raw_limit(self) -> None:
        client = TestClient(app)
        with patch("app.main.settings.max_image_mb", 0), patch(
            "app.main.settings.max_raw_image_mb", 1
        ), patch("app.services.image_processing.rawpy.imread", return_value=FakeRawImage()):
            response = client.post(
                "/api/images/preview",
                files={"photo": ("camera.CR3", b"raw-data", "application/octet-stream")},
            )

        self.assertEqual(response.status_code, 200)

    def test_preview_api_rejects_non_image_empty_and_oversized_uploads(self) -> None:
        client = TestClient(app)

        non_image = client.post(
            "/api/images/preview",
            files={"photo": ("notes.txt", b"not an image", "text/plain")},
        )
        empty = client.post(
            "/api/images/preview",
            files={"photo": ("empty.jpg", b"", "image/jpeg")},
        )
        with patch("app.main.settings.max_image_mb", 0):
            oversized = client.post(
                "/api/images/preview",
                files={"photo": ("photo.jpg", b"image", "image/jpeg")},
            )
        with patch("app.main.settings.max_raw_image_mb", 0):
            oversized_raw = client.post(
                "/api/images/preview",
                files={"photo": ("camera.dng", b"raw", "application/octet-stream")},
            )

        self.assertEqual(non_image.status_code, 415)
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized_raw.status_code, 413)

    def test_suggestions_forward_reference_upload_to_the_model(self) -> None:
        client = TestClient(app)
        suggestion = {"summary": "Reference style", "settings": {"Exposure2012": 0.2}}
        with (
            patch("app.main.runtime.active", True),
            patch(
                "app.main.generate_suggestion",
                new_callable=AsyncMock,
                return_value=suggestion,
            ) as generate,
        ):
            response = client.post(
                "/api/suggestions",
                data={"prompt": "Borrow the reference color"},
                files={
                    "photo": ("target.jpg", b"target-image", "image/jpeg"),
                    "reference": ("reference.png", b"reference-image", "image/png"),
                },
            )

        self.assertEqual(response.status_code, 200)
        generate.assert_awaited_once()
        call = generate.await_args
        assert call is not None
        self.assertEqual(call.args[:3], ("Borrow the reference color", b"target-image", "image/jpeg"))
        self.assertEqual(call.kwargs["reference_image"], b"reference-image")
        self.assertEqual(call.kwargs["reference_mime_type"], "image/png")

    def test_suggestions_send_converted_jpeg_to_the_model(self) -> None:
        client = TestClient(app)
        suggestion = {"summary": "RAW preview", "settings": {"Exposure2012": 0.2}}
        with (
            patch("app.main.runtime.active", True),
            patch("app.services.image_processing.rawpy.imread", return_value=FakeRawImage()),
            patch("app.main.generate_suggestion", new_callable=AsyncMock, return_value=suggestion) as generate,
        ):
            response = client.post(
                "/api/suggestions",
                data={"prompt": "natural"},
                files={"photo": ("camera.nef", b"raw-data", "application/octet-stream")},
            )

        self.assertEqual(response.status_code, 200)
        generate.assert_awaited_once()
        call = generate.await_args
        assert call is not None
        model_image = call.args[1]
        model_mime_type = call.args[2]
        self.assertEqual(model_mime_type, "image/jpeg")
        self.assertEqual(Image.open(BytesIO(model_image)).format, "JPEG")

    def test_suggestions_forward_the_allowed_key_set(self) -> None:
        client = TestClient(app)
        suggestion = {"summary": "Trimmed", "settings": {"Exposure2012": 0.2}}
        with (
            patch("app.main.runtime.active", True),
            patch("app.main.generate_suggestion", new_callable=AsyncMock, return_value=suggestion) as generate,
        ):
            response = client.post(
                "/api/suggestions",
                data={"prompt": "natural", "allowed_keys": '["Vibrance","Exposure2012"]'},
                files={"photo": ("photo.jpg", b"image", "image/jpeg")},
            )

        self.assertEqual(response.status_code, 200)
        # Registry order wins and the key set travels as the fourth argument.
        call = generate.await_args
        assert call is not None
        self.assertEqual(call.args[3], ("Exposure2012", "Vibrance"))

    def test_suggestions_reject_a_key_set_the_registry_cannot_use(self) -> None:
        client = TestClient(app)
        with (
            patch("app.main.runtime.active", True),
            patch("app.main.generate_suggestion", new_callable=AsyncMock) as generate,
        ):
            response = client.post(
                "/api/suggestions",
                data={"prompt": "natural", "allowed_keys": '["MadeUpControl"]'},
                files={"photo": ("photo.jpg", b"image", "image/jpeg")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("至少允许一个", response.json()["detail"])
        generate.assert_not_awaited()

    def test_suggestions_accept_a_comma_separated_key_set(self) -> None:
        client = TestClient(app)
        suggestion = {"summary": "Trimmed", "settings": {"Exposure2012": 0.2}}
        with (
            patch("app.main.runtime.active", True),
            patch("app.main.generate_suggestion", new_callable=AsyncMock, return_value=suggestion) as generate,
        ):
            response = client.post(
                "/api/suggestions",
                data={"prompt": "natural", "allowed_keys": "Exposure2012, Vibrance"},
                files={"photo": ("photo.jpg", b"image", "image/jpeg")},
            )

        self.assertEqual(response.status_code, 200)
        call = generate.await_args
        assert call is not None
        self.assertEqual(call.args[3], ("Exposure2012", "Vibrance"))


if __name__ == "__main__":
    unittest.main()
