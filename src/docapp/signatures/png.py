"""Подготовка подписи: прозрачный фон и обрезка полей (ТЗ-кровь §6, ADR-0029).

`Pillow` в зависимостях нет, поэтому картинку разбирает `pymupdf`: он декодирует
JPEG и PNG из байтов и отдаёт пиксели, а PNG с альфа-каналом собирается здесь же
своим кодировщиком (IHDR + zlib + IEND) — так же, как это делал бы canvas.

**Почему порог не на телефоне.** Прозрачность зависит от освещения: в кабинете
бумага на снимке серая или жёлтая, а не белая, и фиксированный порог «белее 200 —
прозрачно» сделал бы из подписи цветной прямоугольник. Поэтому уровни «бумага» и
«штрих» считаются по гистограмме конкретного снимка, и это одна реализация с
тестами вместо двух — телефонной и серверной. Телефон делает то, что требует
пальца: поворот и уменьшение (см. `web/static/signatures.js`).

**Почему всё на срезах и таблицах, а не в цикле по пикселям.** Снимок с телефона —
полтора миллиона пикселей; питоновский цикл по ним идёт минутами, а врач в
операционной столько ждать не будет. Цвет приводится переводом байтов по заранее
посчитанным таблицам (`bytes.translate`), яркость берётся по зелёному каналу (он
даёт основной вклад в воспринимаемую яркость), гистограмма — `Counter` по срезу,
рамка штриха — `find`/`rfind` по маске. Всё это выполняется в C.

Правило подготовки:

- PNG с **настоящей** прозрачностью и разумной стороной возвращается как есть:
  лишняя перекодировка только испортила бы штрих. Наличие альфа-канала само по
  себе ничего не значит — canvas отдаёт RGBA, где альфа 255 во всех пикселях;
- всё остальное (JPEG, PNG без прозрачности, слишком большая картинка) переводится
  в PNG с прозрачным фоном: бумага становится прозрачной, штрих остаётся тёмным и
  нейтральным (цвет бумаги не даёт ореола), поля обрезаются по штриху.
"""

from __future__ import annotations

import struct
import zlib
from collections import Counter

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

#: Потолок серого у штриха: подпись печатается тёмным, а цвет бумаги (жёлтая
#: или голубая на снимке) не должен давать цветной ореол вокруг линии.
INK_CEILING = 90

#: Порог «это уже прозрачный PNG»: есть хоть один пиксель прозрачнее этого.
_TRANSPARENT_ENOUGH = 250

#: Таблица «яркость → серый штриха»: цвет бумаги срезается, штрих остаётся тёмным.
_SHADE_TABLE = bytes(min(level, INK_CEILING) for level in range(256))

#: Таблица «яркость → признак штриха» для поиска рамки.
_INK_TABLE = bytes(1 if level >= INK_ALPHA else 0 for level in range(256))


def has_alpha(data: bytes) -> bool:
    """Есть ли в PNG альфа-канал (тип цвета 4 — серый+альфа, 6 — RGBA)."""
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
    """Подпись к сохранению: PNG с прозрачным фоном и обрезанными полями.

    Готовый файл определяется по **настоящей прозрачности**, а не по наличию
    альфа-канала: canvas на телефоне отдаёт RGBA, в котором альфа равна 255 во
    всех пикселях, — это обычный непрозрачный снимок, и пропускать его нельзя.
    Именно так подпись владельца попала на прод жёлтым прямоугольником.
    """
    if _is_transparent_png(data):
        return data
    return transparent(data)


def transparent(data: bytes) -> bytes:
    """Сделать фон прозрачным и обрезать поля (JPEG, PNG без прозрачности, большое фото)."""
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


def _is_transparent_png(data: bytes) -> bool:
    """PNG с альфа-каналом, в котором есть хотя бы один прозрачный пиксель."""
    if not has_alpha(data) or not 0 < png_side(data) <= MAX_SIDE:
        return False
    try:
        pixmap = pymupdf.Pixmap(data)
    except Exception:  # noqa: BLE001 — битый PNG должен уйти в обычную обработку
        return False
    if pixmap.n != 4 or not pixmap.alpha:
        return False
    return min(pixmap.samples[3::4]) < _TRANSPARENT_ENOUGH


