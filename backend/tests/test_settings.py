import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import (
    MODEL_SETTING_KEYS,
    SettingsValidationError,
    develop_controls_payload,
    model_keys_for,
    validate_develop_settings,
)
from app.services.llm import _build_system_prompt, _ollama_response_schema, _parse_suggestion


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

    def test_default_model_keys_allow_every_non_experimental_control(self) -> None:
        payload = develop_controls_payload()
        controls = [control for group in payload["groups"] for control in group["controls"]]
        expected = {
            str(control["key"]) for control in controls if not control["experimental"]
        }

        self.assertEqual(set(MODEL_SETTING_KEYS), expected)
        self.assertEqual(set(payload["model_keys"]), expected)
        for key in (
            "WhiteBalance",
            "ToneCurveName",
            "PostCropVignetteStyle",
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
        properties = _ollama_response_schema()["properties"]["settings"]["properties"]

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
            "PostCropVignetteAmount",
            "GrainAmount",
            "LuminanceSmoothing",
            "ParametricShadows",
        ):
            self.assertIn(key, prompt)
        # Each panel bullet names the trigger that makes the group worth using.
        self.assertIn("HSL / colour mixer (single colour work", prompt)
        self.assertIn("Detail (a soft photo that needs sharpening", prompt)
        self.assertIn("Colour grading (cinematic", prompt)

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
