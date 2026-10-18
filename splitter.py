"""FLAC stream-copy or APE transcoding with FFmpeg and Mutagen metadata."""

from __future__ import annotations

import shutil
import math
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mutagen.apev2 import APEBinaryValue
from mutagen.flac import FLAC, Picture
from mutagen.monkeysaudio import MonkeysAudio

from cue import CueSheet, Track
from flac_frames import normalize_copied_flac
from masks import render_mask


class SplitCancelled(Exception):
    pass


@dataclass(frozen=True)
class SplitJob:
    track: Track
    destination: Path
    start_frames: int
    end_frames: int | None


def create_jobs(cue: CueSheet, mask: str, *, guard_cd_frames: int = 0) -> list[SplitJob]:
    """Plan lossless frame-copy cuts, keeping all audio and CUE pregaps.

    Output-side FFmpeg trim can include a whole frame starting before INDEX 01,
    but containing a brief beginning of the next song. On discs with pregaps,
    move the cut back by up to two FLAC blocks, *within the existing pregap*.
    No audio is deleted; a small tail of the pause belongs to the next file.
    """
    jobs: list[SplitJob] = []
    starts = [track.start_frames for track in cue.tracks]
    for index, track in enumerate(cue.tracks):
        if (index > 0 and guard_cd_frames > 0
                and track.pregap_frames is not None
                and track.start_frames - track.pregap_frames >= 2 * guard_cd_frames):
            starts[index] = track.start_frames - guard_cd_frames
    used_names: set[str] = set()
    for index, track in enumerate(cue.tracks):
        filename = render_mask(mask, cue, track)
        dest = cue.path.parent / filename
        if filename.casefold() in used_names:
            raise ValueError(f"Маска создаёт одинаковые имена файлов: {filename}")
        used_names.add(filename.casefold())
        if dest.exists() or temporary_path(dest).exists():
            raise FileExistsError(f"Файл уже существует: {dest}")
        jobs.append(SplitJob(
            track=track,
            destination=dest,
            start_frames=starts[index],
            end_frames=starts[index + 1] if index + 1 < len(cue.tracks) else None,
        ))
    return jobs


def temporary_path(destination: Path) -> Path:
    return destination.with_name(destination.stem + ".part.flac")


def find_ffmpeg() -> str:
    directory = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    for candidate in (directory / "ffmpeg.exe", directory / "ffmpeg"):
        if candidate.is_file():
            return str(candidate)
    result = shutil.which("ffmpeg")
    if result:
        return result
    raise FileNotFoundError("FFmpeg не найден. Поместите ffmpeg.exe рядом с программой.")


def seconds(frames: int) -> str:
    return f"{frames / 75:.9f}"


def source_tag(source: FLAC | MonkeysAudio, key: str) -> str:
    """Read a text tag from either FLAC/Vorbis or Monkey's Audio/APEv2."""
    value = source.get(key)
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value).split("\x00", 1)[0] if value is not None else ""


def set_flac_tags(output: Path, source: FLAC | MonkeysAudio, cue: CueSheet, track: Track) -> None:
    target = FLAC(output)
    target.clear()
    tags = {
        "TITLE": track.title or f"Track {track.number:02d}",
        "ARTIST": track.artist or cue.album_artist,
        "ALBUM": cue.album,
        "ALBUMARTIST": cue.album_artist,
        "TRACKNUMBER": str(track.number),
        "TRACKTOTAL": str(len(cue.tracks)),
        "DATE": cue.year or source_tag(source, "date") or source_tag(source, "year"),
        "GENRE": cue.genre or source_tag(source, "genre"),
        "COMMENT": cue.comment or source_tag(source, "comment"),
    }
    for key, value in tags.items():
        if value:
            target[key] = [value]
    if isinstance(source, FLAC):
        for picture in source.pictures:
            target.add_picture(picture)
    else:
        # APEv2 embeds cover images as filename\0image bytes.
        for key, kind in (("Cover Art (Front)", 3), ("Cover Art (Back)", 4)):
            item = source.get(key)
            if not isinstance(item, APEBinaryValue):
                continue
            _, separator, data = bytes(item).partition(b"\x00")
            if not separator or not data:
                continue
            picture = Picture()
            picture.type = kind
            picture.data = data
            if data.startswith(b"\xff\xd8\xff"):
                picture.mime = "image/jpeg"
            elif data.startswith(b"\x89PNG\r\n\x1a\n"):
                picture.mime = "image/png"
            else:
                continue  # Unknown art type: do not create an invalid FLAC picture.
            target.add_picture(picture)
    target.save()


