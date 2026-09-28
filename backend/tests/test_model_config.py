import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.model_runtime import runtime


class ModelConfigurationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "model_config.json"
        self.path_patch = patch.object(config, "CONFIG_PATH", self.config_path)
        self.path_patch.start()
        runtime.active = False
        runtime.message = "尚未启动"
        self.client = TestClient(app)

    def tearDown(self) -> None:
        runtime.active = False
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_saves_configuration_without_returning_api_key(self) -> None:
        response = self.client.put(
            "/api/model/config",
            json={
                "provider": "openai",
                "model_name": "vision-test",
                "openai_base_url": "https://api.example.com/v1",
                "api_key": "test-secret",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["api_key_configured"])
        self.assertNotIn("test-secret", response.text)
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["api_key"], "test-secret")

    def test_model_stays_stopped_until_explicit_start(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["model_active"])

    def test_detects_local_ollama_models_without_starting_them(self) -> None:
        config.save_model_config(
            config.ModelConfig(model_name="selected", ollama_base_url="http://ollama.test")
        )

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                request = httpx.Request("GET", url)
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "qwen2.5vl:7b",
                                "size": 6000,
                                "details": {"family": "qwen25vl", "parameter_size": "8.3B"},
                            },
                            {"name": "llava:13b", "size": 8000, "details": {}},
                        ]
                    },
                    request=request,
                )

        with patch("app.main.httpx.AsyncClient", return_value=FakeAsyncClient()):
            response = self.client.get("/api/models/local")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()["models"]], ["llava:13b", "qwen2.5vl:7b"])
        self.assertEqual(response.json()["models"][1]["parameter_size"], "8.3B")
        self.assertFalse(runtime.active)

    def test_skips_malformed_local_model_entries(self) -> None:
        config.save_model_config(
            config.ModelConfig(model_name="selected", ollama_base_url="http://ollama.test")
        )

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                request = httpx.Request("GET", url)
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            "not-a-dict",
                            {"name": 42},
                            {"name": "no-details:1b", "size": 100},
                            {"name": "bad-details:1b", "details": "oops"},
                        ]
                    },
                    request=request,
                )

        with patch("app.main.httpx.AsyncClient", return_value=FakeAsyncClient()):
            response = self.client.get("/api/models/local")

        self.assertEqual(response.status_code, 200)
        models = response.json()["models"]
        self.assertEqual([item["name"] for item in models], ["bad-details:1b", "no-details:1b"])
        self.assertEqual(models[0]["family"], "")
        self.assertEqual(models[0]["parameter_size"], "")
        self.assertEqual(models[0]["size"], 0)
        self.assertEqual(models[1]["size"], 100)

    def test_rejects_non_object_model_list_payload(self) -> None:
        config.save_model_config(
            config.ModelConfig(model_name="selected", ollama_base_url="http://ollama.test")
        )

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                request = httpx.Request("GET", url)
                return httpx.Response(200, json=["unexpected"], request=request)

        with patch("app.main.httpx.AsyncClient", return_value=FakeAsyncClient()):
            response = self.client.get("/api/models/local")

        self.assertEqual(response.status_code, 502)
        self.assertIn("无效的模型清单", response.json()["detail"])

    def test_ollama_load_happens_only_after_start_request(self) -> None:
        config.save_model_config(config.ModelConfig(model_name="test-vision"))
        calls: list[dict[str, Any]] = []

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, url: str, json: dict[str, object]):
                calls.append({"url": url, "json": json})
                request = httpx.Request("POST", url)
                return httpx.Response(200, json={"response": "READY"}, request=request)

        with patch("app.main.httpx.AsyncClient", return_value=FakeAsyncClient()):
            response = self.client.post("/api/model/start")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["active"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["json"]["model"], "test-vision")
        self.assertEqual(calls[0]["json"]["options"]["num_ctx"], 8192)

    def test_switching_to_local_preserves_cloud_api_key(self) -> None:
        self.client.put(
            "/api/model/config",
            json={"provider": "openai", "model_name": "vision-test", "api_key": "test-secret"},
        )

        response = self.client.put(
            "/api/model/config",
            json={"provider": "ollama", "model_name": "qwen2.5vl:7b", "api_key": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(config.load_model_config().api_key, "test-secret")

    def test_suggestion_requires_explicit_model_start(self) -> None:
        response = self.client.post(
            "/api/suggestions",
            files={"photo": ("photo.jpg", b"image", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("启动模型", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
