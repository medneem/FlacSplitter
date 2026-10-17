"""Portable UTF-8 settings stored beside the EXE; never in PyInstaller's temp folder."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from masks import validate_mask

DEFAULT_MASK = "%n. %a - %t"
DEFAULT_LAST_CUE_DIR = ""


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path(directory: Path | None = None) -> Path:
    return (directory or application_directory()) / "config.json"


def load_settings(directory: Path | None = None) -> dict[str, str]:
    path = config_path(directory)
    if not path.exists():
        settings = {"filename_mask": DEFAULT_MASK, "last_cue_dir": DEFAULT_LAST_CUE_DIR}
        save_settings(settings, directory)
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Ошибка чтения config.json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("config.json должен содержать JSON-объект.")
    mask = data.get("filename_mask", DEFAULT_MASK)
    validate_mask(mask)
    last_cue_dir = data.get("last_cue_dir", DEFAULT_LAST_CUE_DIR)
    if not isinstance(last_cue_dir, str):
        raise ValueError("last_cue_dir в config.json должен быть строкой.")
    return {"filename_mask": mask, "last_cue_dir": last_cue_dir}


def save_settings(settings: dict[str, str], directory: Path | None = None) -> None:
    mask = settings["filename_mask"]
    validate_mask(mask)
    path = config_path(directory)
    # Older callers that save only the mask must not discard the last folder.
    last_cue_dir = settings.get("last_cue_dir")
    if last_cue_dir is None:
        last_cue_dir = load_settings(directory)["last_cue_dir"] if path.is_file() else DEFAULT_LAST_CUE_DIR
    if not isinstance(last_cue_dir, str):
        raise ValueError("last_cue_dir должен быть строкой.")
    temp_path = path.with_name(path.name + ".tmp")
    try:
        temp_path.write_text(
            json.dumps({"filename_mask": mask, "last_cue_dir": last_cue_dir},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