def split_album(
    cue: CueSheet,
    mask: str,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Path]:
    is_ape = cue.source.suffix.lower() == ".ape"
    source = MonkeysAudio(cue.source) if is_ape else FLAC(cue.source)
    sample_rate = source.info.sample_rate
    if sample_rate <= 0:
        raise ValueError("Не удалось определить частоту дискретизации аудио.")
    # The stream-copy guard is needed ONLY for FLAC: encoded APE cuts
    # use exact sample positions and must not move the INDEX 01 boundary.
    guard = 0
    if not is_ape:
        guard = math.ceil(2 * source.info.max_blocksize * 75 / sample_rate)
    jobs = create_jobs(cue, mask, guard_cd_frames=guard)
    duration = source.info.length
    if duration <= 0:
        raise ValueError("Не удалось определить длительность исходного аудиофайла.")
    if cue.tracks[-1].start_frames / 75 >= duration:
        raise ValueError("Последний трек начинается за пределами исходного аудиофайла.")
    ffmpeg = find_ffmpeg()
    completed: list[Path] = []
    total = len(jobs)
    for index, job in enumerate(jobs):
        if cancel is not None and cancel.is_set():
            raise SplitCancelled("Разделение отменено пользователем.")
        temporary = temporary_path(job.destination)
        if progress:
            progress(index, total, f"Обработка {index + 1}/{total}: {job.destination.name}")
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
        if is_ape:
            # Seek a few whole seconds before the cut for speed. FFmpeg's
            # accurate input-side seek decodes/discards up to that second;
            # atrim then cuts on the exact sample, not on codec frames.
            anchor = max(0, job.start_frames // 75 - 5)
            start_sample = (job.start_frames * sample_rate + 37) // 75 - anchor * sample_rate
            trim = f"atrim=start_sample={start_sample}"
            if job.end_frames is not None:
                end_sample = (job.end_frames * sample_rate + 37) // 75 - anchor * sample_rate
                trim += f":end_sample={end_sample}"
            command.extend([
                "-ss", str(anchor), "-i", str(cue.source),
                "-map", "0:a:0", "-map_metadata", "-1", "-vn", "-sn", "-dn",
                "-af", trim + ",asetpts=PTS-STARTPTS",
                "-c:a", "flac", "-compression_level", "8", str(temporary),
            ])
        else:
            command.extend([
                "-i", str(cue.source),
                # Stream-copy uses output-side -ss to avoid duplicated frames.
                "-ss", seconds(job.start_frames),
            ])
            if job.end_frames is not None:
                command.extend(["-t", seconds(job.end_frames - job.start_frames)])
            command.extend([
                "-map", "0:a:0", "-map_metadata", "-1", "-vn", "-sn", "-dn",
                "-c:a", "copy", str(temporary),
            ])
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, creationflags=flags,
            )
            while True:
                try:
                    _, errors = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        process.terminate()
                        try:
                            process.communicate(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
                        raise SplitCancelled("Разделение отменено пользователем.")
            if process.returncode:
                message = errors.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"FFmpeg: {message or 'ошибка кодирования'}")
            set_flac_tags(temporary, source, cue, job.track)
            if not is_ape:
                normalize_copied_flac(temporary, ffmpeg)
            # Preflight happens before processing, but protect against late races.
            if job.destination.exists():
                raise FileExistsError(f"Файл появился во время обработки: {job.destination}")
            temporary.rename(job.destination)
            completed.append(job.destination)
        finally:
            temporary.unlink(missing_ok=True)
        if progress:
            progress(index + 1, total, f"Готово {index + 1}/{total}: {job.destination.name}")
    return completed
