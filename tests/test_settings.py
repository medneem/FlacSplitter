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
            self.assertEqual(load_settings(path)["last_cue_dir"], "")
            save_settings({"filename_mask": "%n - Метель - %t"}, path)
            text = (path / "config.json").read_text(encoding="utf-8")
            self.assertIn("Метель", text)
            self.assertEqual(load_settings(path)["filename_mask"], "%n - Метель - %t")

    def test_remember_unicode_cue_directory_and_keep_it_when_mask_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            folder = config_dir / "Света - Что мне делать"
            folder.mkdir()
            original = {"filename_mask": DEFAULT_MASK, "last_cue_dir": str(folder)}
            save_settings(original, config_dir)
            self.assertEqual(load_settings(config_dir), original)
            # Updating only the mask (including legacy callers) cannot erase the path.
            save_settings({"filename_mask": "%n - %t"}, config_dir)
            self.assertEqual(load_settings(config_dir)["last_cue_dir"], str(folder))
            self.assertEqual(load_settings(config_dir)["filename_mask"], "%n - %t")
            self.assertEqual(
                json.loads((config_dir / "config.json").read_text(encoding="utf-8")),
                {"filename_mask": "%n - %t", "last_cue_dir": str(folder)},
            )

    def test_legacy_config_without_directory_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "config.json").write_text(
                '{"filename_mask": "%n. %t"}', encoding="utf-8")
            self.assertEqual(
                load_settings(folder),
                {"filename_mask": "%n. %t", "last_cue_dir": ""},
            )

    def test_invalid_directory_type_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            invalid = '{"filename_mask": "%n", "last_cue_dir": 12}'
            (folder / "config.json").write_text(invalid, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "last_cue_dir"):
                load_settings(folder)
            self.assertEqual(
                (folder / "config.json").read_text(encoding="utf-8"), invalid)

    def test_invalid_config_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "config.json").write_text('{"filename_mask":"%x"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(path)
            self.assertIn("%x", (path / "config.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
