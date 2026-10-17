"""GUI smoke tests run headlessly on Windows GitHub Actions."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cue import CueSheet, Track
from main import MainWindow, open_output_file


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        with patch("main.load_settings", return_value={"filename_mask": "%n. %a - %t"}):
            self.window = MainWindow()
        self.addCleanup(self.window.close)
        self.cue = CueSheet(
            path=self.folder / "Альбом.cue", source=self.folder / "Альбом.flac",
            album="Альбом", album_artist="Света",
            year="2001", genre="", comment="",
            tracks=(Track(1, "Что мне делать? (Radio Edit)", "Света", 0),),
        )
        self.window.cue = self.cue
        self.window.refresh_preview()

    def test_every_table_header_is_bold(self):
        header = self.window.table
        for column in range(header.columnCount()):
            with self.subTest(column=column):
                self.assertTrue(header.horizontalHeaderItem(column).font().bold())

    def test_existing_track_launches_default_player(self):
        item = self.window.table.item(0, 3)
        self.assertEqual(
            item.text(), "01. Света - Что мне делать？ (Radio Edit).flac")
        destination = self.folder / item.text()
        destination.write_bytes(b"fake FLAC; file existence is sufficient for dispatch")
        self.window.refresh_preview()
        item = self.window.table.item(0, 3)
        self.assertTrue(item.font().underline())
        self.assertEqual(Path(item.data(Qt.ItemDataRole.UserRole)), destination)
        with patch("main.open_output_file", return_value=True) as launch:
            self.window.table.itemClicked.emit(item)
        launch.assert_called_once_with(destination)

    def test_missing_track_does_not_launch_player(self):
        item = self.window.table.item(0, 3)
        self.assertFalse((self.folder / item.text()).exists())
        with patch("main.open_output_file") as launch, \
                patch("main.QMessageBox.information") as info:
            self.window.table.itemClicked.emit(item)
        launch.assert_not_called()
        info.assert_called_once()
        self.assertIn("ещё не существует", info.call_args.args[2])

    def test_click_other_column_does_not_launch_player(self):
        with patch("main.open_output_file") as launch, \
                patch("main.QMessageBox.information") as info:
            self.window.table.itemClicked.emit(self.window.table.item(0, 2))
        launch.assert_not_called()
        info.assert_not_called()

    def test_player_fails_gracefully(self):
        item = self.window.table.item(0, 3)
        destination = self.folder / item.text()
        destination.touch()
        self.window.refresh_preview()
        with patch("main.open_output_file", return_value=False), \
                patch("main.QMessageBox.warning") as warning:
            self.window.table.itemClicked.emit(self.window.table.item(0, 3))
        warning.assert_called_once()

    def test_launch_uses_local_file_url_with_unicode(self):
        destination = self.folder / "Что мне делать？.flac"
        with patch("main.QDesktopServices") as service:
            service.openUrl.return_value = True
            self.assertTrue(open_output_file(destination))
            sent_url = service.openUrl.call_args.args[0]
            self.assertTrue(sent_url.isLocalFile())
            self.assertEqual(Path(sent_url.toLocalFile()), destination.resolve())


if __name__ == "__main__":
    unittest.main()
