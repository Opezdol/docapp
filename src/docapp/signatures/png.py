"""Подготовка подписи: прозрачный фон и обрезка полей (ТЗ-кровь §6, ADR-0029).

`Pillow` в зависимостях нет, поэтому картинку разбирает `pymupdf`: он декодирует
JPEG и PNG из байтов и отдаёт пиксели, а PNG с альфа-каналом собирается здесь же
своим кодировщиком (IHDR + zlib + IEND) — так же, как это делал бы canvas.

**Почему порог не на телефоне.** Прозрачность зависит от освещения: в кабинете
бумага на снимке серая, а не белая, и фиксированный порог «белее 200 — прозрачно»
сделал бы из подписи серое пятно. Поэтому уровни «бумага» и «штрих» считаются по
гистограмме конкретного снимка, и это одна реализация с тестами вместо двух —
телефонной и серверной. Телефон делает то, что требует пальца: поворот и
уменьшение (см. `web/static/signatures.js`).

Правило подготовки:

- PNG с альфа-каналом и разумной стороной возвращается **как есть**: клиент его
  уже собрал, лишняя перекодировка только испортила бы штрих;
- всё остальное (JPEG, PNG без альфы, слишком большая картинка) переводится в PNG
  с прозрачным фоном: бумага становится прозрачной, штрих остаётся, поля
  обрезаются по штриху с небольшим запасом.
"""

from __future__ import annotations

import struct
import zlib

import pymupdf

from docapp.signatures.errors import SignatureError

#: Сигнатура PNG и смещение типа цвета в IHDR (8 + длина + тип + ширина + высота + глубина).
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_COLOR_TYPE_OFFSET = 25

#: Предел длинной стороны: подпись печатается в графе шириной 3–4 см.
MAX_SIDE = 1600

#: Ниже этого различия «бумага/штрих» снимок считается пустым, а не подписью.
MIN_CONTRAST = 25

#: Пиксель считается штрихом при такой непрозрачности — по нему режется рамка.
INK_ALPHA = 96

#: Запас вокруг штриха при обрезке, доля от его размера.
MARGIN = 0.04


def has_alpha(data: bytes) -> bool:
    """PNG с альфа-каналом (тип цвета 4 — серый+альфа, 6 — RGBA)."""
    if not data.startswith(PNG_MAGIC) or len(data) < 26:
        return False
    return data[_COLOR_TYPE_OFFSET] in (4, 6)


def png_side(data: bytes) -> int:
    """Длинная сторона PNG по IHDR; 0, если это не PNG."""
    if not data.startswith(PNG_MAGIC) or len(data) < 24:
        return 0
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return max(width, height)


def prepare(data: bytes) -> bytes:
    """Подпись к сохранению: PNG с прозрачным фоном и обрезанными полями."""
    if has_alpha(data) and 0 < png_side(data) <= MAX_SIDE:
        return data
    return transparent(data)


def transparent(data: bytes) -> bytes:
    """Сделать фон прозрачным и обрезать поля (JPEG, PNG без альфы, большое фото)."""
    source = _decode(data)
    paper, ink = _levels(source)
    rows, box = _rgba_rows(source, paper, ink)
    if box is None:
        raise SignatureError("На фото не видно подписи")

    width, height = source.width, source.height
    left, top, right, bottom = _with_margin(box, width, height)
    # Если «штрих» занимает почти весь кадр, это не подпись на бумаге, а тёмный
    # снимок: обрезать нечего, оставляем как есть.
    if (right - left) * (bottom - top) < width * height * 97 // 100:
        rows = [row[left * 4 : right * 4] for row in rows[top:bottom]]
        width, height = right - left, bottom - top
    return _encode(width, height, rows)


