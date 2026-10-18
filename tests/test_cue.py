import tempfile
import unittest
from pathlib import Path

from cue import parse_cue


CUE = '''REM GENRE Dance
REM DATE 1999
REM COMMENT "Прекрасный альбом"
PERFORMER "Бомфанк"
TITLE "Метель"
FILE "full.flac" WAVE
  TRACK 01 AUDIO
    TITLE "Съешь ещё"
    PERFORMER "Медведь"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "Вьюга"
    INDEX 00 01:00:00
    INDEX 01 01:02:30
'''


class CueTests(unittest.TestCase):
    def write_cue(self, directory: Path, text: str, encoding: str = "utf-8") -> Path:
        (directory / "full.flac").touch()
        path = directory / "album.cue"
        path.write_bytes(text.encode(encoding))
        return path

    def test_parse_utf8_metadata_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = parse_cue(self.write_cue(Path(tmp), CUE))
            self.assertEqual(cue.album, "Метель")
            self.assertEqual(cue.tracks[0].title, "Съешь ещё")
            self.assertEqual(cue.tracks[1].artist, "Бомфанк")
            self.assertEqual(cue.tracks[1].start_frames, 4680)
            self.assertEqual(cue.comment, "Прекрасный альбом")
            self.assertEqual(cue.genre, "Dance")
            self.assertEqual(cue.year, "1999")

    def test_parse_windows1251(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = parse_cue(self.write_cue(Path(tmp), CUE, "cp1251"))
            self.assertEqual(cue.tracks[0].title, "Съешь ещё")

    def test_parse_utf16(self):
        with tempfile.TemporaryDirectory() as tmp:
            cue = parse_cue(self.write_cue(Path(tmp), CUE, "utf-16"))
            self.assertEqual(cue.album, "Метель")

    def test_ape_source_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "full.ape").touch()
            path = folder / "album.cue"
            path.write_text(CUE.replace("full.flac", "full.ape"), encoding="utf-8")
            cue = parse_cue(path)
            self.assertEqual(cue.source, (folder / "full.ape").resolve())
            self.assertEqual(len(cue.tracks), 2)

    def test_wav_reference_falls_back_to_ape(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "full.ape").touch()
            path = folder / "album.cue"
            path.write_text(CUE.replace("full.flac", "full.wav"), encoding="utf-8")
            self.assertEqual(parse_cue(path).source, (folder / "full.ape").resolve())

    def test_missing_reference_with_both_formats_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "full.flac").touch()
            (folder / "full.ape").touch()
            path = folder / "album.cue"
            path.write_text(CUE.replace("full.flac", "full.wav"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "FLAC, и APE"):
                parse_cue(path)

    def test_reject_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = CUE.replace("  TRACK 02 AUDIO", 'FILE "second.flac" WAVE\n  TRACK 02 AUDIO')
            with self.assertRaisesRegex(ValueError, "одним исходным"):
                parse_cue(self.write_cue(Path(tmp), text))

    def test_reject_missing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = CUE.replace("    INDEX 01 01:02:30", "")
            with self.assertRaisesRegex(ValueError, "INDEX 01"):
                parse_cue(self.write_cue(Path(tmp), text))


if __name__ == "__main__":
    unittest.main()
