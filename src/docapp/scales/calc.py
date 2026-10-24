"""Шкалы: пункты, баллы и зоны (ТЗ-шкалы, ADR-0028).

Единственное место, где объявлены четыре шкалы: пункты, сколько баллов даёт
ответ и где кончается зона. Роутер только читает форму и отдаёт шаблон —
шаблон ничего не считает.

Модуль ничего не хранит (`schema=None`): балл считается на каждый запрос из
ответов формы и живёт только в отрисованной странице. Ни ФИО, ни даты, ни
записи анестезии модуль не знает — связи с `records` у него нет.

Формулировки и пороги — по первоисточникам шкал:

- STOP-BANG — Chung et al., 2008, восемь пунктов по 1 баллу;
- Apfel — Apfel et al., 1999, четыре фактора по 1 баллу;
- Aldrete (модифицированная) — пять критериев по 0–2 балла;
- Nu-DESC — Gaudreau et al., 2006, пять пунктов по 0–2 балла.

Незаполненный пункт считается нулевым: все четыре шкалы аддитивные, и сумма с
неизвестным слагаемым не существует (ADR-0028).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Option:
    """Вариант ответа: значение в форме и подпись на странице."""

    value: int
    label: str


@dataclass(frozen=True)
class Item:
    """Пункт шкалы.

    `points` идёт параллельно `options`: баллы за каждый вариант ответа.
    `shared` помечает пункт, который берётся из общего поля и показывается
    один раз — сейчас это «Пол» в STOP-BANG и Apfel.
    """

    key: str
    label: str
    options: tuple[Option, ...]
    points: tuple[int, ...]
    shared: bool = False
    hint: str = ""


@dataclass(frozen=True)
class Band:
    """Зона шкалы: баллы до `top` включительно и что они означают."""

    top: int
    label: str


@dataclass(frozen=True)
class Scale:
    """Шкала целиком: пункты, зоны и оговорки для справочного блока."""

    key: str
    title: str
    stage: str
    items: tuple[Item, ...]
    bands: tuple[Band, ...]
    notes: tuple[str, ...] = ()

    @property
    def max_points(self) -> int:
        """Максимум баллов — сумма лучших ответов по всем пунктам."""
        return sum(max(item.points) for item in self.items)


#: Ответ «да/нет» — большинство пунктов STOP-BANG и Apfel.
YES_NO: tuple[Option, ...] = (Option(0, "Нет"), Option(1, "Да"))

#: Общий пункт «Пол»: в STOP-BANG балл даёт мужской пол, в Apfel — женский.
SEX: tuple[Option, ...] = (Option(0, "Мужской"), Option(1, "Женский"))

#: Три варианта оценки у Aldrete и Nu-DESC.
LEVELS: tuple[Option, ...] = (
    Option(2, "Выражено"),
    Option(1, "Слабо или периодически"),
    Option(0, "Нет"),
)


STOP_BANG = Scale(
    key="stop_bang",
    title="STOP-BANG",
    stage="до анестезии",
    items=(
        Item("snore", "Храп", YES_NO, (0, 1), hint="громкий, слышно за дверью"),
        Item("tired", "Усталость", YES_NO, (0, 1), hint="сонливость, утомляемость днём"),
        Item("apnea", "Остановки дыхания", YES_NO, (0, 1), hint="кто-то наблюдал во сне"),
        Item(
            "pressure",
            "Давление",
            YES_NO,
            (0, 1),
            hint="повышенное артериальное давление: диагноз или лечение",
        ),
        Item("bmi", "ИМТ больше 35", YES_NO, (0, 1)),
        Item("age", "Возраст старше 50", YES_NO, (0, 1)),
        Item("neck", "Окружность шеи больше 40 см", YES_NO, (0, 1)),
        Item("sex", "Пол", SEX, (1, 0), shared=True),
    ),
    bands=(
        Band(2, "низкий риск СОАС"),
        Band(4, "промежуточный риск СОАС"),
        Band(8, "высокий риск СОАС"),
    ),
)


APFEL = Scale(
    key="apfel",
    title="Apfel",
    stage="до анестезии",
    items=(
        Item("sex", "Пол", SEX, (0, 1), shared=True),
        Item("non_smoker", "Не курит", YES_NO, (0, 1)),
        Item("history", "ПОТР или укачивание в анамнезе", YES_NO, (0, 1)),
        Item("opioids", "Планируются послеоперационные опиоиды", YES_NO, (0, 1)),
    ),
    bands=(
        Band(0, "риск ПОТР около 10%"),
        Band(1, "риск ПОТР около 21%"),
        Band(2, "риск ПОТР около 39%"),
        Band(3, "риск ПОТР около 61%"),
        Band(4, "риск ПОТР около 79%"),
    ),
)


ALDRETE = Scale(
    key="aldrete",
    title="Aldrete",
    stage="после анестезии",
    items=(
        Item(
            "activity",
            "Активность",
            (
                Option(2, "Произвольные движения всеми конечностями"),
                Option(1, "Движения двумя конечностями"),
                Option(0, "Не двигается"),
            ),
            (2, 1, 0),
        ),
        Item(
            "breathing",
            "Дыхание",
            (
                Option(2, "Дышит глубоко, кашляет"),
                Option(1, "Поверхностное, одышка"),
                Option(0, "Апноэ или ИВЛ"),
            ),
            (2, 1, 0),
        ),
        Item(
            "circulation",
            "Кровообращение",
            (
                Option(2, "АД в пределах ±20% от дооперационного"),
                Option(1, "АД ±20–50% от дооперационного"),
                Option(0, "АД более ±50% от дооперационного"),
            ),
            (2, 1, 0),
        ),
        Item(
            "consciousness",
            "Сознание",
            (
                Option(2, "Полностью в сознании"),
                Option(1, "Пробуждается по требованию"),
                Option(0, "Не реагирует"),
            ),
            (2, 1, 0),
        ),
        Item(
            "saturation",
            "Насыщение кислородом",
            (
                Option(2, "SpO₂ больше 92% на воздухе"),
                Option(1, "Нужен кислород, чтобы SpO₂ был больше 90%"),
                Option(0, "SpO₂ меньше 90% даже с кислородом"),
            ),
            (2, 1, 0),
        ),
    ),
    bands=(
        Band(8, "не готова к переводу"),
        Band(10, "готовность к переводу в отделение"),
    ),
    notes=(
        "Шкалу принято повторять в динамике: перевод разрешает последняя оценка.",
    ),
)


NU_DESC = Scale(
    key="nu_desc",
    title="Nu-DESC",
    stage="после анестезии",
    items=(
        Item(
            "disorientation",
            "Дезориентация",
            LEVELS,
            (2, 1, 0),
            hint="в месте, времени, собственной личности",
        ),
        Item(
            "behaviour",
            "Неадекватное поведение",
            LEVELS,
            (2, 1, 0),
            hint="пытается снять датчики, катетеры; небезопасные действия",
        ),
        Item(
            "communication",
            "Неадекватная коммуникация",
            LEVELS,
            (2, 1, 0),
            hint="бессвязная речь, отсутствие контакта",
        ),
        Item("illusions", "Иллюзии и галлюцинации", LEVELS, (2, 1, 0)),
        Item(
            "retardation",
            "Психомоторная заторможенность",
            LEVELS,
            (2, 1, 0),
            hint="замедленность, редкие реакции",
        ),
    ),
    bands=(
        Band(1, "отрицательный скрининг"),
        Band(10, "положительный скрининг"),
    ),
)


#: Шкалы в том порядке, в котором они показываются и считаются.
SCALES: tuple[Scale, ...] = (STOP_BANG, APFEL, ALDRETE, NU_DESC)

#: Шкала по ключу — для формы и тестов.
SCALE_BY_KEY: dict[str, Scale] = {scale.key: scale for scale in SCALES}


@dataclass(frozen=True)
class RenderedItem:
    """Пункт на странице: что показать, что отмечено и сколько он дал."""

    item: Item
    value: int | None
    points: int
    derived: bool = False


@dataclass(frozen=True)
class RenderedScale:
    """Шкала на странице: её пункты и готовый результат словами."""

    scale: Scale
    items: tuple[RenderedItem, ...]
    points: int
    result: str


def selected_keys(values: Sequence[str]) -> tuple[str, ...]:
    """Выбранные шкалы в каноническом порядке; неизвестные ключи отброшены."""
    wanted = set(values)
    return tuple(scale.key for scale in SCALES if scale.key in wanted)


def answer(item: Item, raw: Mapping[str, str]) -> int | None:
    """Ответ пункта из формы; None — не заполнено (считается нулём)."""
    text = raw.get(item.key)
    if text is None or text == "":
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if any(option.value == value for option in item.options):
        return value
    return None


def points_for(item: Item, value: int | None) -> int:
    """Баллы пункта за ответ; незаполненный или чужой ответ — ноль."""
    if value is None:
        return 0
    for option, points in zip(item.options, item.points):
        if option.value == value:
            return points
    return 0


def zone(scale: Scale, points: int) -> str:
    """Что означают баллы: первая зона, в которую они попадают."""
    for band in scale.bands:
        if points <= band.top:
            return band.label
    return scale.bands[-1].label


def plan(
    keys: Sequence[str], raw: Mapping[str, str]
) -> tuple[RenderedScale, ...]:
    """Разложить выбранные шкалы для страницы: пункты, баллы и результат.

    Общий пункт «Пол» показывается один раз — в первой выбранной шкале, которой
    он нужен; во второй он помечается `derived` и выводится строкой «учтено»
    (ТЗ §4: одно поле питает оба пункта).
    """
    shown: set[str] = set()
    rendered: list[RenderedScale] = []
    for key in selected_keys(keys):
        scale = SCALE_BY_KEY[key]
        items: list[RenderedItem] = []
        total = 0
        for item in scale.items:
            value = answer(item, raw)
            points = points_for(item, value)
            derived = item.shared and item.key in shown
            items.append(RenderedItem(item, value, points, derived))
            if item.shared:
                shown.add(item.key)
            total += points
        rendered.append(
            RenderedScale(
                scale=scale,
                items=tuple(items),
                points=total,
                result=f"{total} из {scale.max_points} — {zone(scale, total)}",
            )
        )
    return tuple(rendered)