def _decode(data: bytes):
    """Пиксели картинки: RGB, длинная сторона не больше MAX_SIDE."""
    try:
        source = pymupdf.Pixmap(data)
    except Exception as error:  # noqa: BLE001 — pymupdf бросает разные типы
        raise SignatureError("Файл не читается как картинка") from error
    if source.width <= 0 or source.height <= 0:
        raise SignatureError("Файл не читается как картинка")
    # Серый и CMYK приводим к RGB. Альфа-канал не убираем: `Pixmap(csRGB, pix)`
    # его сохраняет (n = 4), а нам он не мешает — прозрачность считается заново,
    # и `_green_rows` берёт шаг по фактическому числу каналов.
    if source.colorspace is None or source.colorspace.n != 3:
        source = pymupdf.Pixmap(pymupdf.csRGB, source)
    while max(source.width, source.height) > MAX_SIDE:
        shrunk = pymupdf.Pixmap(source)
        shrunk.shrink(1)
        source = shrunk
    return source


def _green_rows(source) -> list[bytes]:
    """Зелёный канал по строкам: `bytes` на строку, без цикла по пикселям.

    Шаг берётся по фактическому числу каналов: у снимка от canvas их четыре
    (RGBA), у JPEG — три. Альфа в расчёт не идёт, но её байты сдвигают соседние,
    и шаг 3 по RGBA дал бы мусор вместо яркости.
    """
    width, height, step = source.width, source.height, source.n
    samples = source.samples
    stride = source.stride
    if stride == width * step:  # обычный случай: строки идут подряд — берём срезом
        green = samples[1::step]
        return [green[y * width : (y + 1) * width] for y in range(height)]
    return [
        samples[y * stride + 1 : y * stride + width * step : step]
        for y in range(height)
    ]


def _levels(source) -> tuple[int, int]:
    """Уровни «бумага» и «штрих» по гистограмме яркости снимка.

    Бумага — 85-й процентиль яркости (её на снимке большинство), штрих — 3-й.
    Так порог подстраивается под освещение и цвет: серая бумага в кабинете,
    жёлтая под лампой и белый лист на столе дают разные абсолютные значения, но
    одинаковое отношение. Гистограмма считается `Counter` по срезу — это C.
    """
    hist = [0] * 256
    for value, count in Counter(b"".join(_green_rows(source))).items():
        hist[value] = count

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
        if not count:
            continue
        passed += count
        if passed >= target:
            return value
    return 255


def _alpha_table(paper: int, ink: int) -> bytes:
    """Таблица «яркость → альфа»: мягкий край между бумагой и штрихом.

    Альфа растёт от нуля к единице, поэтому край штриха остаётся гладким, а не
    рваным, как при жёстком пороге.
    """
    span = max(1, paper - ink)
    clear = ink + span * 55 // 100  # ярче — прозрачно
    solid = ink + span * 20 // 100  # темнее — непрозрачно
    table = bytearray(256)
    for level in range(256):
        if level >= clear:
            table[level] = 0
        elif level <= solid:
            table[level] = 255
        else:
            table[level] = 255 * (clear - level) // (clear - solid)
    return bytes(table)


def _rgba_rows(
    source, paper: int, ink: int
) -> tuple[list[bytearray], tuple[int, int, int, int] | None]:
    """Строки RGBA и рамка штриха.

    Каждая строка собирается присваиванием по срезу с шагом 4: цвет и альфа
    приходят из таблиц, поэтому цикл идёт по строкам, а не по пикселям.
    """
    width = source.width
    alpha_table = _alpha_table(paper, ink)
    rows: list[bytearray] = []
    left, right = width, -1
    top, bottom = source.height, -1

    for y, green in enumerate(_green_rows(source)):
        alpha = green.translate(alpha_table)
        shade = green.translate(_SHADE_TABLE)
        mask = alpha.translate(_INK_TABLE)
        row = bytearray(width * 4)
        row[0::4] = shade
        row[1::4] = shade
        row[2::4] = shade
        row[3::4] = alpha
        rows.append(row)
        if b"\x01" in mask:
            first = mask.find(b"\x01")
            last = mask.rfind(b"\x01")
            if first < left:
                left = first
            if last > right:
                right = last
            if y < top:
                top = y
            if y > bottom:
                bottom = y

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
