import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import SettingsValidationError, validate_develop_settings
from app.services.llm import _parse_suggestion


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


if __name__ == "__main__":
    unittest.main()
