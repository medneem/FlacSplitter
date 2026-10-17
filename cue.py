"""Minimal CUE sheet parser for one FLAC image (75 CD frames per second)."""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Track:
    number: int
    title: str
    artist: str
    start_frames: int


@dataclass(frozen=True)
class CueSheet:
    path: Path
    source: Path
    album: str
    album_artist: str
    year: str
    genre: str
    comment: str
    tracks: tuple[Track, ...]


def read_cue_text(path: Path) -> str:
    content = path.read_bytes()
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return content.decode("utf-16")
    if content.startswith(codecs.BOM_UTF8):
        return content.decode("utf-8-sig")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        # Legacy Russian CUE sheets are commonly stored in Windows-1251.
        return content.decode("cp1251")


def cue_value(line: str, keyword: str) -> str | None:
    match = re.match(r"^" + keyword + r'\s+(?:"([^"]*)"|(.*))$', line, re.I)
    if not match:
        return None
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip()


def parse_cue(path: Path) -> CueSheet:
    path = Path(path).resolve()
    text = read_cue_text(path)
    album = artist = year = genre = comment = ""
    file_names: list[str] = []
    tracks: list[dict] = []
    current: dict | None = None
    active_file: str | None = None

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        value = cue_value(line, "FILE")
        if value is not None:
            # FILE also contains an encoding marker (WAVE/BINARY/...).
            match = re.match(r'^(?:"([^"]+)"|(\S+))\s+\S+$', value, re.I)
            if not match:
                raise ValueError(f"Строка {lineno}: неверная директива FILE.")
            active_file = match.group(1) or match.group(2)
            file_names.append(active_file)
            continue

        match = re.match(r"^TRACK\s+(\d+)\s+(\w+)\s*$", line, re.I)
        if match:
            if active_file is None:
                raise ValueError(f"Строка {lineno}: TRACK без FILE.")
            if match.group(2).upper() != "AUDIO":
                raise ValueError("Поддерживаются только аудиотреки (TRACK AUDIO).")
            current = {
                "number": int(match.group(1)),
                "title": "",
                "artist": "",
                "start_frames": None,
                "file": active_file,
            }
            tracks.append(current)
            continue

        match = re.match(r"^INDEX\s+01\s+(\d+):(\d{2}):(\d{2})\s*$", line, re.I)
        if match and current is not None:
            minutes, seconds, frames = map(int, match.groups())
            if seconds >= 60 or frames >= 75:
                raise ValueError(f"Строка {lineno}: неверная метка INDEX 01.")
            if current["start_frames"] is not None:
                raise ValueError(f"Строка {lineno}: повторная метка INDEX 01.")
            current["start_frames"] = (minutes * 60 + seconds) * 75 + frames
            continue

        for keyword in ("TITLE", "PERFORMER"):
            value = cue_value(line, keyword)
            if value is not None:
                if current is not None:
                    current["title" if keyword == "TITLE" else "artist"] = value
                elif keyword == "TITLE":
                    album = value
                else:
                    artist = value
                break
        else:
            match = re.match(r'^REM\s+(GENRE|DATE|YEAR|COMMENT)\s+(.+)$', line, re.I)
            if match and current is None:
                key, value = match.group(1).upper(), match.group(2).strip()
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                if key == "GENRE":
                    genre = value
                elif key in ("DATE", "YEAR"):
                    year = value
                else:
                    comment = value

    if not tracks:
        raise ValueError("CUE не содержит аудиотреков.")
    if len(set(file_names)) != 1 or len({item["file"] for item in tracks}) != 1:
        raise ValueError("Пока поддерживается CUE только с одним исходным FLAC.")
    if any(item["start_frames"] is None for item in tracks):
        raise ValueError("Для каждого трека необходима метка INDEX 01.")
    if len({item["number"] for item in tracks}) != len(tracks):
        raise ValueError("В CUE повторяются номера треков.")
    positions = [item["start_frames"] for item in tracks]
    if any(next_pos <= pos for pos, next_pos in zip(positions, positions[1:])):
        raise ValueError("Метки INDEX 01 должны идти строго по возрастанию.")

    # CUE paths normally use Windows separators even when parsed elsewhere.
    relative_name = file_names[0].replace("\\", "/")
    source_name = Path(relative_name)
    source = (path.parent / source_name).resolve()
    if source.suffix.lower() != ".flac":
        raise ValueError("Первая версия поддерживает только FLAC-файлы.")
    if not source.is_file():
        raise FileNotFoundError(f"Исходный FLAC не найден: {source}")

    return CueSheet(
        path=path,
        source=source,
        album=album,
        album_artist=artist,
        year=year,
        genre=genre,
        comment=comment,
        tracks=tuple(Track(
            number=item["number"],
            title=item["title"],
            artist=item["artist"] or artist,
            start_frames=item["start_frames"],
        ) for item in tracks),
    )
