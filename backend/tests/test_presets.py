import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import presets
from app.main import app

SHIPPED_PRESETS = Path(__file__).resolve().parents[2] / "color_presets.json"


def read_document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def stored_presets(path: Path) -> list[dict[str, object]]:
    return read_document(path)["presets"]


class ShippedPresetFileTests(unittest.TestCase):
    """随仓库发布的 color_presets.json 必须是一份合法的 v2 预设文件。"""

    def test_file_lives_in_project_root_and_parses(self) -> None:
        self.assertEqual(presets.PRESETS_PATH, SHIPPED_PRESETS)
        self.assertTrue(SHIPPED_PRESETS.is_file())

        loaded = presets.load_presets()

        self.assertGreaterEqual(len(loaded), 8)
        self.assertEqual(loaded[0].id, "film-portrait")
        self.assertEqual(loaded[0].name, "胶片暖调人像")
        self.assertEqual(loaded[0].tags, ["人像", "婚礼", "日常"])
        ids = [preset.id for preset in loaded]
        self.assertEqual(len(ids), len(set(ids)))
        for preset in loaded:
            self.assertTrue(preset.name)
            self.assertTrue(preset.tags)
            self.assertTrue(preset.prompt.structured)
            self.assertTrue(preset.prompt.steps)
            if preset.default_intensity is not None:
                self.assertGreater(preset.default_intensity, 0)
                self.assertLessEqual(preset.default_intensity, 1)
            # 合成出来的创作说明就是前端填进输入框、再原样发给模型的那段话。
            self.assertGreaterEqual(len(preset.instruction), 4)
            self.assertLessEqual(len(preset.instruction), presets.PRESET_PROMPT_MAX_LENGTH)

    def test_instruction_carries_steps_constraints_and_the_reference_block(self) -> None:
        film = presets.load_presets()[0]

        self.assertTrue(film.instruction.startswith("胶片暖调人像：高光加暖"))
        self.assertIn("约束：肤色干净通透带奶油感", film.instruction)
        # 参考参数的标题行同时是 llm.py 识别「这次请求带了建议区间」的标记。
        self.assertIn(presets.PARAMS_HINT_TITLE, film.instruction)
        self.assertIn("整体强度 0.8", film.instruction)
        # 短名会补上注册表中文标签与 Lightroom 键名，模型不必猜「高光」是哪个滑块。
        self.assertIn("高光(Highlights2012) +5~+10", film.instruction)
        self.assertIn("颗粒 数量(GrainAmount) 10~15", film.instruction)
        # 预设里的每一段都进了说明，没有只挑一部分。
        for step in film.prompt.steps:
            self.assertIn(step, film.instruction)

    def test_file_uses_utf8_without_bom_and_crlf(self) -> None:
        raw = SHIPPED_PRESETS.read_bytes()

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        raw.decode("utf-8")


class PresetFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "color_presets.json"
        shutil.copyfile(SHIPPED_PRESETS, self.path)
        self.baseline = len(stored_presets(self.path))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_raw(self, raw: str) -> None:
        self.path.write_text(raw, encoding="utf-8")

    def test_appends_new_preset_and_persists_it(self) -> None:
        created = presets.create_preset(
            presets.PresetDraft(
                name="暮色江面",
                tag="氛围",
                summary="压低高光、冷紫阴影，保留水面反光。",
                prompt="暮色江面：压低高光让天空不过曝，阴影推向冷紫，水面反光保留细节，整体对比适中。",
            ),
            self.path,
        )

        self.assertEqual(created.id, "preset")
        self.assertEqual(created.tags, ["氛围"])
        self.assertEqual(created.created_at, presets.date.today().isoformat())
        self.assertFalse(created.prompt.structured)
        stored = stored_presets(self.path)
        self.assertEqual(len(stored), self.baseline + 1)
        self.assertEqual(stored[-1]["name"], "暮色江面")
        self.assertEqual(stored[-1]["created_at"], created.created_at)
        self.assertEqual(presets.load_presets(self.path)[-1].prompt.text, created.prompt.text)

    def test_appends_text_prompt_with_params_hint(self) -> None:
        created = presets.create_preset(
            presets.PresetDraft(
                name="银盐冷调",
                tag="风格化",
                summary="冷白、低饱和、干净的高光。",
                prompt="银盐冷调：白平衡偏冷，整体低饱和，高光干净，阴影略带青灰。",
                params_hint={"temp": "4200~4800", "saturation": "-15~-8", "色温": "偏冷 5~10"},
            ),
            self.path,
        )

        # 填了参数参考就写成 { text, params_hint } 对象，正文不再被硬拆成 steps。
        self.assertTrue(created.prompt.structured)
        self.assertEqual(created.prompt.steps, [])
        self.assertEqual(created.prompt.params_hint["temp"], "4200~4800")
        stored = stored_presets(self.path)[-1]
        self.assertEqual(
            stored["prompt"],
            {
                "text": "银盐冷调：白平衡偏冷，整体低饱和，高光干净，阴影略带青灰。",
                "params_hint": {"temp": "4200~4800", "saturation": "-15~-8", "色温": "偏冷 5~10"},
            },
        )
        self.assertEqual(
            presets.load_presets(self.path)[-1].prompt.params_hint["saturation"], "-15~-8"
        )
        # 合成结果：正文一段 + 参考参数一行；短名补上标注，中文标签原样保留。
        self.assertEqual(created.instruction.splitlines()[0], stored["prompt"]["text"])
        self.assertIn(presets.PARAMS_HINT_TITLE, created.instruction)
        self.assertIn("色温(Temperature) 4200~4800", created.instruction)
        self.assertIn("饱和度(Saturation) -15~-8", created.instruction)
        self.assertIn("色温 偏冷 5~10", created.instruction)

    def test_keeps_plain_text_form_when_params_hint_is_empty(self) -> None:
        created = presets.create_preset(
            presets.PresetDraft(
                name="海雾蓝调",
                prompt="海雾蓝调：白平衡压冷、整体低饱和，让远山有层次但不发灰。",
                params_hint={},
            ),
            self.path,
        )

        # 参数参考整块留空：预设仍是字符串形态，创作说明里不会多出参考参数那一行。
        self.assertFalse(created.prompt.structured)
        self.assertEqual(stored_presets(self.path)[-1]["prompt"], created.prompt.text)
        self.assertNotIn(presets.PARAMS_HINT_TITLE, created.instruction)

    def test_params_hint_options_cover_aliases_and_registry_keys(self) -> None:
        options = presets.params_hint_options()
        names = [option["name"] for option in options]
        labels = {option["name"]: option["label"] for option in options}

        # 表单的 datalist 需要去重后的完整候选表：短名在前，其余是注册表键名。
        self.assertEqual(len(names), len(set(names)))
        for alias in presets.HINT_ALIASES:
            self.assertIn(alias, labels)
        for key in presets.DEVELOP_CONTROLS:
            self.assertIn(key, labels)
        self.assertEqual(labels["highlights"], "高光(Highlights2012)")
        self.assertEqual(labels["Highlights2012"], "高光(Highlights2012)")
        self.assertEqual(labels["temp"], "色温(Temperature)")
        # 键名与短名指向同一个标注；枚举取值、中文标签不在候选表里，写了也照样保留
        # （见 test_appends_text_prompt_with_params_hint 里的「色温 偏冷 5~10」）。
        self.assertEqual(labels["WhiteBalance"], "白平衡预设(WhiteBalance)")
        self.assertEqual(len(options), len(set(presets.HINT_ALIASES) | set(presets.DEVELOP_CONTROLS)))

    def test_fills_missing_tag_and_summary_from_prompt(self) -> None:
        created = presets.create_preset(
            presets.PresetDraft(name="海雾蓝调", prompt="海雾蓝调：白平衡压冷、整体低饱和，让远山有层次但不发灰。"),
            self.path,
        )

        self.assertEqual(created.tags, [presets.DEFAULT_TAG])
        self.assertEqual(
            created.summary, created.prompt.text[: presets.PRESET_SUMMARY_FALLBACK_LENGTH]
        )

    def test_reads_and_keeps_structured_presets(self) -> None:
        self.write_raw(
            json.dumps(
                {
                    "version": "2.0",
                    "presets": [
                        {
                            "name": "街角霓虹",
                            "tags": ["夜景", "城市"],
                            "default_intensity": 0.7,
                            "prompt": {
                                "steps": ["压低曝光让夜空纯净", "单独提升红色与蓝色饱和度"],
                                "constraints": ["暗部不要出现彩色噪点"],
                                "params_hint": {"exposure": "-0.5~-1.0", "blue_sat": "+15~+25"},
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )

        preset = presets.load_presets(self.path)[0]

        self.assertEqual(preset.id, "preset")
        self.assertEqual(preset.tags, ["夜景", "城市"])
        self.assertEqual(preset.default_intensity, 0.7)
        self.assertTrue(preset.prompt.structured)
        self.assertIn("街角霓虹：压低曝光让夜空纯净；单独提升红色与蓝色饱和度", preset.instruction)
        self.assertIn("约束：暗部不要出现彩色噪点", preset.instruction)
        self.assertIn("整体强度 0.7", preset.instruction)
        self.assertIn("曝光(Exposure2012) -0.5~-1.0", preset.instruction)
        self.assertIn("饱和度 蓝(SaturationAdjustmentBlue) +15~+25", preset.instruction)

        # 新增一条之后整份文件重写：结构化形态、强度与建议区间都要原样保留。
        presets.create_preset(
            presets.PresetDraft(name="自建方向", prompt="自建方向：整体低饱和，保留肤色与衣服层次。"),
            self.path,
        )
        stored = stored_presets(self.path)[0]

        self.assertEqual(stored["default_intensity"], 0.7)
        self.assertEqual(stored["prompt"]["constraints"], ["暗部不要出现彩色噪点"])
        self.assertEqual(
            stored["prompt"]["params_hint"], {"exposure": "-0.5~-1.0", "blue_sat": "+15~+25"}
        )
        self.assertEqual(read_document(self.path)["version"], "2.0")

    def test_keeps_plain_text_presets_while_writing(self) -> None:
        created = presets.create_preset(
            presets.PresetDraft(name="纯文本方向", prompt="纯文本方向：微微提亮、阴影偏暖，保持肤色自然。"),
            self.path,
        )

        stored = stored_presets(self.path)

        # 网页新建的预设保存为一段纯文本，与旧结构一致，不会被硬拆成 steps。
        self.assertIsInstance(stored[-1]["prompt"], str)
        self.assertEqual(stored[-1]["prompt"], created.prompt.text)
        self.assertEqual(created.instruction, created.prompt.text)
        self.assertEqual(stored[-1]["tags"], [presets.DEFAULT_TAG])
        # 文件里原有的结构化预设不会被改写成纯文本。
        self.assertIsInstance(stored[0]["prompt"], dict)

    def test_reads_v1_entries_with_a_single_tag(self) -> None:
        self.write_raw(
            json.dumps(
                {
                    "presets": [
                        {"name": "Blue Hour", "tag": "蓝调", "prompt": "蓝调时刻：阴影冷蓝，高光保持中性。"}
                    ]
                },
                ensure_ascii=False,
            )
        )

        preset = presets.load_presets(self.path)[0]

        self.assertEqual(preset.id, "blue-hour")
        self.assertEqual(preset.tags, ["蓝调"])
        self.assertFalse(preset.prompt.structured)
        self.assertIsNone(preset.default_intensity)
        self.assertEqual(preset.instruction, "蓝调时刻：阴影冷蓝，高光保持中性。")

    def test_generates_ascii_id_and_avoids_collisions(self) -> None:
        first = presets.create_preset(
            presets.PresetDraft(name="Teal Film", prompt="青色胶片：阴影青绿，高光暖白。"),
            self.path,
        )
        second = presets.create_preset(
            presets.PresetDraft(name="Teal  Film", prompt="复刻青色胶片：阴影青绿，高光暖白并带一点颗粒。"),
            self.path,
        )

        self.assertEqual(first.id, "teal-film")
        self.assertEqual(second.id, "teal-film-2")

    def test_rejects_structured_prompt_problems(self) -> None:
        cases: list[tuple[object, str]] = [
            ({"steps": []}, "至少要写 1 条调色步骤"),
            ({"steps": ["压低曝光"] * (presets.PRESET_STEPS_MAX_COUNT + 1)}, "prompt.steps 最多"),
            ({"steps": ["暖" * (presets.PRESET_STEP_MAX_LENGTH + 1)]}, "每条步骤最多"),
            (
                {"steps": ["压低曝光"], "constraints": ["保留暗部"] * (presets.PRESET_CONSTRAINTS_MAX_COUNT + 1)},
                "prompt.constraints 最多",
            ),
            ({"steps": ["压低曝光"], "params_hint": ["exposure"]}, "params_hint 需要是一个对象"),
            ({"steps": ["压低曝光"], "params_hint": {"exposure": "   "}}, "取值不能为空"),
            (
                {"steps": ["压低曝光"], "params_hint": {f"k{index}": "+1~+2" for index in range(presets.PRESET_HINT_MAX_COUNT + 1)}},
                "params_hint 最多",
            ),
            ("暖", "至少 4 个字"),
            (123, "需要是一段文字"),
        ]

        for prompt, expected in cases:
            with self.subTest(expected=expected):
                self.write_raw(
                    json.dumps({"presets": [{"name": "结构化", "prompt": prompt}]}, ensure_ascii=False)
                )
                with self.assertRaises(presets.PresetFileError) as raised:
                    presets.load_presets(self.path)
                self.assertIn(expected, str(raised.exception))

    def test_rejects_out_of_range_intensity(self) -> None:
        for value in (0, 1.5, "0.8", True):
            with self.subTest(value=value):
                self.write_raw(
                    json.dumps(
                        {
                            "presets": [
                                {
                                    "name": "强度越界",
                                    "default_intensity": value,
                                    "prompt": {"steps": ["压低曝光"]},
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
                with self.assertRaises(presets.PresetFileError) as raised:
                    presets.load_presets(self.path)
                self.assertIn("default_intensity", str(raised.exception))

        # 省略强度是允许的：创作说明里就不会出现强度那一段。
        self.write_raw(
            json.dumps({"presets": [{"name": "无强度", "prompt": {"steps": ["压低曝光"]}}]}, ensure_ascii=False)
        )
        self.assertIsNone(presets.load_presets(self.path)[0].default_intensity)
        self.assertNotIn(presets.INTENSITY_LABEL, presets.load_presets(self.path)[0].instruction)

    def test_rejects_instruction_longer_than_the_prompt_limit(self) -> None:
        self.write_raw(
            json.dumps(
                {
                    "presets": [
                        {
                            "name": "超长方向",
                            "prompt": {"steps": [f"步骤{index}：" + "暖" * 76 for index in range(presets.PRESET_STEPS_MAX_COUNT)]},
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

        with self.assertRaises(presets.PresetFileError) as raised:
            presets.load_presets(self.path)

        self.assertIn("创作说明合成后最多", str(raised.exception))

    def test_rejects_invalid_drafts(self) -> None:
        cases = [
            (presets.PresetDraft(name="", prompt="足够长的提示词内容"), "名称不能为空"),
            (presets.PresetDraft(name="无提示词", prompt="   "), "提示词不能为空"),
            (presets.PresetDraft(name="太短", prompt="暖"), "至少"),
            (presets.PresetDraft(name="超长名称" * 7, prompt="足够长的提示词内容"), "最多"),
            (presets.PresetDraft(name="超长描述", summary="描述" * 40, prompt="足够长的提示词内容"), "一句话描述最多"),
            (presets.PresetDraft(name="胶片暖调人像", prompt="重复名称：暖调肤色，高光加暖。"), "同名预设"),
        ]

        for draft, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(presets.PresetError) as raised:
                    presets.create_preset(draft, self.path)
                self.assertIn(expected, str(raised.exception))

        self.assertEqual(len(stored_presets(self.path)), self.baseline)

    def test_rejects_params_hint_problems_on_create(self) -> None:
        cases: list[tuple[dict[str, str], str]] = [
            ({"exposure": "   "}, "取值不能为空"),
            ({"   ": "+0.3~+0.7"}, "键名不能为空"),
            ({"e" * (presets.PRESET_HINT_KEY_MAX_LENGTH + 1): "+1"}, "键名最多"),
            ({"exposure": "1" * (presets.PRESET_HINT_VALUE_MAX_LENGTH + 1)}, "取值最多"),
            (
                {f"k{index}": "+1~+2" for index in range(presets.PRESET_HINT_MAX_COUNT + 1)},
                "params_hint 最多",
            ),
        ]

        for hints, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(presets.PresetError) as raised:
                    presets.create_preset(
                        presets.PresetDraft(
                            name="参数参考校验",
                            prompt="参数参考校验：只用来验证参数参考的边界与报错文案。",
                            params_hint=hints,
                        ),
                        self.path,
                    )
                self.assertIn(expected, str(raised.exception))

        self.assertEqual(len(stored_presets(self.path)), self.baseline)

    def test_rejects_draft_longer_than_the_prompt_limit(self) -> None:
        with self.assertRaises(presets.PresetError) as raised:
            presets.create_preset(
                presets.PresetDraft(name="过长提示词", prompt="暖" * (presets.PRESET_PROMPT_MAX_LENGTH + 1)),
                self.path,
            )

        self.assertIn(str(presets.PRESET_PROMPT_MAX_LENGTH), str(raised.exception))

    def test_rejects_new_preset_when_the_list_is_full(self) -> None:
        with patch.object(presets, "MAX_PRESETS", self.baseline):
            with self.assertRaises(presets.PresetError) as raised:
                presets.create_preset(
                    presets.PresetDraft(name="再来一个", prompt="再来一个足够长的提示词内容"),
                    self.path,
                )

        self.assertIn("最多", str(raised.exception))

    def test_deletes_by_id_and_reports_unknown_ids(self) -> None:
        self.assertTrue(presets.delete_preset("film-portrait", self.path))
        self.assertNotIn("film-portrait", [preset.id for preset in presets.load_presets(self.path)])
        self.assertEqual(len(stored_presets(self.path)), self.baseline - 1)
        self.assertFalse(presets.delete_preset("film-portrait", self.path))

    def test_deleting_every_preset_leaves_a_readable_empty_document(self) -> None:
        for preset in presets.load_presets(self.path):
            self.assertTrue(presets.delete_preset(preset.id, self.path))

        self.assertEqual(presets.load_presets(self.path), [])
        self.assertEqual(stored_presets(self.path), [])
        self.assertEqual(read_document(self.path)["version"], presets.PRESET_FILE_VERSION)

    def test_written_file_keeps_utf8_without_bom_and_crlf(self) -> None:
        presets.create_preset(
            presets.PresetDraft(name="行尾检查", prompt="行尾检查：确认写回的 JSON 保持 CRLF 与无 BOM。"),
            self.path,
        )
        raw = self.path.read_bytes()

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertTrue(raw.endswith(b"\r\n"))
        self.assertIn("行尾检查", raw.decode("utf-8"))

    def test_missing_file_reports_the_file_name(self) -> None:
        missing = self.path.with_name("nowhere.json")

        with self.assertRaises(presets.PresetFileError) as raised:
            presets.load_presets(missing)

        self.assertIsInstance(raised.exception, presets.PresetError)
        self.assertIn("nowhere.json", str(raised.exception))

    def test_broken_json_reports_the_line(self) -> None:
        self.write_raw('{"presets": [')

        with self.assertRaises(presets.PresetFileError) as raised:
            presets.load_presets(self.path)

        self.assertIn("不是合法的 JSON", str(raised.exception))

    def test_root_needs_a_presets_array(self) -> None:
        self.write_raw(json.dumps([{"name": "只有数组"}], ensure_ascii=False))

        with self.assertRaises(presets.PresetFileError) as raised:
            presets.load_presets(self.path)

        self.assertIn("presets 数组", str(raised.exception))

    def test_entry_errors_name_the_position(self) -> None:
        self.write_raw(
            json.dumps(
                {"presets": [{"name": "第一条", "prompt": "第一条足够长的提示词"}, {"name": "缺提示词"}]},
                ensure_ascii=False,
            )
        )

        with self.assertRaises(presets.PresetFileError) as raised:
            presets.load_presets(self.path)

        self.assertIn("第 2 项", str(raised.exception))
        self.assertIn("提示词不能为空", str(raised.exception))

    def test_duplicate_entry_ids_are_rejected(self) -> None:
        self.write_raw(
            json.dumps(
                {
                    "presets": [
                        {"id": "same", "name": "第一条", "prompt": "第一条足够长的提示词"},
                        {"id": "same", "name": "第二条", "prompt": "第二条足够长的提示词"},
                    ]
                },
                ensure_ascii=False,
            )
        )

        with self.assertRaises(presets.PresetFileError) as raised:
            presets.load_presets(self.path)

        self.assertIn("重复", str(raised.exception))

    def test_entries_without_ids_get_slugged_names(self) -> None:
        self.write_raw(
            json.dumps(
                {"presets": [{"name": "Blue Hour", "prompt": "蓝调时刻：阴影冷蓝，高光保持中性。"}]},
                ensure_ascii=False,
            )
        )

        self.assertEqual(presets.load_presets(self.path)[0].id, "blue-hour")


class PresetApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "color_presets.json"
        shutil.copyfile(SHIPPED_PRESETS, self.path)
        self.path_patch = patch.object(presets, "PRESETS_PATH", self.path)
        self.path_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_lists_presets_with_the_file_name(self) -> None:
        response = self.client.get("/api/presets")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["file"], "color_presets.json")
        self.assertEqual(payload["version"], presets.PRESET_FILE_VERSION)
        self.assertEqual(payload["presets"][0]["id"], "film-portrait")
        self.assertGreaterEqual(len(payload["presets"]), 8)
        first = payload["presets"][0]
        # 抽屉渲染卡片与填输入框需要的字段都在这一份载荷里。
        self.assertEqual(first["tags"], ["人像", "婚礼", "日常"])
        self.assertEqual(first["default_intensity"], 0.8)
        self.assertIn("高光加暖，阴影稍微抬起并带轻微褪色感", first["prompt"]["steps"])
        self.assertEqual(first["prompt"]["constraints"], ["肤色干净通透带奶油感，避免发黄发橙", "脸颊和额头必须保留细节"])
        self.assertEqual(first["prompt"]["params_hint"]["grain"], "10~15")
        self.assertTrue(first["prompt"]["structured"])
        self.assertIn("高光(Highlights2012) +5~+10", first["instruction"])
        self.assertIn(presets.PARAMS_HINT_TITLE, first["instruction"])

    def test_creates_preset_and_returns_the_updated_list(self) -> None:
        response = self.client.post(
            "/api/presets",
            json={
                "name": "银盐冷调",
                "tag": "风格化",
                "summary": "冷白、低饱和、干净的高光。",
                "prompt": "银盐冷调：白平衡偏冷，饱和度降低，高光干净，阴影略带青灰。",
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["preset"]["id"], "preset")
        self.assertEqual(payload["preset"]["name"], "银盐冷调")
        self.assertEqual(payload["preset"]["tags"], ["风格化"])
        self.assertEqual(payload["preset"]["prompt"]["text"], payload["preset"]["instruction"])
        self.assertEqual(len(payload["presets"]), len(stored_presets(self.path)))
        self.assertEqual(payload["presets"][-1]["name"], "银盐冷调")
        self.assertEqual(self.client.get("/api/presets").json()["presets"][-1]["tags"], ["风格化"])

    def test_creates_preset_with_params_hint_and_ships_the_form_options(self) -> None:
        response = self.client.post(
            "/api/presets",
            json={
                "name": "银盐冷调",
                "tag": "风格化",
                "prompt": "银盐冷调：白平衡偏冷，饱和度降低，高光干净，阴影略带青灰。",
                "params_hint": {"temp": "4200~4800", "saturation": "-15~-8"},
            },
        )

        self.assertEqual(response.status_code, 201)
        created = response.json()["preset"]
        self.assertTrue(created["prompt"]["structured"])
        self.assertEqual(created["prompt"]["steps"], [])
        self.assertEqual(
            created["prompt"]["params_hint"], {"temp": "4200~4800", "saturation": "-15~-8"}
        )
        self.assertEqual(
            created["instruction"],
            "银盐冷调：白平衡偏冷，饱和度降低，高光干净，阴影略带青灰。\n"
            "参考参数（建议，非强制）：色温(Temperature) 4200~4800；饱和度(Saturation) -15~-8",
        )
        stored = stored_presets(self.path)[-1]
        self.assertEqual(stored["prompt"]["params_hint"], {"temp": "4200~4800", "saturation": "-15~-8"})

        listed = self.client.get("/api/presets").json()
        # 表单的候选参数名随预设一起下发：前端据此渲染 datalist 与合成预览。
        names = {option["name"] for option in listed["params_hint_options"]}
        self.assertIn("highlights", names)
        self.assertIn("SharpenEdgeMasking", names)
        self.assertEqual(listed["presets"][-1]["prompt"]["text"], created["prompt"]["text"])

    def test_rejects_duplicate_name_with_a_chinese_message(self) -> None:
        response = self.client.post(
            "/api/presets",
            json={"name": "胶片暖调人像", "prompt": "重复名称：暖调肤色，高光加暖，阴影抬起。"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("同名预设", response.json()["detail"])

    def test_rejects_empty_name(self) -> None:
        response = self.client.post("/api/presets", json={"name": "  ", "prompt": "空名称的提示词内容"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("名称不能为空", response.json()["detail"])

    def test_deletes_preset_and_reports_unknown_ids(self) -> None:
        removed = self.client.delete("/api/presets/moody-dark")

        self.assertEqual(removed.status_code, 200)
        self.assertNotIn("moody-dark", [item["id"] for item in removed.json()["presets"]])
        self.assertEqual(self.client.delete("/api/presets/moody-dark").status_code, 404)

    def test_broken_file_returns_a_server_error_with_context(self) -> None:
        self.path.write_text("{ not json", encoding="utf-8")

        response = self.client.get("/api/presets")

        self.assertEqual(response.status_code, 500)
        self.assertIn("color_presets.json", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
