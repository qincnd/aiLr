import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import (
    MODEL_SETTING_KEYS,
    SettingsValidationError,
    VIGNETTE_SETTING_KEYS,
    develop_controls_payload,
    model_keys_for,
    review_develop_settings,
    tame_vignette_artifacts,
    validate_develop_settings,
)

MASK_KEYS = set(VIGNETTE_SETTING_KEYS)
from app.presets import PARAMS_HINT_LABEL
from app.services.llm import (
    REFERENCE_PARAMS_RULE,
    _build_system_prompt,
    _ollama_response_schema,
    _parse_suggestion,
)


class DevelopSettingsTests(unittest.TestCase):
    def test_accepts_supported_values(self) -> None:
        self.assertEqual(
            validate_develop_settings({"Exposure2012": 0.5, "Temperature": 5600}),
            {"Exposure2012": 0.5, "Temperature": 5600.0},
        )

    def test_rejects_values_outside_range(self) -> None:
        with self.assertRaises(SettingsValidationError):
            validate_develop_settings({"Exposure2012": 8})

    def test_rejects_unknown_controls_and_booleans(self) -> None:
        with self.assertRaises(SettingsValidationError):
            validate_develop_settings({"MadeUpControl": 1})
        with self.assertRaises(SettingsValidationError):
            validate_develop_settings({"Exposure2012": True})

    def test_parses_and_validates_model_output(self) -> None:
        result = _parse_suggestion('{"summary":"自然通透","settings":{"Exposure2012":0.4}}')
        self.assertEqual(result["settings"], {"Exposure2012": 0.4})
        with self.assertRaises(ValueError):
            _parse_suggestion("not json")
        with self.assertRaises(SettingsValidationError):
            _parse_suggestion('{"summary":"bad","settings":{"Exposure2012":12}}')
        with self.assertRaises(SettingsValidationError):
            _parse_suggestion('{"summary":"bad","settings":{"Temperature":0.5}}')

    def test_ollama_schema_constrains_temperature_to_kelvin_range(self) -> None:
        schema = _ollama_response_schema()
        temperature = schema["properties"]["settings"]["properties"]["Temperature"]

        self.assertEqual(temperature["minimum"], 2000.0)
        self.assertEqual(temperature["maximum"], 50000.0)
        self.assertIn("Kelvin", temperature["description"])

    def test_accepts_enum_boolean_and_point_curve_values(self) -> None:
        self.assertEqual(
            validate_develop_settings(
                {
                    "ToneCurveName": "Medium Contrast",
                    "AutoTone": True,
                    "ToneCurvePV2012": "0,0;64,52;128,132;255,255",
                    "PostCropVignetteStyle": 2,
                }
            ),
            {
                "ToneCurveName": "Medium Contrast",
                "AutoTone": True,
                "ToneCurvePV2012": [[0.0, 0.0], [64.0, 52.0], [128.0, 132.0], [255.0, 255.0]],
                "PostCropVignetteStyle": 2,
            },
        )

    def test_rejects_malformed_extended_values(self) -> None:
        invalid_settings = (
            {"ToneCurveName": "Bogus"},
            {"AutoTone": "true"},
            {"PostCropVignetteStyle": 9},
            {"ToneCurvePV2012": "0,0;255,255;128,128"},
            {"ToneCurvePV2012": [[0, 0], [300, 10]]},
            {"ToneCurvePV2012": "0,0"},
            {"HueAdjustmentRed": 400},
        )
        for settings in invalid_settings:
            with self.assertRaises(SettingsValidationError):
                validate_develop_settings(settings)

    def test_review_reports_overflow_instead_of_raising(self) -> None:
        accepted, problems = review_develop_settings(
            {
                "Exposure2012": 8,
                "Temperature": 5600,
                "MadeUpControl": 1,
                "AutoTone": "true",
                "ToneCurveName": "Bogus",
            }
        )

        # Only the values Lightroom accepts survive; the rest comes back as reasons
        # the suggestion chain can send to the model and show to the user.
        self.assertEqual(accepted, {"Temperature": 5600.0})
        self.assertEqual(len(problems), 4)
        self.assertIn("参数 Exposure2012 超出范围 [-5, 5]: 8", problems)
        self.assertIn("不支持的 Lightroom 参数: MadeUpControl", problems)
        self.assertIn("参数 AutoTone 必须是 true 或 false", problems)
        self.assertTrue(
            any(problem.startswith("参数 ToneCurveName 只接受以下取值") for problem in problems)
        )

    def test_review_keeps_a_legal_answer_untouched(self) -> None:
        self.assertEqual(
            review_develop_settings({"Exposure2012": 0.5, "AutoTone": True}),
            ({"Exposure2012": 0.5, "AutoTone": True}, []),
        )

    def test_review_flags_an_empty_answer(self) -> None:
        self.assertEqual(review_develop_settings({}), ({}, ["至少需要一个调色参数"]))

    def test_validate_still_raises_the_first_problem(self) -> None:
        with self.assertRaises(SettingsValidationError) as raised:
            validate_develop_settings({"Exposure2012": 8, "Vibrance": 10})

        self.assertIn("超出范围", str(raised.exception))

    def test_review_guards_a_vignette_that_would_draw_a_circle(self) -> None:
        accepted, problems = review_develop_settings(
            {
                "PostCropVignetteAmount": -80,
                "PostCropVignetteFeather": 0,
                "PostCropVignetteRoundness": 100,
                "PostCropVignetteMidpoint": 50,
                "Vibrance": 10,
            },
            guard_vignette=True,
        )

        # These are legal Lightroom values, so they stay in the answer; the guard only
        # says why they would render as a circular mask on the photo.
        self.assertEqual(accepted["PostCropVignetteRoundness"], 100.0)
        self.assertEqual(accepted["Vibrance"], 10.0)
        self.assertEqual(len(problems), 3)
        self.assertTrue(
            any(item.startswith("参数 PostCropVignetteRoundness 的值 100") for item in problems)
        )
        self.assertTrue(any("羽化为 0" in item for item in problems))
        self.assertTrue(any(item.startswith("参数 PostCropVignetteAmount 的值 -80") for item in problems))
        self.assertTrue(all("请改到" in item for item in problems))

    def test_review_guards_the_paint_overlay_vignette_style(self) -> None:
        accepted, problems = review_develop_settings(
            {"PostCropVignetteStyle": 3, "PostCropVignetteAmount": -20},
            guard_vignette=True,
        )

        self.assertEqual(accepted["PostCropVignetteStyle"], 3)
        self.assertEqual(len(problems), 1)
        self.assertIn("绘制叠加", problems[0])

    def test_review_keeps_a_soft_vignette_untouched(self) -> None:
        self.assertEqual(
            review_develop_settings(
                {
                    "PostCropVignetteAmount": -30,
                    "PostCropVignetteFeather": 55,
                    "PostCropVignetteRoundness": -40,
                    "PostCropVignetteMidpoint": 45,
                    "PostCropVignetteStyle": 2,
                },
                guard_vignette=True,
            ),
            (
                {
                    "PostCropVignetteAmount": -30.0,
                    "PostCropVignetteFeather": 55.0,
                    "PostCropVignetteRoundness": -40.0,
                    "PostCropVignetteMidpoint": 45.0,
                    "PostCropVignetteStyle": 2,
                },
                [],
            ),
        )

    def test_review_ignores_a_vignette_amount_that_is_invisible(self) -> None:
        # A normalized 0.2 draws nothing at all, so the other knobs cannot create a
        # visible shape and the answer does not need another model turn.
        self.assertEqual(
            review_develop_settings(
                {"PostCropVignetteAmount": 0.2, "PostCropVignetteRoundness": 100},
                guard_vignette=True,
            ),
            ({"PostCropVignetteAmount": 0.2, "PostCropVignetteRoundness": 100.0}, []),
        )

    def test_the_vignette_guard_is_off_for_hand_made_values(self) -> None:
        settings = {"PostCropVignetteAmount": -90, "PostCropVignetteRoundness": 100}

        # The web sliders, the bridge and the MCP tools keep sending exactly what was
        # asked for, so only the suggestion path turns the guard on.
        self.assertEqual(
            review_develop_settings(settings),
            ({"PostCropVignetteAmount": -90.0, "PostCropVignetteRoundness": 100.0}, []),
        )
        self.assertEqual(validate_develop_settings(settings)["PostCropVignetteRoundness"], 100.0)

    def test_tame_vignette_artifacts_clamps_the_circle(self) -> None:
        tamed, changes = tame_vignette_artifacts(
            {
                "PostCropVignetteAmount": -80,
                "PostCropVignetteFeather": 0,
                "PostCropVignetteRoundness": 100,
                "PostCropVignetteMidpoint": 100,
                "PostCropVignetteStyle": 3,
                "GrainAmount": 15,
            }
        )

        self.assertEqual(
            tamed,
            {
                "PostCropVignetteAmount": -60.0,
                "PostCropVignetteFeather": 25.0,
                "PostCropVignetteRoundness": 25.0,
                "PostCropVignetteMidpoint": 80.0,
                "GrainAmount": 15.0,
            },
        )
        self.assertEqual(len(changes), 5)
        self.assertTrue(any("绘制叠加" in item for item in changes))
        self.assertTrue(
            any(
                item.startswith("PostCropVignetteRoundness 由 100 收敛为 25")
                for item in changes
            )
        )

    def test_tame_vignette_artifacts_leaves_a_soft_vignette_alone(self) -> None:
        clean = {"PostCropVignetteAmount": -40, "PostCropVignetteFeather": 50}

        tamed, changes = tame_vignette_artifacts(clean)

        self.assertEqual(tamed, clean)
        self.assertEqual(changes, [])
        # The caller's own dict is never edited in place.
        self.assertIsNot(tamed, clean)

    def test_registry_covers_every_lightroom_panel(self) -> None:
        payload = develop_controls_payload()
        groups = {group["id"]: group["controls"] for group in payload["groups"]}
        keys = [control["key"] for controls in groups.values() for control in controls]

        self.assertGreaterEqual(len(keys), 100)
        self.assertEqual(len(set(keys)), payload["total"])
        for expected in (
            "Exposure2012",
            "Temperature",
            "ToneCurvePV2012",
            "HueAdjustmentRed",
            "ColorGradeGlobalSat",
            "SplitToningBalance",
            "Sharpness",
            "GrainAmount",
            "PostCropVignetteAmount",
            "AutoLateralCA",
            "PerspectiveScale",
            "AutoTone",
        ):
            self.assertIn(expected, keys)

        exposure = next(control for control in groups["basic"] if control["key"] == "Exposure2012")
        self.assertEqual(exposure["controller"], "Exposure")
        vignette = next(control for control in groups["effects"] if control["key"] == "PostCropVignetteStyle")
        self.assertEqual([choice[0] for choice in vignette["choices"]], [1, 2, 3])
        self.assertTrue(set(payload["model_keys"]).issubset(set(keys)))
        # Version sensitive parameters stay out of the model schema.
        self.assertFalse({"PerspectiveScale", "AutoLateralCA"} & set(payload["model_keys"]))

    def test_ollama_schema_exposes_every_model_key(self) -> None:
        schema = _ollama_response_schema()
        settings = schema["properties"]["settings"]
        properties = settings["properties"]

        self.assertEqual(set(properties), set(MODEL_SETTING_KEYS))
        self.assertGreater(len(properties), 60)
        self.assertEqual(properties["HueAdjustmentRed"]["minimum"], -100.0)
        self.assertFalse(settings["additionalProperties"])

    def test_default_model_keys_skip_experimental_controls_and_the_mask(self) -> None:
        payload = develop_controls_payload()
        controls = [control for group in payload["groups"] for control in group["controls"]]
        expected = {
            str(control["key"])
            for control in controls
            if not control["experimental"] and control["key"] not in MASK_KEYS
        }

        self.assertEqual(set(MODEL_SETTING_KEYS), expected)
        self.assertEqual(set(payload["model_keys"]), expected)
        # The mask (Post Crop vignette) is the one group that ships switched off.
        self.assertEqual(set(payload["mask_keys"]), MASK_KEYS)
        self.assertEqual(len(MASK_KEYS), 5)
        for key in (
            "WhiteBalance",
            "ToneCurveName",
            "AutoTone",
            "ConvertToGrayscale",
            "ToneCurvePV2012",
            "ToneCurvePV2012Red",
        ):
            self.assertIn(key, MODEL_SETTING_KEYS)
        # Version sensitive controls stay opt in.
        self.assertFalse(
            {"AutoLateralCA", "PerspectiveScale", "PerspectiveUpright"} & set(MODEL_SETTING_KEYS)
        )
        # The payload tells the web UI which switches start off, and the mask switch can
        # unlock the vignette per request.
        flags = {str(control["key"]): control["model_default"] for control in controls}
        self.assertFalse(any(flags[key] for key in MASK_KEYS))
        self.assertTrue(flags["Exposure2012"])
        self.assertFalse(flags["PerspectiveScale"])
        # The mask switch unlocks the vignette for that request only, and registry order
        # (basic before effects) is kept.
        unlocked = model_keys_for(["PostCropVignetteAmount", "Vibrance"])
        self.assertEqual(set(unlocked), {"PostCropVignetteAmount", "Vibrance"})
        self.assertLess(unlocked.index("Vibrance"), unlocked.index("PostCropVignetteAmount"))

    def test_model_keys_for_narrows_and_unlocks_controls(self) -> None:
        self.assertEqual(model_keys_for(None), MODEL_SETTING_KEYS)
        # Registry order is kept and unknown names are dropped.
        self.assertEqual(
            model_keys_for(["Vibrance", "Exposure2012", "MadeUpControl"]),
            ("Exposure2012", "Vibrance"),
        )
        # An experimental control the user allowed by hand becomes available again.
        self.assertEqual(model_keys_for(["PerspectiveScale"]), ("PerspectiveScale",))
        self.assertEqual(model_keys_for([]), ())

    def test_ollama_schema_describes_every_value_kind(self) -> None:
        # The mask ships switched off, so its enum is described on a request that
        # unlocks it (the web UI mask switch does exactly that).
        properties = _ollama_response_schema(
            list(MODEL_SETTING_KEYS) + ["PostCropVignetteStyle"]
        )["properties"]["settings"]["properties"]

        self.assertEqual(properties["WhiteBalance"]["type"], "string")
        self.assertIn("Daylight", properties["WhiteBalance"]["enum"])
        self.assertEqual(properties["AutoTone"], {"type": "boolean"})
        self.assertEqual(properties["PostCropVignetteStyle"]["type"], "integer")
        self.assertEqual(properties["PostCropVignetteStyle"]["enum"], [1, 2, 3])
        self.assertEqual(properties["ToneCurvePV2012"]["type"], "string")
        self.assertIn("x,y", properties["ToneCurvePV2012"]["description"])

    def test_ollama_schema_only_exposes_allowed_keys(self) -> None:
        properties = _ollama_response_schema(["Vibrance", "AutoTone"])["properties"]["settings"][
            "properties"
        ]

        self.assertEqual(set(properties), {"Vibrance", "AutoTone"})
        self.assertEqual(properties["AutoTone"], {"type": "boolean"})

    def test_system_prompt_describes_allowed_kinds_only(self) -> None:
        prompt = _build_system_prompt(["Exposure2012", "WhiteBalance", "AutoTone", "ToneCurvePV2012"])

        self.assertIn("Exposure2012", prompt)
        self.assertIn("WhiteBalance = As Shot / Auto", prompt)
        self.assertIn("AutoTone", prompt)
        self.assertIn("x,y", prompt)
        self.assertNotIn("HueAdjustmentRed", prompt)

    def test_system_prompt_asks_for_more_than_the_base_sliders(self) -> None:
        prompt = _build_system_prompt()

        # The old wording capped the answer at the 13 base sliders.
        self.assertNotIn("pick 3 to 8", prompt)
        self.assertNotIn("only add them when the brief clearly asks", prompt)
        self.assertIn("Return 3 to 12 settings", prompt)
        for key in (
            "HueAdjustmentRed",
            "ColorGradeGlobalHue",
            "SplitToningShadowHue",
            "GrainAmount",
            "LuminanceSmoothing",
            "ParametricShadows",
        ):
            self.assertIn(key, prompt)
        # The mask (Post Crop vignette) is off by default, so the vignette keys and their
        # window only show up once the mask switch puts them into the allowed set.
        self.assertNotIn("PostCropVignette", prompt)
        unlocked = _build_system_prompt(["PostCropVignetteAmount"])
        self.assertIn("PostCropVignetteAmount", unlocked)
        self.assertIn("PostCropVignetteRoundness at 25 or below", unlocked)
        # Each panel bullet names the trigger that makes the group worth using.
        self.assertIn("HSL / colour mixer (single colour work", prompt)
        self.assertIn("Detail (a soft photo that needs sharpening", prompt)
        self.assertIn("Colour grading (cinematic", prompt)

    def test_system_prompt_states_the_vignette_window_only_when_the_mask_is_on(self) -> None:
        self.assertNotIn("Post Crop vignette is unlocked", _build_system_prompt())
        self.assertNotIn(
            "Post Crop vignette is unlocked", _build_system_prompt(["Exposure2012", "Vibrance"])
        )
        # Any vignette key in the allowed set means the mask switch is on.
        for key in ("PostCropVignetteAmount", "PostCropVignetteFeather", "PostCropVignetteStyle"):
            self.assertIn("Post Crop vignette is unlocked", _build_system_prompt([key]))

    def test_system_prompt_roles_the_model_as_a_colourist(self) -> None:
        prompt = _build_system_prompt()

        self.assertIn("senior Lightroom Classic colourist", prompt)
        # The working method is spelled out instead of implied.
        self.assertIn("Fix the fundamentals first", prompt)
        self.assertIn("Shape the tone, then place the colour", prompt)
        self.assertIn("Never let one move cancel another", prompt)
        # The plugin writes global sliders only, so local work must not be promised.
        self.assertIn("no masks, no local adjustments, no crop and no healing brush", prompt)
        # The output contract still pins the JSON shape and the Kelvin scale.
        self.assertIn("Answer with one JSON object holding summary and settings", prompt)
        self.assertIn("not a normalized 0-to-1 value or a relative adjustment", prompt)

    def test_system_prompt_hides_groups_without_allowed_keys(self) -> None:
        prompt = _build_system_prompt(["Vibrance", "LuminanceSmoothing"])

        self.assertIn("LuminanceSmoothing", prompt)
        self.assertIn("Detail (a soft photo", prompt)
        for absent in (
            "HueAdjustmentRed",
            "ColorGradeGlobalHue",
            "SplitToningShadowHue",
            "GrainAmount",
            "ParametricShadows",
        ):
            self.assertNotIn(absent, prompt)
        self.assertNotIn("Enum keys accept", prompt)
        self.assertNotIn("Boolean keys answer", prompt)

    def test_system_prompt_scales_the_requested_count_to_the_allowed_set(self) -> None:
        self.assertIn(
            "Return the one setting that matches the brief best.",
            _build_system_prompt(["Vibrance"]),
        )
        self.assertIn(
            "Return 3 to 6 settings",
            _build_system_prompt(
                ["Vibrance", "Saturation", "Exposure2012", "Contrast2012", "Texture", "Dehaze"]
            ),
        )

    def test_system_prompt_explains_reference_parameters_only_for_presets(self) -> None:
        """预设合成出来的「参考参数」块：不是强制规范，但也不能整段忽略。"""
        plain = _build_system_prompt()
        with_reference = _build_system_prompt(reference_params=True)

        self.assertNotIn("reference parameters", plain)
        self.assertIn(PARAMS_HINT_LABEL, with_reference)
        self.assertIn("not as a specification", with_reference)
        self.assertIn("nothing in it is mandatory", with_reference)
        self.assertIn("Do not ignore the block either", with_reference)
        self.assertIn("整体强度", with_reference)
        # 这条规则只往系统提示里加一段话，不会顺手改动允许的键集合。
        self.assertEqual(with_reference.replace(REFERENCE_PARAMS_RULE + "\n", ""), plain)

    def test_parse_suggestion_drops_keys_the_caller_disallowed(self) -> None:
        text = (
            '{"summary":"自然","settings":'
            '{"Exposure2012":0.3,"PerspectiveScale":100,"MadeUpControl":1}}'
        )

        self.assertEqual(_parse_suggestion(text)["settings"], {"Exposure2012": 0.3})
        self.assertEqual(
            _parse_suggestion(text, ["Exposure2012"])["settings"], {"Exposure2012": 0.3}
        )
        self.assertEqual(
            _parse_suggestion(text, ["Exposure2012", "PerspectiveScale"])["settings"],
            {"Exposure2012": 0.3, "PerspectiveScale": 100.0},
        )
        with self.assertRaises(ValueError):
            _parse_suggestion(text, ["Vibrance"])


if __name__ == "__main__":
    unittest.main()
