"""End-to-end audio splitting test when FFmpeg is available."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mutagen.flac import FLAC
from cue import parse_cue
from splitter import split_album, create_jobs



def compressed_audio_payloads(path: Path) -> list[bytes]:
    """Inspect entropy-coded data, excluding editable FLAC frame headers/CRCs."""
    from flac_frames import _metadata_end, _frame_header_length, _packets
    packets = _packets(shutil.which("ffmpeg"), path)
    with path.open("rb") as file:
        _metadata_end(file)
        payloads = []
        for size, _ in packets:
            frame = file.read(size)
            _, header_end = _frame_header_length(frame)
            payloads.append(frame[header_end + 1:-2])
        return payloads


def decoded_pcm(path: Path) -> bytes:
    """Decode for validation ONLY; production does not decode/re-encode."""
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror",
        "-i", str(path), "-f", "s16le", "-",
    ], capture_output=True, check=True)
    return result.stdout


def decoded_frame_numbers(path: Path) -> list[int]:
    from flac_frames import _metadata_end, _frame_header_length, _packets
    packets = _packets(shutil.which("ffmpeg"), path)
    indexes = []
    with path.open("rb") as file:
        _metadata_end(file)
        for size, _ in packets:
            frame = file.read(size)
            width, _ = _frame_header_length(frame)
            lead = frame[4]
            number = lead if width == 1 else lead & ((1 << (7 - width)) - 1)
            for next_byte in frame[5:4 + width]:
                number = (number << 6) | (next_byte & 0x3f)
            indexes.append(number)
    return indexes



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
            # The compressed audio payload, excluding frame headers, is unchanged.
            self.assertEqual(
                compressed_audio_payloads(source),
                compressed_audio_payloads(outputs[0]) + compressed_audio_payloads(outputs[1]),
            )
            self.assertEqual(decoded_pcm(source), decoded_pcm(outputs[0]) + decoded_pcm(outputs[1]))
            for output in outputs:
                self.assertEqual(
                    decoded_frame_numbers(output),
                    list(range(len(decoded_frame_numbers(output)))),
                )
            with self.assertRaises(FileExistsError):
                split_album(parse_cue(cue_path), "%n. %a - %t")


    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
    def test_multiple_long_tracks_keep_full_audio_and_valid_frame_numbers(self):
        # Frame numbers become multibyte over 127; the older stream-copy
        # implementation kept original album indexes and confused players.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "Long.flac"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=523:duration=35",
                "-c:a", "flac", str(source),
            ], check=True)
            cue_path = folder / "Long.cue"
            cue_path.write_text(
                'PERFORMER "Тест"\nTITLE "Тестовый альбом"\n'
                'FILE "Long.flac" WAVE\n'
                'TRACK 01 AUDIO\n  TITLE "Первый?"\n  INDEX 01 00:00:00\n'
                'TRACK 02 AUDIO\n  TITLE "Второй"\n  INDEX 01 00:08:00\n'
                'TRACK 03 AUDIO\n  TITLE "Третий"\n  INDEX 01 00:17:00\n'
                'TRACK 04 AUDIO\n  TITLE "Четвертый"\n  INDEX 01 00:26:00\n',
                encoding="utf-8",
            )
            outputs = split_album(parse_cue(cue_path), "%n. %a - %t")
            self.assertEqual(len(outputs), 4)
            self.assertEqual(outputs[0].name, "01. Тест - Первый.flac")
            self.assertTrue(all(FLAC(output).info.length > 7 for output in outputs))
            self.assertEqual(
                compressed_audio_payloads(source),
                [payload for output in outputs for payload in compressed_audio_payloads(output)],
            )
            self.assertEqual(
                decoded_pcm(source),
                b"".join(decoded_pcm(output) for output in outputs),
            )
            for output in outputs:
                indexes = decoded_frame_numbers(output)
                self.assertEqual(indexes, list(range(len(indexes))))
                if shutil.which("flac"):
                    subprocess.run(["flac", "-t", "-s", str(output)], check=True)


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
