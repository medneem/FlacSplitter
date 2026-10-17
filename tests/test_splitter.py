"""End-to-end audio splitting test when FFmpeg is available."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mutagen.flac import FLAC
from cue import parse_cue
from splitter import split_album


class IntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
    def test_split_flac_utf8_filenames_and_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "Альбом.flac"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:a", "flac", str(source),
            ], check=True)
            cue_path = folder / "Альбом.cue"
            cue_path.write_text(
                'PERFORMER "Исполнитель"\nTITLE "Альбом"\n'
                'FILE "Альбом.flac" WAVE\n'
                'TRACK 01 AUDIO\n  TITLE "Метель ь"\n  INDEX 01 00:00:00\n'
                'TRACK 02 AUDIO\n  TITLE "Вьюга"\n  INDEX 01 00:01:00\n',
                encoding="utf-8",
            )
            outputs = split_album(parse_cue(cue_path), "%n. %a - %t")
            self.assertEqual(len(outputs), 2)
            self.assertEqual(outputs[0].name, "01. Исполнитель - Метель ь.flac")
            self.assertTrue(source.is_file())
            self.assertAlmostEqual(FLAC(outputs[0]).info.length, 1, delta=.04)
            self.assertAlmostEqual(FLAC(outputs[1]).info.length, 2, delta=.04)
            self.assertEqual(FLAC(outputs[0])["title"], ["Метель ь"])
            self.assertEqual(FLAC(outputs[1])["tracknumber"], ["2"])
            with self.assertRaises(FileExistsError):
                split_album(parse_cue(cue_path), "%n. %a - %t")


if __name__ == "__main__":
    unittest.main()