def _decode(data: bytes):
    """Пиксели картинки: RGB, длинная сторона не больше MAX_SIDE."""
    try:
        source = pymupdf.Pixmap(data)
    except Exception as error:  # noqa: BLE001 — pymupdf бросает разные типы
        raise SignatureError("Файл не читается как картинка") from error
    if source.width <= 0 or source.height <= 0:
        raise SignatureError("Файл не читается как картинка")
    if source.colorspace is None or source.colorspace.n != 3:
        source = pymupdf.Pixmap(pymupdf.csRGB, source)
    while max(source.width, source.height) > MAX_SIDE:
        shrunk = pymupdf.Pixmap(source)
        shrunk.shrink(1)
        source = shrunk
    return source


def _levels(source) -> tuple[int, int]:
    """Уровни «бумага» и «штрих» по гистограмме яркости снимка.

    Бумага — 85-й процентиль яркости (её на снимке большинство), штрих — 3-й.
    Так порог подстраивается под освещение: серая бумага в кабинете и белый лист
    на столе дают разные абсолютные значения, но одинаковое отношение.
    """
    hist = [0] * 256
    samples = source.samples
    stride = source.stride
    for y in range(0, source.height, 2):
        base = y * stride
        for x in range(0, source.width, 2):
            i = base + x * 3
            hist[(samples[i] + samples[i + 1] + samples[i + 2]) // 3] += 1

    total = sum(hist)
    if total == 0:
        raise SignatureError("Пустая картинка")
    ink = _percentile(hist, total, 3)
    paper = _percentile(hist, total, 85)
    if paper - ink < MIN_CONTRAST:
        raise SignatureError("На фото не видно подписи: слишком мало контраста")
    return paper, ink


def _percentile(hist: list[int], total: int, percent: int) -> int:
    """Значение яркости, ниже которого лежит `percent` процентов пикселей."""
    target = total * percent // 100
    passed = 0
    for value, count in enumerate(hist):
        passed += count
        if passed >= target:
            return value
    return 255


def _rgba_rows(source, paper: int, ink: int) -> tuple[list[bytearray], tuple[int, int, int, int] | None]:
    """Строки RGBA и рамка штриха: один проход по пикселям.

    Альфа растёт от нуля к единице между «бумагой» и «штрихом», поэтому край
    штриха остаётся мягким, а не рваным, как при жёстком пороге.
    """
    span = max(1, paper - ink)
    clear = ink + span * 55 // 100  # ярче — прозрачно
    solid = ink + span * 20 // 100  # темнее — непрозрачно

    rows: list[bytearray] = []
    left, top, right, bottom = source.width, source.height, -1, -1
    for y in range(source.height):
        base = y * source.stride
        row = bytearray(source.width * 4)
        out = 0
        for x in range(source.width):
            i = base + x * 3
            red, green, blue = source.samples[i], source.samples[i + 1], source.samples[i + 2]
            level = (red + green + blue) // 3
            if level >= clear:
                alpha = 0
            elif level <= solid:
                alpha = 255
            else:
                alpha = 255 * (clear - level) // (clear - solid)
            row[out] = red
            row[out + 1] = green
            row[out + 2] = blue
            row[out + 3] = alpha
            if alpha >= INK_ALPHA:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
            out += 4
        rows.append(row)

    if right < 0:
        return rows, None
    return rows, (left, top, right, bottom)


def _with_margin(
    box: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int, int, int]:
    """Рамка штриха плюс небольшой запас, в пределах картинки."""
    left, top, right, bottom = box
    margin = max(2, int(max(right - left, bottom - top) * MARGIN))
    return (
        max(0, left - margin),
        max(0, top - margin),
        min(width, right + 1 + margin),
        min(height, bottom + 1 + margin),
    )


def _encode(width: int, height: int, rows: list[bytearray]) -> bytes:
    """Собрать PNG: фильтр 0 в каждой строке, тип цвета 6 (RGBA)."""
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        PNG_MAGIC
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def _chunk(tag: bytes, payload: bytes) -> bytes:
    """Чанк PNG: длина, тег, данные, CRC."""
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )
