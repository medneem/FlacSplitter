"""Minimal Russian-language desktop UI for FlacSplitter."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView,
)

from cue import CueSheet, parse_cue
from masks import TOKEN_HELP, render_mask, validate_mask
from settings import DEFAULT_MASK, config_path, load_settings, save_settings
from splitter import SplitCancelled, create_jobs, split_album
from version import APP_VERSION

APP_NAME = "FlacSplitter"


def application_icon() -> QIcon:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    renderer = QSvgRenderer(str(base / "assets" / "app.svg"))
    if not renderer.isValid():
        return QIcon()
    image = QPixmap(256, 256)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(image)


class SplitWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, cue: CueSheet, mask: str):
        super().__init__()
        self.cue = cue
        self.mask = mask
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        def report(done: int, total: int, message: str) -> None:
            self.progress.emit(round(done * 100 / total), message)
        try:
            outputs = split_album(self.cue, self.mask, report, self.cancel_event)
        except SplitCancelled as exc:
            self.finished.emit(False, str(exc))
        except Exception as exc:
            self.finished.emit(False, f"Ошибка: {exc}")
        else:
            self.finished.emit(True, f"Готово! Создано файлов: {len(outputs)}.")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(960, 550)
        self.setMinimumSize(730, 450)
        self.setAcceptDrops(True)
        try:
            self.settings = load_settings()
        except (ValueError, OSError) as exc:
            self.settings = {"filename_mask": DEFAULT_MASK}
            QMessageBox.warning(self, "Ошибка config.json",
                f"{exc}\n\nЗагружена маска по умолчанию. Исходный config.json не изменён.")

        self.cue: CueSheet | None = None
        self.thread: QThread | None = None
        self.worker: SplitWorker | None = None

        root = QWidget()
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        heading = QLabel("Разделение FLAC по CUE")
        heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(heading, 1)
        header.addWidget(QLabel(f"v{APP_VERSION}"))
        layout.addLayout(header)

        file_row = QHBoxLayout()
        self.cue_name = QLineEdit()
        self.cue_name.setReadOnly(True)
        self.cue_name.setPlaceholderText("Выберите CUE-файл или перетащите его в окно")
        self.open_button = QPushButton("Открыть CUE…")
        self.open_button.clicked.connect(self.choose_cue)
        file_row.addWidget(self.cue_name, 1)
        file_row.addWidget(self.open_button)
        layout.addLayout(file_row)

        mask_row = QHBoxLayout()
        mask_row.addWidget(QLabel("Маска имени файла:"))
        self.mask_edit = QLineEdit(self.settings["filename_mask"])
        self.mask_edit.setPlaceholderText(DEFAULT_MASK)
        self.mask_edit.textChanged.connect(self.refresh_preview)
        self.mask_edit.editingFinished.connect(self.save_mask)
        mask_row.addWidget(self.mask_edit, 1)
        help_button = QPushButton("Справка по маске")
        help_button.clicked.connect(self.show_mask_help)
        mask_row.addWidget(help_button)
        layout.addLayout(mask_row)

        self.album_label = QLabel("Альбом не выбран.")
        layout.addWidget(self.album_label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["№", "Исполнитель", "Название", "Выходной FLAC"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        self.status = QLabel("Откройте CUE для начала работы.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.folder_button = QPushButton("Открыть папку")
        self.folder_button.clicked.connect(self.open_folder)
        self.folder_button.setEnabled(False)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self.cancel_split)
        self.cancel_button.setEnabled(False)
        self.split_button = QPushButton("Разделить")
        self.split_button.setDefault(True)
        self.split_button.clicked.connect(self.start_split)
        self.split_button.setEnabled(False)
        buttons.addWidget(self.folder_button)
        buttons.addStretch()
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.split_button)
        layout.addLayout(buttons)
        self.statusBar().showMessage(f"Конфигурация: {config_path()}")

    def choose_cue(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл CUE", str(Path.home()), "CUE sheets (*.cue);;Все файлы (*)")
        if file_name:
            self.open_cue(Path(file_name))

    def open_cue(self, path: Path) -> None:
        try:
            cue = parse_cue(path)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось открыть CUE", str(exc))
            return
        self.cue = cue
        self.cue_name.setText(str(cue.path))
        self.album_label.setText(
            f"Альбом: {cue.album or 'Без названия'}"
            f"    |    Треков: {len(cue.tracks)}"
            f"    |    FLAC: {cue.source.name}"
        )
        self.folder_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.refresh_preview()

    def refresh_preview(self) -> None:
        if self.thread is not None:
            return
        if self.cue is None:
            self.split_button.setEnabled(False)
            return
        self.table.setRowCount(len(self.cue.tracks))
        try:
            validate_mask(self.mask_edit.text())
            for row, track in enumerate(self.cue.tracks):
                cells = [
                    f"{track.number:02d}",
                    track.artist,
                    track.title,
                    render_mask(self.mask_edit.text(), self.cue, track),
                ]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if col == 0:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(row, col, item)
            self.split_button.setEnabled(True)
            self.status.setText("Готово к разделению. Файлы будут сохранены рядом с CUE.")
        except ValueError as exc:
            self.split_button.setEnabled(False)
            self.status.setText(str(exc))

    def save_mask(self) -> bool:
        latest = {"filename_mask": self.mask_edit.text()}
        try:
            save_settings(latest)
        except (ValueError, OSError) as exc:
            self.status.setText(f"Маска не сохранена: {exc}")
            return False
        self.settings = latest
        return True

    def show_mask_help(self) -> None:
        lines = [f"{key} — {value}" for key, value in TOKEN_HELP.items()]
        QMessageBox.information(
            self, "Справка по маскам",
            "Переменные для имён FLAC:\n\n" + "\n".join(lines)
            + "\n\nПример: %n. %a - %t"
            + "\nФайлы автоматически сохраняются в папку открытого CUE."
            + "\nНедопустимые символы Windows заменяются на _."
        )

    def start_split(self) -> None:
        if self.cue is None or self.thread is not None:
            return
        if not self.save_mask():
            QMessageBox.warning(self, "Неверная маска", self.status.text())
            return
        try:
            create_jobs(self.cue, self.mask_edit.text())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Невозможно разделить", str(exc))
            return
        self.progress_bar.setValue(0)
        self.open_button.setEnabled(False)
        self.mask_edit.setEnabled(False)
        self.split_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.thread = QThread(self)
        self.worker = SplitWorker(self.cue, self.mask_edit.text())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.on_thread_stopped)
        self.thread.start()

    def on_progress(self, progress: int, text: str) -> None:
        self.progress_bar.setValue(progress)
        self.status.setText(text)

    def on_finished(self, success: bool, text: str) -> None:
        self.status.setText(text)
        if success:
            self.progress_bar.setValue(100)
        else:
            QMessageBox.warning(self, "FlacSplitter", text)

    def on_thread_stopped(self) -> None:
        self.worker = None
        self.thread = None
        self.open_button.setEnabled(True)
        self.mask_edit.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.refresh_preview()

    def cancel_split(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("Отмена…")

    def open_folder(self) -> None:
        if self.cue is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.cue.path.parent)))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() and url.toLocalFile().lower().endswith(".cue")
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if self.thread is not None:
            return
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".cue"):
                self.open_cue(Path(url.toLocalFile()))
                event.acceptProposedAction()
                return

    def closeEvent(self, event) -> None:
        if self.thread is not None:
            self.cancel_split()
            QMessageBox.information(self, "Подождите", "Дождитесь завершения отмены.")
            event.ignore()
        else:
            super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(application_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
