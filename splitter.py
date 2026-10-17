"""FLAC stream-copy slicing with FFmpeg and metadata repair using Mutagen."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mutagen.flac import FLAC

from cue import CueSheet, Track
from masks import render_mask


class SplitCancelled(Exception):
    pass


@dataclass(frozen=True)
class SplitJob:
    track: Track
    destination: Path
    start_frames: int
    end_frames: int | None


def create_jobs(cue: CueSheet, mask: str) -> list[SplitJob]:
    jobs: list[SplitJob] = []
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
            start_frames=track.start_frames,
            end_frames=cue.tracks[index + 1].start_frames if index + 1 < len(cue.tracks) else None,
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


def set_flac_tags(output: Path, source: FLAC, cue: CueSheet, track: Track) -> None:
    target = FLAC(output)
    target.clear()
    tags = {
        "TITLE": track.title or f"Track {track.number:02d}",
        "ARTIST": track.artist or cue.album_artist,
        "ALBUM": cue.album,
        "ALBUMARTIST": cue.album_artist,
        "TRACKNUMBER": str(track.number),
        "TRACKTOTAL": str(len(cue.tracks)),
        "DATE": cue.year or (source.get("date", [""])[0]),
        "GENRE": cue.genre or (source.get("genre", [""])[0]),
        "COMMENT": cue.comment or (source.get("comment", [""])[0]),
    }
    for key, value in tags.items():
        if value:
            target[key] = [value]
    for picture in source.pictures:
        target.add_picture(picture)
    target.save()



def repair_streaminfo(path: Path, ffmpeg: str) -> None:
    """Correct copied FLAC STREAMINFO duration and invalidate album MD5.

    Count samples by reading packet durations from FFmpeg framecrc output,
    using stream-copy without decoding/recompressing PCM.
    FLAC's zero MD5 means unknown; individual FLAC frames retain their CRC.
    """
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-f", "framecrc", "-",
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=flags, check=False,
    )
    if process.returncode:
        details = process.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"Не удалось проверить FLAC: {details}")
    total_samples = 0
    packets = 0
    for line in process.stdout.decode("utf-8", "replace").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split(",")
        if len(fields) < 6:
            raise ValueError("Неожиданный формат аудиофреймов FFmpeg.")
        try:
            duration = int(fields[3].strip())
        except ValueError as exc:
            raise ValueError("Не удалось прочитать длительность FLAC-фрейма.") from exc
        if duration <= 0:
            raise ValueError("Обнаружен FLAC-фрейм без длительности.")
        total_samples += duration
        packets += 1
    if not packets or not (0 < total_samples < (1 << 36)):
        raise ValueError("Не удалось подсчитать аудиосэмплы выходного FLAC.")

    with path.open("r+b") as output:
        if output.read(4) != b"fLaC":
            raise ValueError("Выходной файл не содержит заголовок FLAC.")
        header = output.read(4)
        if len(header) != 4 or (header[0] & 0x7f) != 0 or int.from_bytes(header[1:4], "big") != 34:
            raise ValueError("Выходной FLAC имеет неожиданную структуру STREAMINFO.")
        # STREAMINFO: 10 bytes block/frame sizes, then 20+3+5+36-bit packed fields.
        output.seek(18)
        data = output.read(8)
        if len(data) != 8:
            raise ValueError("Повреждён заголовок STREAMINFO.")
        packed = int.from_bytes(data, "big")
        updated = (packed & ~((1 << 36) - 1)) | total_samples
        output.seek(18)
        output.write(updated.to_bytes(8, "big"))
        # Source album's MD5 is invalid for individual tracks; 0 is legal.
        output.write(bytes(16))


def split_album(
    cue: CueSheet,
    mask: str,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Path]:
    jobs = create_jobs(cue, mask)
    source = FLAC(cue.source)
    duration = source.info.length
    if duration <= 0:
        raise ValueError("Не удалось определить длительность исходного FLAC.")
    if cue.tracks[-1].start_frames / 75 >= duration:
        raise ValueError("Последний трек начинается за пределами исходного FLAC.")
    ffmpeg = find_ffmpeg()
    completed: list[Path] = []
    total = len(jobs)
    for index, job in enumerate(jobs):
        if cancel is not None and cancel.is_set():
            raise SplitCancelled("Разделение отменено пользователем.")
        temporary = temporary_path(job.destination)
        if progress:
            progress(index, total, f"Обработка {index + 1}/{total}: {job.destination.name}")
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-i", str(cue.source),
            # Output-side seeking drops whole frames before the CUE boundary.
            # Input-side -ss with stream-copy could duplicate boundary frames.
            "-ss", seconds(job.start_frames),
        ]
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
            repair_streaminfo(temporary, ffmpeg)
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
