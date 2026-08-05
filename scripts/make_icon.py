"""Генератор иконок docapp: синий фон + белый медицинский крест.

Чистый Python (zlib + struct), без внешних зависимостей.
Запуск: uv run python scripts/make_icon.py
"""

import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "docapp" / "web" / "static"

BLUE = (21, 101, 192)    # #1565c0
WHITE = (255, 255, 255)


def make_png(size: int) -> bytes:
    """PNG размером size x size: синий фон, белый крест по центру."""
    rows = []
    for y in range(size):
        row = bytearray([0])  # фильтр: None
        for x in range(size):
            row.extend(pixel(size, x, y))
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8bit, RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def pixel(size: int, x: int, y: int) -> bytes:
    """Цвет пикселя: крест занимает центральные 60% по каждой оси."""
    margin = size * 0.2
    bar = size * 0.2  # толщина перекладины креста
    in_x = margin <= x < size - margin
    in_y = margin <= y < size - margin
    center_x = (size - bar) / 2 <= x < (size + bar) / 2
    center_y = (size - bar) / 2 <= y < (size + bar) / 2
    if in_x and center_y:
        return bytes(WHITE)  # вертикальная перекладина
    if in_y and center_x:
        return bytes(WHITE)  # горизонтальная перекладина
    return bytes(BLUE)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        path = OUT_DIR / f"icon-{size}.png"
        path.write_bytes(make_png(size))
        print(f"created {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
