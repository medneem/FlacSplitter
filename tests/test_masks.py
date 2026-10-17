import tempfile
import unittest
from pathlib import Path

from cue import CueSheet, Track
from masks import render_mask, validate_mask
from splitter import create_jobs


class MaskTests(unittest.TestCase):
    def setUp(self):
        self.track1 = Track(1, "Мягкий знак ь и съезд", "Медведь", 0)
        self.track2 = Track(2, "Песня/Часть", "Бомфанк", 75)
        self.cue = CueSheet(
            path=Path("album.cue"), source=Path("album.flac"), album="Большой альбом",
            album_artist="Исполнитель", year="1999", genre="Rock", comment="Тест",
            tracks=(self.track1, self.track2),
        )

    def test_all_codes(self):
        mask = "%N - %n - %A - %a - %T - %t - %c - %y - %g %%"
        result = render_mask(mask, self.cue, self.track1)
        self.assertEqual(result,
            "02 - 01 - Исполнитель - Медведь - Большой альбом - Мягкий знак ь и съезд - Тест - 1999 - Rock %.flac")

    def test_cyrillic_soft_sign_not_replaced(self):
        self.assertEqual(render_mask("%t", self.cue, self.track1),
                         "Мягкий знак ь и съезд.flac")

    def test_invalid_win_filename_character(self):
        self.assertEqual(render_mask("%t", self.cue, self.track2), "ПесняЧасть.flac")

    def test_question_mark_is_removed_from_filename(self):
        song = Track(1, "Что мне делать? (Radio Edit)", "Света", 0)
        self.assertEqual(
            render_mask("%n. %a - %t", self.cue, song),
            "01. Света - Что мне делать (Radio Edit).flac",
        )
        # The original CUE title remains unchanged (and is used in FLAC tags).
        self.assertEqual(song.title, "Что мне делать? (Radio Edit)")

    def test_all_forbidden_windows_characters_are_removed(self):
        song = Track(1, 'Назва: /\\*<>|" тест?', "Света", 0)
        filename = render_mask("%t", self.cue, song)
        self.assertEqual(filename, "Назва  тест.flac")
        self.assertNotIn("?", filename)
        self.assertNotIn("_", filename)

    def test_unknown_mask_token(self):
        with self.assertRaises(ValueError):
            validate_mask("%x")

    def test_preserve_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            cue = CueSheet(path=directory / "album.cue", source=directory / "full.flac",
                           album="", album_artist="", year="", genre="", comment="",
                           tracks=(self.track1,))
            (directory / "01.flac").touch()
            with self.assertRaises(FileExistsError):
                create_jobs(cue, "%n")

    def test_case_insensitive_collisions(self):
        tracks = (Track(1, "Test", "", 0), Track(2, "test", "", 75))
        cue = CueSheet(path=Path("album.cue"), source=Path("full.flac"),
                       album="", album_artist="", year="", genre="", comment="", tracks=tracks)
        with self.assertRaisesRegex(ValueError, "одинаковые"):
            create_jobs(cue, "%t")


if __name__ == "__main__":
    unittest.main()
