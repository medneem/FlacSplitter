"""Medieval-compatible filename mask tokens, without lossy Unicode substitutions."""

from __future__ import annotations

import re
import unicodedata

from cue import CueSheet, Track

TOKEN_HELP = {
    "%N": "Количество треков",
    "%n": "Номер текущего трека",
    "%A": "Исполнитель альбома",
    "%a": "Исполнитель трека",
    "%T": "Название альбома",
    "%t": "Название песни",
    "%c": "Комментарий / описание альбома",
    "%y": "Год выпуска",
    "%g": "Жанр",
    "%%": "Знак процента",
}
INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def validate_mask(mask: str) -> None:
    if not isinstance(mask, str) or not mask.strip():
        raise ValueError("Маска не может быть пустой.")
    pos = 0
    while pos < len(mask):
        if mask[pos] != "%":
            pos += 1
            continue
        if pos + 1 >= len(mask) or mask[pos:pos + 2] not in TOKEN_HELP:
            raise ValueError(f"Неизвестная переменная маски: {mask[pos:pos + 2]!r}")
        pos += 2


def sanitize_filename(name: str) -> str:
    # ASCII ? is forbidden in Windows filenames; fullwidth ？ (U+FF1F) is allowed.
    # Use NFC (not NFKC), so the visually similar question mark stays intact.
    name = unicodedata.normalize("NFC", name).replace("?", "？")
    name = INVALID_WINDOWS.sub("_", name).strip().rstrip(". ")
    if not name:
        raise ValueError("Имя файла после применения маски оказалось пустым.")
    if WINDOWS_RESERVED.match(name):
        name = "_" + name
    # Leave room for .flac and for long paths on Windows.
    return name[:180].rstrip(". ") + ".flac"


def render_mask(mask: str, cue: CueSheet, track: Track) -> str:
    validate_mask(mask)
    values = {
        "%N": str(len(cue.tracks)).zfill(2),
        "%n": str(track.number).zfill(2),
        "%A": cue.album_artist,
        "%a": track.artist or cue.album_artist,
        "%T": cue.album,
        "%t": track.title or f"Track {track.number:02d}",
        "%c": cue.comment,
        "%y": cue.year,
        "%g": cue.genre,
        "%%": "%",
    }
    output: list[str] = []
    pos = 0
    while pos < len(mask):
        if mask[pos] == "%":
            output.append(values[mask[pos:pos + 2]])
            pos += 2
        else:
            output.append(mask[pos])
            pos += 1
    return sanitize_filename("".join(output))
