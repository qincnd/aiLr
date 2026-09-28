import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app
from app.services.llm import (
    MAX_PARAMETER_REPAIR_ROUNDS,
    MAX_REPAIR_ISSUES,
    _overflow_report,
    _repair_instruction,
    generate_suggestion,
)

LEGAL_ANSWER = '{"summary":"自然通透","settings":{"Exposure2012":0.4,"Vibrance":12}}'
OVERFLOW_ANSWER = '{"summary":"过曝","settings":{"Exposure2012":12,"Vibrance":12}}'
UNKNOWN_ENUM_ANSWER = '{"summary":"日光","settings":{"WhiteBalance":"Sunny","Vibrance":8}}'
REPAIRED_ENUM_ANSWER = '{"summary":"日光","settings":{"WhiteBalance":"Daylight","Vibrance":8}}'
# 合法但会在画面上画出一圈硬边圆形的暗角：圆度拉满 + 羽化 0 + 数量拉满。
CIRCLE_VIGNETTE_ANSWER = (
    '{"summary":"复古暖褐","settings":{"PostCropVignetteAmount":-80,"PostCropVignetteFeather":0,'
    '"PostCropVignetteRoundness":100,"PostCropVignetteMidpoint":50,"GrainAmount":15}}'
)
SOFT_VIGNETTE_ANSWER = (
    '{"summary":"复古暖褐","settings":{"PostCropVignetteAmount":-25,"PostCropVignetteFeather":60,'
    '"PostCropVignetteRoundness":0,"PostCropVignetteMidpoint":50,"GrainAmount":15}}'
)
# What the web UI posts once the 蒙版 master switch is on: the five vignette keys plus the
# grain keys the scripted answers carry.
MASK_ALLOWED = [
    "PostCropVignetteAmount",
    "PostCropVignetteFeather",
    "PostCropVignetteRoundness",
    "PostCropVignetteMidpoint",
    "PostCropVignetteStyle",
    "GrainAmount",
]


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class ScriptedModelClient:
    """httpx.AsyncClient stand-in: replays scripted answers, records every request."""

    def __init__(self, answers: list[str], provider: str) -> None:
        self.answers = list(answers)
        self.provider = provider
        self.requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> "ScriptedModelClient":
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False

    async def post(
        self, url: str, json: dict[str, Any] | None = None, headers: Any = None
    ) -> FakeResponse:
        self.requests.append(json or {})
        if not self.answers:
            raise AssertionError("模型被请求的次数超过了脚本预设")
        text = self.answers.pop(0)
        if self.provider == "ollama":
            return FakeResponse({"message": {"content": text}})
        return FakeResponse({"choices": [{"message": {"content": text}}]})


def _model_config(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        model_name="scripted-vision",
        ollama_base_url="http://127.0.0.1:11434",
        openai_base_url="https://api.openai.com/v1",
        api_key="" if provider == "ollama" else "test-key",
    )


@contextmanager
def scripted_model(answers: list[str], provider: str = "ollama") -> Iterator[ScriptedModelClient]:
    client = ScriptedModelClient(answers, provider)
    with (
        patch("app.services.llm.load_model_config", return_value=_model_config(provider)),
        patch("app.services.llm.httpx.AsyncClient", lambda *args, **kwargs: client),
    ):
        yield client


class SuggestionOverflowRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_legal_answer_is_returned_without_a_repair_round(self) -> None:
        with scripted_model([LEGAL_ANSWER]) as client:
            result = await generate_suggestion("自然通透", b"image", "image/jpeg")

        self.assertEqual(result["settings"], {"Exposure2012": 0.4, "Vibrance": 12.0})
        self.assertEqual(result["summary"], "自然通透")
        self.assertNotIn("overflow", result)
        self.assertEqual(len(client.requests), 1)

    async def test_overflow_is_sent_back_to_the_model(self) -> None:
        with scripted_model([OVERFLOW_ANSWER, LEGAL_ANSWER]) as client:
            result = await generate_suggestion("自然通透", b"image", "image/jpeg")

        self.assertEqual(result["settings"], {"Exposure2012": 0.4, "Vibrance": 12.0})
        overflow = result["overflow"]
        self.assertTrue(overflow["repaired"])
        self.assertEqual(overflow["attempts"], 2)
        self.assertEqual(overflow["rounds"], 1)
        self.assertEqual(overflow["unresolved"], [])
        self.assertEqual(overflow["dropped_keys"], [])
        self.assertIn("参数 Exposure2012 超出范围 [-5, 5]: 12", overflow["resolved"])
        self.assertIn("全部参数现已落在允许范围内", overflow["notice"])

        # The correction turn names the overflowing key and echoes the answer to fix.
        messages = client.requests[-1]["messages"]
        self.assertEqual(messages[-2]["role"], "assistant")
        self.assertEqual(messages[-2]["content"], OVERFLOW_ANSWER)
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("breaks these rules", messages[-1]["content"])
        self.assertIn("Exposure2012", messages[-1]["content"])
        # The system prompt already warns that an overflowing answer comes back.
        self.assertIn("sent straight back to you for another attempt", messages[0]["content"])

    async def test_mistyped_enum_is_repaired(self) -> None:
        with scripted_model([UNKNOWN_ENUM_ANSWER, REPAIRED_ENUM_ANSWER]) as client:
            result = await generate_suggestion("日光感", b"image", "image/jpeg")

        self.assertEqual(result["settings"], {"WhiteBalance": "Daylight", "Vibrance": 8.0})
        self.assertTrue(result["overflow"]["repaired"])
        self.assertEqual(len(client.requests), 2)
        self.assertIn("只接受以下取值", client.requests[-1]["messages"][-1]["content"])

    async def test_stubborn_overflow_is_dropped_and_reported(self) -> None:
        answers = [OVERFLOW_ANSWER] * (MAX_PARAMETER_REPAIR_ROUNDS + 1)
        with scripted_model(answers) as client:
            result = await generate_suggestion("自然通透", b"image", "image/jpeg")

        # Only the values inside the ranges reach the caller, and the user is told.
        self.assertEqual(result["settings"], {"Vibrance": 12.0})
        overflow = result["overflow"]
        self.assertFalse(overflow["repaired"])
        self.assertEqual(overflow["attempts"], MAX_PARAMETER_REPAIR_ROUNDS + 1)
        self.assertEqual(overflow["rounds"], MAX_PARAMETER_REPAIR_ROUNDS)
        self.assertEqual(overflow["dropped_keys"], ["Exposure2012"])
        self.assertEqual(overflow["unresolved"], ["参数 Exposure2012 超出范围 [-5, 5]: 12"])
        self.assertIn("已丢弃这些参数（Exposure2012）", overflow["notice"])
        self.assertEqual(len(client.requests), MAX_PARAMETER_REPAIR_ROUNDS + 1)

    async def test_unreadable_correction_keeps_the_usable_part(self) -> None:
        with scripted_model([OVERFLOW_ANSWER, "not json"]) as client:
            result = await generate_suggestion("自然通透", b"image", "image/jpeg")

        self.assertEqual(result["settings"], {"Vibrance": 12.0})
        self.assertEqual(result["overflow"]["dropped_keys"], ["Exposure2012"])
        self.assertEqual(result["overflow"]["rounds"], 1)
        self.assertEqual(len(client.requests), 2)

    async def test_a_circle_vignette_is_sent_back_for_repair(self) -> None:
        with scripted_model([CIRCLE_VIGNETTE_ANSWER, SOFT_VIGNETTE_ANSWER]) as client:
            result = await generate_suggestion("复古暖褐胶片", b"image", "image/jpeg", MASK_ALLOWED)

        # The second answer is used as it is, so nothing has to be clamped afterwards.
        self.assertEqual(result["settings"]["PostCropVignetteRoundness"], 0.0)
        self.assertEqual(result["settings"]["PostCropVignetteFeather"], 60.0)
        self.assertEqual(result["settings"]["GrainAmount"], 15.0)
        overflow = result["overflow"]
        self.assertEqual(overflow["adjusted"], [])
        self.assertTrue(overflow["repaired"])
        self.assertEqual(overflow["rounds"], 1)
        self.assertEqual(len(overflow["resolved"]), 3)
        self.assertTrue(
            any("PostCropVignetteRoundness" in problem for problem in overflow["resolved"])
        )
        self.assertEqual(len(client.requests), 2)
        # The correction turn names the artifact, not a range the host never rejected.
        repair_turn = client.requests[-1]["messages"][-1]["content"]
        self.assertIn("硬边圆形", repair_turn)
        self.assertIn("PostCropVignetteFeather", repair_turn)

    async def test_a_stubborn_circle_vignette_is_tamed_before_lightroom(self) -> None:
        answers = [CIRCLE_VIGNETTE_ANSWER] * (MAX_PARAMETER_REPAIR_ROUNDS + 1)
        with scripted_model(answers) as client:
            result = await generate_suggestion("复古暖褐胶片", b"image", "image/jpeg", MASK_ALLOWED)

        # Whatever the model insists on is pulled into the window that cannot draw a
        # hard-edged circle; the rest of the answer is untouched.
        settings = result["settings"]
        self.assertEqual(settings["PostCropVignetteAmount"], -60.0)
        self.assertEqual(settings["PostCropVignetteFeather"], 25.0)
        self.assertEqual(settings["PostCropVignetteRoundness"], 25.0)
        self.assertEqual(settings["PostCropVignetteMidpoint"], 50.0)
        self.assertEqual(settings["GrainAmount"], 15.0)

        overflow = result["overflow"]
        self.assertEqual(len(overflow["adjusted"]), 3)
        self.assertTrue(
            any(
                item.startswith("PostCropVignetteRoundness 由 100 收敛为 25")
                for item in overflow["adjusted"]
            )
        )
        self.assertEqual(overflow["dropped_keys"], [])
        self.assertIn("收敛", overflow["notice"])
        self.assertEqual(len(client.requests), MAX_PARAMETER_REPAIR_ROUNDS + 1)

    async def test_a_soft_vignette_is_left_alone(self) -> None:
        with scripted_model([SOFT_VIGNETTE_ANSWER]) as client:
            result = await generate_suggestion("复古暖褐胶片", b"image", "image/jpeg", MASK_ALLOWED)

        self.assertNotIn("overflow", result)
        self.assertEqual(result["settings"]["PostCropVignetteRoundness"], 0.0)
        self.assertEqual(len(client.requests), 1)

    async def test_the_mask_stays_off_unless_the_request_unlocks_it(self) -> None:
        with scripted_model([SOFT_VIGNETTE_ANSWER]) as client:
            result = await generate_suggestion("复古暖褐胶片", b"image", "image/jpeg")

        # Without the mask switch the vignette is not in the allowed set, so the model's
        # vignette values are filtered out before they can reach Lightroom, and the prompt
        # never mentions them.
        self.assertEqual(result["settings"], {"GrainAmount": 15.0})
        self.assertNotIn("PostCropVignette", client.requests[0]["messages"][0]["content"])

    async def test_every_value_overflowing_raises_instead_of_repairing_forever(self) -> None:
        oversize = '{"summary":"过曝","settings":{"Exposure2012":12}}'
        with scripted_model([oversize] * (MAX_PARAMETER_REPAIR_ROUNDS + 1)) as client, self.assertRaises(
            ValueError
        ) as raised:
            await generate_suggestion("自然通透", b"image", "image/jpeg")

        self.assertIn("全部越界", str(raised.exception))
        self.assertIn("Exposure2012", str(raised.exception))
        self.assertEqual(len(client.requests), MAX_PARAMETER_REPAIR_ROUNDS + 1)

    async def test_a_disallowed_key_is_not_an_overflow(self) -> None:
        answer = (
            '{"summary":"自然通透","settings":'
            '{"Exposure2012":0.4,"Vibrance":12,"PerspectiveScale":100}}'
        )
        with scripted_model([answer]) as client:
            result = await generate_suggestion(
                "自然通透", b"image", "image/jpeg", ["Exposure2012", "Vibrance"]
            )

        self.assertEqual(result["settings"], {"Exposure2012": 0.4, "Vibrance": 12.0})
        self.assertNotIn("overflow", result)
        self.assertEqual(len(client.requests), 1)

    async def test_cloud_repair_turn_repeats_the_text_only(self) -> None:
        with scripted_model([OVERFLOW_ANSWER, LEGAL_ANSWER], "openai") as client:
            result = await generate_suggestion("自然通透", b"image", "image/jpeg")

        self.assertEqual(result["settings"], {"Exposure2012": 0.4, "Vibrance": 12.0})
        first_user = client.requests[0]["messages"][1]["content"]
        self.assertEqual([part["type"] for part in first_user], ["text", "image_url"])
        repair_user = client.requests[-1]["messages"][-1]["content"]
        # The photo is already in the conversation, so the repair turn stays cheap.
        self.assertEqual(len(repair_user), 1)
        self.assertEqual(repair_user[0]["type"], "text")
        self.assertIn("Exposure2012", repair_user[0]["text"])

    def test_repair_instruction_caps_the_overflow_list(self) -> None:
        problems = [
            f"参数 Key{index} 超出范围 [-1, 1]: 9" for index in range(MAX_REPAIR_ISSUES + 3)
        ]

        instruction = _repair_instruction(problems)

        self.assertIn("参数 Key0 超出范围 [-1, 1]: 9", instruction)
        self.assertNotIn(f"参数 Key{MAX_REPAIR_ISSUES} ", instruction)
        self.assertIn("3 more of the same kind", instruction)

    def test_overflow_report_is_empty_without_a_repair(self) -> None:
        self.assertIsNone(_overflow_report(1, 0, [], [], [], []))

    def test_a_tamed_vignette_alone_still_gets_a_report(self) -> None:
        report = _overflow_report(1, 0, [], [], [], ["PostCropVignetteRoundness 由 100 收敛为 25"])

        self.assertEqual(report["adjusted"], ["PostCropVignetteRoundness 由 100 收敛为 25"])
        self.assertEqual(report["unresolved"], [])
        self.assertEqual(report["resolved"], [])
        self.assertIn("防伪蒙版", report["notice"])


class SuggestionApiOverflowTests(unittest.TestCase):
    def test_the_overflow_report_reaches_the_web_client(self) -> None:
        client = TestClient(app)
        overflow = {
            "attempts": 2,
            "rounds": 1,
            "repaired": True,
            "resolved": ["参数 Exposure2012 超出范围 [-5, 5]: 12"],
            "unresolved": [],
            "dropped_keys": [],
            "adjusted": [],
            "notice": "模型首次回答有 1 项参数不合规，已把溢出清单回传并修正 1 次，全部参数现已落在允许范围内。",
        }
        suggestion = {"summary": "自然通透", "settings": {"Exposure2012": 0.4}, "overflow": overflow}
        with (
            patch("app.main.runtime.active", True),
            patch("app.main.generate_suggestion", new_callable=AsyncMock, return_value=suggestion),
        ):
            response = client.post(
                "/api/suggestions",
                data={"prompt": "自然通透"},
                files={"photo": ("photo.jpg", b"image", "image/jpeg")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["overflow"], overflow)


if __name__ == "__main__":
    unittest.main()
