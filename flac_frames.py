"""Make a stream-copied FLAC independently playable without re-encoding audio.

Only the FLAC frame number, its header CRC8 and frame CRC16, and the
STREAMINFO sample count/MD5 are rewritten; compressed subframes stay intact.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class FlacFrameError(ValueError):
    """Unsupported or inconsistent FLAC framing."""


def _make_crc16_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ (0x8005 if crc & 0x8000 else 0)) & 0xffff
        table.append(crc)
    return tuple(table)


_CRC16_TABLE = _make_crc16_table()


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xffff) ^ _CRC16_TABLE[(crc >> 8) ^ byte]
    return crc


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 0xff
    return crc


def _apply(matrix: tuple[int, ...], value: int) -> int:
    result = 0
    while value:
        bit = value & -value
        result ^= matrix[bit.bit_length() - 1]
        value ^= bit
    return result


def _advance_crc16(crc: int, zero_bytes: int) -> int:
    """Shift a CRC16 across N zero bytes in logarithmic time."""
    matrix = tuple(
        _CRC16_TABLE[bit >> 8] if bit >= 256 else bit << 8
        for bit in (1 << i for i in range(16))
    )
    while zero_bytes:
        if zero_bytes & 1:
            crc = _apply(matrix, crc)
        matrix = tuple(_apply(matrix, column) for column in matrix)
        zero_bytes >>= 1
    return crc


def _encode_number(number: int) -> bytes:
    """FLAC's UTF-8-like unsigned frame/sample index (36-bit maximum)."""
    if number < 0 or number >= 1 << 36:
        raise FlacFrameError("Номер FLAC-фрейма выходит за допустимый диапазон.")
    if number < 128:
        return bytes([number])
    for width, bits in ((2, 11), (3, 16), (4, 21), (5, 26), (6, 31), (7, 36)):
        if number < 1 << bits:
            break
    continuation = bytearray(width - 1)
    for i in range(width - 2, -1, -1):
        continuation[i] = 0x80 | (number & 0x3f)
        number >>= 6
    return bytes([((0xff << (8 - width)) & 0xff) | number]) + continuation


def _frame_header_length(frame: bytes) -> tuple[int, int]:
    """Return (frame number byte count, index of header CRC8)."""
    # Silent FLAC frames may legitimately be 11 bytes long.
    if len(frame) < 9 or frame[0] != 0xff or frame[1] not in (0xf8, 0xf9):
        raise FlacFrameError("Неверная синхронизация FLAC-фрейма.")
    leading = frame[4]
    if leading < 0x80:
        width = 1
    elif leading >= 0xc0:
        width = 0
        for bit in range(7, -1, -1):
            if leading & (1 << bit):
                width += 1
            else:
                break
    else:
        raise FlacFrameError("Некорректный UTF-8-номер FLAC-фрейма.")
    if not 1 <= width <= 7:
        raise FlacFrameError("Неизвестная длина номера FLAC-фрейма.")
    for char in frame[5:4 + width]:
        if char & 0xc0 != 0x80:
            raise FlacFrameError("Повреждён номер FLAC-фрейма.")
    offset = 4 + width
    block_code = frame[2] >> 4
    rate_code = frame[2] & 15
    if block_code == 0 or rate_code == 15:
        raise FlacFrameError("Недопустимые параметры заголовка FLAC.")
    offset += 1 if block_code == 6 else 2 if block_code == 7 else 0
    offset += 1 if rate_code == 12 else 2 if rate_code in (13, 14) else 0
    if offset >= len(frame) - 2 or _crc8(frame[:offset]) != frame[offset]:
        raise FlacFrameError("Ошибка контрольной суммы заголовка FLAC-фрейма.")
    return width, offset


def _metadata_end(source) -> int:
    if source.read(4) != b"fLaC":
        raise FlacFrameError("Отсутствует FLAC-заголовок.")
    first = True
    while True:
        head = source.read(4)
        if len(head) != 4:
            raise FlacFrameError("Повреждены метаданные FLAC.")
        kind = head[0] & 0x7f
        length = int.from_bytes(head[1:4], "big")
        if first and (kind != 0 or length != 34):
            raise FlacFrameError("Отсутствует стандартный блок STREAMINFO.")
        first = False
        source.seek(length, 1)
        if head[0] & 0x80:
            return source.tell()


def _packets(ffmpeg: str, path: Path) -> list[tuple[int, int]]:
    """Get compressed packet lengths and sample counts without transcoding."""
    process = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-f", "framecrc", "-",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if process.returncode:
        details = process.stderr.decode("utf-8", "replace").strip()
        raise FlacFrameError(f"Не удалось прочитать FLAC-фреймы: {details}")
    packets: list[tuple[int, int]] = []
    for line in process.stdout.decode("ascii", "replace").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split(",")
        if len(fields) < 6:
            raise FlacFrameError("Неизвестный формат framecrc FFmpeg.")
        try:
            duration, size = int(fields[3].strip()), int(fields[4].strip())
        except ValueError as exc:
            raise FlacFrameError("Некорректный размер или длительность аудиофрейма.") from exc
        if duration <= 0 or size < 8:
            raise FlacFrameError("Повреждён аудиофрейм FLAC.")
        packets.append((size, duration))
    if not packets:
        raise FlacFrameError("В выходном FLAC не обнаружены аудиофреймы.")
    return packets


def normalize_copied_flac(path: Path, ffmpeg: str) -> None:
    """Rebase frame indexes and CRCs while preserving compressed audio data."""
    packets = _packets(ffmpeg, path)
    fd, temp = tempfile.mkstemp(prefix="flacsplitter-rebase-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp_path = Path(temp)
    try:
        with path.open("rb") as original, temp_path.open("w+b") as normalized:
            metadata_end = _metadata_end(original)
            original.seek(0)
            normalized.write(original.read(metadata_end))
            total_samples = 0
            for index, (size, duration) in enumerate(packets):
                frame = original.read(size)
                if len(frame) != size:
                    raise FlacFrameError("Размеры FLAC-фреймов не совпадают с выводом FFmpeg.")
                width, header_end = _frame_header_length(frame)
                number = total_samples if frame[1] & 1 else index
                new_header = bytearray(frame[:4])
                new_header.extend(_encode_number(number))
                new_header.extend(frame[4 + width:header_end])
                new_header.append(_crc8(new_header))
                # CRC16 is linear: adjust the existing checksum for the new
                # header, without iterating through the unchanged audio body.
                payload = frame[header_end + 1:-2]
                old_crc = int.from_bytes(frame[-2:], "big")
                delta = _crc16(frame[:header_end + 1]) ^ _crc16(new_header)
                new_crc = old_crc ^ _advance_crc16(delta, len(payload))
                normalized.write(new_header)
                normalized.write(payload)
                normalized.write(new_crc.to_bytes(2, "big"))
                total_samples += duration
                if total_samples >= 1 << 36:
                    raise FlacFrameError("Слишком большой трек для формата FLAC.")
            if original.read(1):
                raise FlacFrameError("Неучтённые данные после последнего FLAC-фрейма.")
            normalized.seek(18)
            packed = int.from_bytes(normalized.read(8), "big")
            normalized.seek(18)
            normalized.write(((packed & ~((1 << 36) - 1)) | total_samples).to_bytes(8, "big"))
            # MD5 from the original album cannot be reused; zero means unset.
            normalized.write(bytes(16))
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
