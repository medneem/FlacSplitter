import json
import tempfile
import unittest
from pathlib import Path

from settings import DEFAULT_MASK, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_default_created_and_utf8_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            self.assertEqual(load_settings(path)["filename_mask"], DEFAULT_MASK)
            save_settings({"filename_mask": "%n - Метель - %t"}, path)
            text = (path / "config.json").read_text(encoding="utf-8")
            self.assertIn("Метель", text)
            self.assertEqual(load_settings(path)["filename_mask"], "%n - Метель - %t")

    def test_invalid_config_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "config.json").write_text('{"filename_mask":"%x"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)
            self.assertIn("%x", (path / "config.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
