"""Regression: CUE pregaps must stay intact without next-song audio clicks."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mutagen.flac import FLAC

from cue import parse_cue
from splitter import create_jobs, split_album


def decode_pcm(path: Path) -> bytes:
    return subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-xerror", "-i", str(path), "-f", "s16le", "-",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


class GapBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
    def test_existing_two_second_pregaps_are_preserved_without_end_click(self):
        # 4s tone / 2s silence / 4s different tone / 2s silence / 4s tone.
        # INDEX 01 is at the start of each new tone; output-side FFmpeg -t
        # used to include up to one next-song frame after that index.
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "Album.flac"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
            segments = [
                "sine=frequency=440:sample_rate=44100:duration=4",
                "anullsrc=sample_rate=44100:channel_layout=mono:d=2",
                "sine=frequency=880:sample_rate=44100:duration=4",
                "anullsrc=sample_rate=44100:channel_layout=mono:d=2",
                "sine=frequency=660:sample_rate=44100:duration=4",
            ]
            for segment in segments:
                cmd.extend(["-f", "lavfi", "-i", segment])
            cmd.extend([
                "-filter_complex", "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[a]",
                "-map", "[a]", "-c:a", "flac", str(source),
            ])
            subprocess.run(cmd, check=True)
            cue_path = folder / "Album.cue"
            cue_path.write_text(
                'PERFORMER "Artist"\nTITLE "Album"\nFILE "Album.flac" WAVE\n'
                '  TRACK 01 AUDIO\n    TITLE "One"\n    INDEX 01 00:00:00\n'
                '  TRACK 02 AUDIO\n    TITLE "Two"\n    INDEX 00 00:04:00\n    INDEX 01 00:06:00\n'
                '  TRACK 03 AUDIO\n    TITLE "Three"\n    INDEX 00 00:10:00\n    INDEX 01 00:12:00\n',
                encoding="utf-8",
            )
            cue = parse_cue(cue_path)
            self.assertEqual(cue.tracks[1].pregap_frames, 300)
            self.assertEqual(cue.tracks[2].pregap_frames, 750)
            flac_info = FLAC(source).info
            guard = __import__("math").ceil(
                2 * flac_info.max_blocksize * 75 / flac_info.sample_rate
            )
            jobs = create_jobs(cue, "%n. %t", guard_cd_frames=guard)
            self.assertGreater(guard, 0)
            self.assertLess(jobs[1].start_frames, cue.tracks[1].start_frames)
            self.assertGreater(jobs[1].start_frames, cue.tracks[1].pregap_frames)
            self.assertEqual(jobs[0].end_frames, jobs[1].start_frames)
            self.assertEqual(jobs[1].end_frames, jobs[2].start_frames)

            outputs = split_album(cue, "%n. %t")
            self.assertEqual(len(outputs), 3)
            decoded = [decode_pcm(p) for p in outputs]
            self.assertEqual(b"".join(decoded), decode_pcm(source))
            for index in (0, 1):
                # Last 100ms of previous track must be digital silence.
                self.assertEqual(decoded[index][-8820:], bytes(8820))
            # Both 2s pauses remain intact: decoded audio is bit-identical.
            self.assertEqual(sum(FLAC(p).info.total_samples for p in outputs),
                             flac_info.total_samples)
            if shutil.which("flac"):
                for track in outputs:
                    subprocess.run(["flac", "-t", "-s", str(track)], check=True)

    def test_no_pregap_keeps_original_cue_boundary(self):
        from cue import CueSheet, Track
        cue = CueSheet(
            path=Path("example.cue"), source=Path("example.flac"),
            album="", album_artist="", year="", genre="", comment="",
            tracks=(Track(1, "Song A", "", 0), Track(2, "Song B", "", 450),),
        )
        jobs = create_jobs(cue, "%n. %t", guard_cd_frames=16)
        self.assertEqual(jobs[1].start_frames, 450)
