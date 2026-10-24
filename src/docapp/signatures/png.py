"""Подготовка подписи: прозрачный фон, обрезка полей, синий штрих (ADR-0029).

`Pillow` в зависимостях нет, поэтому картинку разбирает `pymupdf`, а PNG с
альфа-каналом собирается своим кодировщиком (IHDR + zlib + IEND).

**Как отделяются чернила от бумаги.** Сначала по цвету: синяя ручка даёт избыток
синего канала (`B − (R+G)//2`), а тень и бумага нейтральны — у них этот избыток
около нуля при любой яркости. Поэтому тень от руки на листе исчезает целиком, даже
тёмная: она не синяя. Если синего в снимке нет (чёрная ручка), включается второй
путь — по темноте, с порогами по гистограмме конкретного снимка. Штрих при этом
ищется как самый тёмный уровень, на котором уже есть заметное число пикселей:
процентиль здесь ошибался, потому что тонкая подпись занимает меньше процента
кадра, и «3-й процентиль» попадал на бумагу — отсюда отказ «на фото не видно
подписи» на хорошем снимке.

**Почему анализ по сетке в 4 раза мельче, а не по каждому пикселю.** Снимок с
телефона — полтора миллиона пикселей; питоновский цикл по ним идёт минутами.
Цвет и уровни считаются по каждому четвёртому пикселю (около 90 тысяч — десятые
доли секунды), альфа потом растягивается на полное разрешение. Подпись печатается
в графе шириной 3–4 см, поэтому шаг 4 пикселя на снимке 1600 точек — десятые доли
миллиметра на бумаге: глазу не видно.

**Цвет.** Штрих печатается синим (как подписываются в отделении): форму хранит
альфа, цвет — константа, и жёлтая или голубая бумага в документ не переезжает.
"""

from __future__ import annotations

import struct
import zlib

import pymupdf

from docapp.signatures.errors import SignatureError

#: Сигнатура PNG и смещение типа цвета в IHDR (8 + длина + тип + ширина + высота + глубина).
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_COLOR_TYPE_OFFSET = 25

#: Предел длинной стороны готового PNG (принимаем от клиента до 1600).
MAX_SIDE = 1600

#: Рабочее разрешение обработки: 1000 точек на 4 см — это около 600 dpi, больше
#: не нужно, а время расчёта держит в десятых долях секунды.
WORK_SIDE = 1000

#: Шаг анализа: альфа считается по каждому четвёртому пикселю и растягивается.
COARSE = 4

#: Ниже этого различия «бумага/штрих» снимок без синего считается пустым.
MIN_CONTRAST = 25

#: Непрозрачность, при которой пиксель считается штрихом для рамки обрезки.
INK_ALPHA = 160

#: Запас вокруг штриха при обрезке, доля от его размера.
MARGIN = 0.04

#: Цвет подписи: отделение подписывается синей ручкой.
INK_COLOUR = (20, 60, 160)

#: Избыток синего, начиная с которого пиксель считается чернилами.
BLUE_EXCESS = 12

#: Рампа альфы по избытку синего: ниже — прозрачно, выше — непрозрачно.
BLUE_FLOOR = 10
BLUE_CEIL = 45

#: Доли рампы «по темноте» (чёрная ручка): ниже первой — непрозрачно, выше второй — нет.
SOLID_SHARE = 10
CLEAR_SHARE = 30

#: Порог «это уже прозрачный PNG»: есть хоть один пиксель прозрачнее этого.
_TRANSPARENT_ENOUGH = 250

#: Таблица «альфа → признак штриха» для поиска рамки.
_INK_MASK_TABLE = bytes(1 if alpha >= INK_ALPHA else 0 for alpha in range(256))

