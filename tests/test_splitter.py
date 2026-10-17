"""End-to-end audio splitting test when FFmpeg is available."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mutagen.flac import FLAC
from cue import parse_cue
from splitter import split_album, create_jobs



def compressed_frame_checksums(path: Path) -> list[str]:
    """Read FFmpeg packet CRCs without decoding or recompressing."""
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-f", "framecrc", "-",
    ], capture_output=True, text=True, check=True)
    return [
        line.split(",")[5].strip()
        for line in result.stdout.splitlines()
        if line and not line.startswith("#")
    ]


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
            self.assertAlmostEqual(FLAC(outputs[0]).info.length, 1, delta=.12)
            self.assertAlmostEqual(FLAC(outputs[1]).info.length, 2, delta=.12)
            self.assertEqual(FLAC(outputs[0])["title"], ["Метель ь"])
            self.assertEqual(FLAC(outputs[1])["tracknumber"], ["2"])
            # STREAMINFO must describe each track, not the whole source album.
            self.assertAlmostEqual(
                FLAC(outputs[0]).info.length + FLAC(outputs[1]).info.length, 3, delta=.001)
            self.assertEqual(FLAC(outputs[0]).info.md5_signature, 0)
            self.assertEqual(FLAC(outputs[1]).info.md5_signature, 0)
            # Compressed audio packets must be copied unchanged, in order.
            self.assertEqual(
                compressed_frame_checksums(source),
                compressed_frame_checksums(outputs[0]) + compressed_frame_checksums(outputs[1]),
            )
            with self.assertRaises(FileExistsError):
                split_album(parse_cue(cue_path), "%n. %a - %t")


    def test_first_track_starts_at_cue_index(self):
        from cue import CueSheet, Track
        cue = CueSheet(
            path=Path("album.cue"), source=Path("full.flac"),
            album="", album_artist="", year="", genre="", comment="",
            tracks=(Track(1, "First", "", 150), Track(2, "Second", "", 750)),
        )
        jobs = create_jobs(cue, "%n. %t")
        self.assertEqual(jobs[0].start_frames, 150)
        self.assertEqual(jobs[0].end_frames, 750)
        self.assertEqual(jobs[1].start_frames, 750)



if __name__ == "__main__":
    unittest.main()