#: Байт → четыре его копии: растянуть альфу с грубой сетки на полное разрешение.
_SPREAD = [bytes([value]) * COARSE for value in range(256)]


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
    """
    if _is_transparent_png(data):
        return data
    return transparent(data)


def transparent(data: bytes) -> bytes:
    """Прозрачный фон, обрезка полей и синий штрих для непрозрачного снимка."""
    source = _decode(data)
    red_rows, green_rows, blue_rows = _channel_rows(source)
    grid = _coarse_grid(red_rows, green_rows, blue_rows, source.width, source.height)
    paper, ink, blue_base = _levels(grid)
    blue = _blue_present(grid, paper, blue_base)
    if not blue and paper - ink < MIN_CONTRAST:
        raise SignatureError("На фото не видно подписи: слишком мало контраста")

    alpha = _alpha_grid(grid, paper, ink, blue, blue_base)
    box = _ink_box(alpha)
    if box is None:
        raise SignatureError("На фото не видно подписи")

    coarse_width, coarse_height = len(alpha[0]), len(alpha)
    left, top, right, bottom = _with_margin(box, coarse_width, coarse_height)
    # Если «штрих» занимает почти весь кадр, это не подпись на бумаге, а тёмный
    # снимок: обрезать нечего, оставляем как есть.
    crop = (right - left) * (bottom - top) < coarse_width * coarse_height * 97 // 100

    width, height = source.width, source.height
    first_column = left * COARSE if crop else 0
    last_column = min(right * COARSE, width) if crop else width
    first_row = top * COARSE
    last_row = min(bottom * COARSE, height)

    alpha_rows = _fine_alpha(
        red_rows, green_rows, blue_rows, paper, ink, blue, blue_base,
        first_row, last_row, first_column, last_column,
    )

    out_width = last_column - first_column
    base = bytearray(out_width * 4)
    base[0::4] = bytes([INK_COLOUR[0]]) * out_width
    base[1::4] = bytes([INK_COLOUR[1]]) * out_width
    base[2::4] = bytes([INK_COLOUR[2]]) * out_width

    rows: list[bytearray] = []
    for line_alpha in alpha_rows:
        line = bytearray(base)
        line[3::4] = line_alpha
        rows.append(line)

    return _encode(out_width, len(rows), rows)


def _fine_alpha(
    red_rows: list[bytes],
    green_rows: list[bytes],
    blue_rows: list[bytes],
    paper: int,
    ink: int,
    blue: bool,
    blue_base: int,
    first_row: int,
    last_row: int,
    first_column: int,
    last_column: int,
) -> list[bytes]:
    """Альфа по каждому пикселю внутри рамки — гладкие края без ступенек.

    Сетка вчетверо мельче нужна только для решений (что считать бумагой, что
    чернилами, где рамка): растянуть её альфу квадратами — и на документе видна
    «пиксельность», подпись выглядит подделкой. Поэтому точная альфа считается
    заново, но уже только внутри рамки, а не по всему кадру.
    """
    dark = None if blue else _dark_table(paper, ink)
    ramp = _blue_ramp() if blue else None
    result: list[bytes] = []
    for y in range(first_row, last_row):
        red = red_rows[y][first_column:last_column]
        green = green_rows[y][first_column:last_column]
        blue_channel = blue_rows[y][first_column:last_column]
        line = bytearray(len(red))
        if ramp is None:
            for index, (r, g, b) in enumerate(zip(red, green, blue_channel)):
                line[index] = dark[(r + g + b) // 3]  # type: ignore[index]
        else:
            for index, (r, g, b) in enumerate(zip(red, green, blue_channel)):
                own = b - (r + g) // 2 - blue_base
                line[index] = ramp[own + 128] if -128 <= own <= 127 else (255 if own > 127 else 0)
        result.append(bytes(line))
    return result


def _blue_ramp() -> bytes:
    """Таблица «избыток синего (со сдвигом +128) → альфа»."""
    span = max(1, BLUE_CEIL - BLUE_FLOOR)
    table = bytearray(256)
    for index in range(256):
        own = index - 128
        if own <= BLUE_FLOOR:
            table[index] = 0
        elif own >= BLUE_CEIL:
            table[index] = 255
        else:
            table[index] = 255 * (own - BLUE_FLOOR) // span
    return bytes(table)


# ── разбор картинки ──────────────────────────────────────────────────


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
    # его сохраняет (n = 4), а нам он не мешает — прозрачность считается заново.
    if source.colorspace is None or source.colorspace.n != 3:
        source = pymupdf.Pixmap(pymupdf.csRGB, source)
    while max(source.width, source.height) > WORK_SIDE:
        shrunk = pymupdf.Pixmap(source)
        shrunk.shrink(1)
        source = shrunk
    return source


def _channel_rows(source) -> list[list[bytes]]:
    """Каналы R, G, B по строкам: `bytes` на строку, без цикла по пикселям.

    Шаг берётся по фактическому числу каналов: у снимка от canvas их четыре
    (RGBA), у JPEG — три. Альфа в расчёт не идёт, но её байты сдвигают соседние,
    и шаг 3 по RGBA дал бы мусор вместо цвета.
    """
    width, height, step = source.width, source.height, source.n
    samples, stride = source.samples, source.stride
    if stride == width * step:  # строки идут подряд — берём срезом
        channels = [samples[offset::step] for offset in range(3)]
        return [
            [channel[y * width : (y + 1) * width] for y in range(height)]
            for channel in channels
        ]
    return [
        [
            samples[y * stride + offset : y * stride + width * step : step]
            for y in range(height)
        ]
        for offset in range(3)
    ]


def _coarse_grid(
    red_rows: list[bytes],
    green_rows: list[bytes],
    blue_rows: list[bytes],
    width: int,
    height: int,
) -> list[list[tuple[int, int]]]:
    """Грубая сетка: (яркость, избыток синего) через каждые COARSE точек.

    Срез с шагом по строке делает C, питоновский цикл остаётся по сетке — это
    около 90 тысяч итераций на снимок 1600×900 вместо полутора миллионов.
    """
    grid = []
    for y in range(0, height, COARSE):
        red = red_rows[y][::COARSE]
        green = green_rows[y][::COARSE]
        blue = blue_rows[y][::COARSE]
        grid.append(
            [
                ((r + g + b) // 3, b - (r + g) // 2)
                for r, g, b in zip(red, green, blue)
            ]
        )
    return grid


def _levels(grid: list[list[tuple[int, int]]]) -> tuple[int, int, int]:
    """Уровни: «бумага», «штрих» и собственная синева бумаги.

    Бумага — 85-й процентиль яркости (её на снимке большинство). Штрих — самый
    тёмный уровень, на котором уже есть заметное число пикселей: процентиль
    ошибался, потому что тонкая подпись занимает меньше процента кадра, и «3-й
    процентиль» попадал на бумагу — приложение отказывалось видеть подпись на
    чистом снимке. Третий уровень — медиана избытка синего: холодный свет делает
    бумагу голубоватой, и без вычитания её собственной синевы вся страница
    считалась бы чернилами.
    """
    hist = [0] * 256
    blue_hist = [0] * 256
    for line in grid:
        for level, excess in line:
            hist[level] += 1
            blue_hist[excess + 128] += 1
    total = sum(hist)
    if total == 0:
        raise SignatureError("Пустая картинка")

    paper = _percentile(hist, total, 85)
    blue_base = _percentile(blue_hist, total, 50) - 128
    # Штрих — самый тёмный уровень, который вообще есть на снимке. Раньше брался
    # первый уровень, набравший заметное число пикселей, и на тонкой подписи это
    # оказывалась тень (её пикселей много, штриха мало): тень становилась
    # «чернилами», а подпись — нет.
    ink = paper
    for level, count in enumerate(hist):
        if count:
            ink = level
            break
    return paper, ink, blue_base


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


def _blue_present(
    grid: list[list[tuple[int, int]]], paper: int, blue_base: int
) -> bool:
    """Есть ли на снимке синие чернила: тень и бумага такого избытка не дают.

    Нужны оба условия — абсолютный избыток и избыток сверх синевы самой бумаги.
    Без первого тёмный нейтральный штрих на жёлтой бумаге считался бы синим
    (он «менее жёлтый», значит относительно синий), и подпись чёрной ручкой
    перестала бы находиться.
    """
    enough = max(15, sum(len(line) for line in grid) // 20000)
    found = 0
    for line in grid:
        for level, excess in line:
            if (
                excess >= BLUE_EXCESS
                and excess - blue_base >= BLUE_EXCESS
                and level < paper - 5
            ):
                found += 1
                if found >= enough:
                    return True
    return False


def _alpha_grid(
    grid: list[list[tuple[int, int]]],
    paper: int,
    ink: int,
    blue: bool,
    blue_base: int,
) -> list[bytes]:
    """Альфа по грубой сетке: по синему, если он есть, иначе по темноте."""
    table = None if blue else _dark_table(paper, ink)
    spread = 255 // (BLUE_CEIL - BLUE_FLOOR)
    lines = []
    for line in grid:
        values = bytearray()
        for level, excess in line:
            if table is None:
                own = excess - blue_base
                if own <= BLUE_FLOOR:
                    values.append(0)
                elif own >= BLUE_CEIL:
                    values.append(255)
                else:
                    values.append((own - BLUE_FLOOR) * spread)
            else:
                values.append(table[level])
        lines.append(bytes(values))
    return lines


def _dark_table(paper: int, ink: int) -> bytes:
    """Таблица «яркость → альфа» для чёрной ручки: мягкий край между бумагой и штрихом."""
    span = max(1, paper - ink)
    clear = ink + span * CLEAR_SHARE // 100  # ярче — прозрачно
    solid = ink + span * SOLID_SHARE // 100  # темнее — непрозрачно
    table = bytearray(256)
    for level in range(256):
        if level >= clear:
            table[level] = 0
        elif level <= solid:
            table[level] = 255
        else:
            table[level] = 255 * (clear - level) // (clear - solid)
    return bytes(table)


def _ink_box(alpha: list[bytes]) -> tuple[int, int, int, int] | None:
    """Рамка штриха по грубой сетке альфы: (лево, верх, право, низ)."""
    left, right = len(alpha[0]), -1
    top, bottom = len(alpha), -1
    for y, line in enumerate(alpha):
        mask = line.translate(_INK_MASK_TABLE)
        if b"\x01" not in mask:
            continue
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
        return None
    return left, top, right, bottom


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
