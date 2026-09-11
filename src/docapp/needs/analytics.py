"""Аналитика по истории заявок: свод за период недель (ТЗ F8/E, задача T5).

Переиспользует агрегацию отчёта-формы из report.py: для каждой группы
заявок (база, неделя) вызывается aggregate_requests, а её результаты
СЛИВАЮТСЯ в общий словарь — суммы по точкам/препаратам за весь период.
Логика агрегации не дублируется; здесь только группировка, слияние,
фильтр по группе и подпись периода.

Роутер (T6) сам отфильтрует заявки store.list_range(from_week, to_week,
base, point) и передаст в summarize готовый список: фильтры base/point
внутри функции НЕ применяются (base используется только для подписи
и выбора точек шапки).

Форма итогового словаря совместима с agg из report.py — build_xlsx и
html_table работают с ним без изменений (дополнительные ключи:
period_label, from_week, to_week, group — их xlsx/HTML игнорируют).
"""

from datetime import datetime

from docapp.core import period
from docapp.needs.catalog import (
    CATEGORY_MEDICAMENTS,
    CATEGORY_SOLUTIONS,
    SOLUTIONS_GROUP,
    Catalog,
)
from docapp.needs.report import aggregate_requests


def period_label(from_week: str, to_week: str) -> str:
    """Подпись периода по неделям (общий механизм периода, ADR-0018).

    Примеры: ('2026-08-10', '2026-08-24') -> '10.08.2026 – 24.08.2026';
    одна неделя ('2026-08-10', '2026-08-10') -> '10.08.2026'.
    """
    return period.range_label(from_week, to_week)


def _merge_solutions(aggs: list[dict], catalog: Catalog, points: list[str]) -> dict:
    """Слить 'solutions' нескольких агрегатов в один поточковый свод.

    Для каждого раствора складываются количества по каждой точке и ИТОГО
    за весь период; единица берётся из первого непустого снимка (в норме
    снимки совпадают, но правки каталога не должны ломать историю).
    Недостающие точки периода дополняются нулями, порядок растворов —
    как в каталоге (незнакомые каталогу — после).
    """
    merged: dict[str, dict] = {}
    for agg in aggs:
        for item, row in agg["solutions"].items():
            out = merged.setdefault(item, {"unit": "", "ИТОГО": 0})
            for point, qty in row.items():
                if point in ("unit", "ИТОГО"):
                    continue
                out[point] = out.get(point, 0) + qty
            out["ИТОГО"] += row["ИТОГО"]
            if not out["unit"] and row.get("unit"):
                out["unit"] = row["unit"]
    for row in merged.values():
        for point in points:
            row.setdefault(point, 0)
    # Порядок вывода — как в каталоге, затем позиции, появившиеся только в снимках.
    ordered: dict[str, dict] = {}
    for item in catalog.solutions_items():
        if item.name in merged:
            ordered[item.name] = merged[item.name]
    for name, row in merged.items():
        ordered.setdefault(name, row)
    return ordered


def _merge_groups(aggs: list[dict], catalog: Catalog) -> dict:
    """Слить 'groups' нескольких агрегатов: суммы по препарату за период.

    Группы и препараты внутри — в порядке каталога (незнакомые — после),
    как в aggregate_requests. Группа «Растворы» сюда не попадает — она
    идёт поточково в 'solutions'.
    """
    merged: dict[str, dict[str, dict]] = {}
    for agg in aggs:
        for group, items in agg["groups"].items():
            out_group = merged.setdefault(group, {})
            for item, entry in items.items():
                out = out_group.setdefault(item, {"unit": "", "qty": 0})
                out["qty"] += entry["qty"]
                if not out["unit"] and entry.get("unit"):
                    out["unit"] = entry["unit"]
    group_order = [g for g in catalog.groups() if g != SOLUTIONS_GROUP]
    items_in_group = catalog.groups()
    ordered_groups: dict[str, dict[str, dict]] = {}
    for group in [g for g in group_order if g in merged] + [
        g for g in merged if g not in group_order
    ]:
        items = merged[group]
        ordered: dict[str, dict] = {}
        for name in items_in_group.get(group, {}):
            if name in items:
                ordered[name] = items[name]
        for name, entry in items.items():
            ordered.setdefault(name, entry)
        ordered_groups[group] = ordered
    return ordered_groups


def summarize(
    requests: list[dict],
    catalog: Catalog,
    from_week: str,
    to_week: str,
    base: str | None = None,
    group: str | None = None,
    section: str | None = None,
) -> dict:
    """Свод заявок за период недель (ТЗ F8/E/F11): слияние недельных агрегатов.

    requests — уже отфильтрованный по базе/точке/диапазону список заявок
    (роутер возьмёт store.list_range(from_week, to_week, base, point));
    фильтры base/point здесь не применяются — base служит только для
    подписи и выбора точек шапки.

    Заявки группируются по (base, week_start); каждая группа проходит
    aggregate_requests с category=None (учитываются только status == 'sent'
    и строки qty > 0 — черновики отсекаются там же), результаты сливаются:

      'solutions' — растворы поточково: {препарат: {точка: qty, ...,
          'ИТОГО': сумма за период, 'unit': единица из снимка}}; точки —
          объединение catalog.points(base) всех затронутых баз в порядке
          каталога;
      'groups' — остальные группы суммарно: {группа: {препарат:
          {'unit': единица, 'qty': сумма за период}}}.

    section-фильтр (раздел): section == CATEGORY_SOLUTIONS («растворы») —
    оставить только 'solutions' (groups={}); section == CATEGORY_MEDICAMENTS
    («медикаменты») — только 'groups' (solutions={}); None — оба раздела.

    group-фильтр: group == SOLUTIONS_GROUP («Растворы») — оставить только
    'solutions' (groups={}); любой другой group — только эта группа в
    'groups' (solutions={}); group не задан — всё.

    Итоговый словарь совместим с agg из report.py (build_xlsx и html_table
    работают без изменений): 'solutions', 'groups', 'points',
    'week_label' (= period_label — подпись периода для шапки отчёта),
    'base', 'generated_at', плюс специфичные для аналитики
    'period_label', 'from_week', 'to_week', 'group', 'section'.

    Пустой период (нет заявок): пустые solutions/groups; 'points' — все
    точки (базы, если base задан, иначе все базы каталога).
    """
    # Группировка заявок по (base, week_start) — каждой группе свой агрегат.
    by_key: dict[tuple[str, str], list[dict]] = {}
    for req in requests:
        by_key.setdefault((req.get("base", ""), req.get("week_start", "")), []).append(req)
    aggs = [
        aggregate_requests(reqs, key_base, None, key_week, catalog)
        for (key_base, key_week), reqs in by_key.items()
    ]

    # Точки периода: базы, затронутые выборкой, в порядке каталога.
    if base is not None:
        points = catalog.points(base)
    else:
        touched = [b for b in catalog.bases() if any(key[0] == b for key in by_key)]
        points = [p for b in touched for p in catalog.points(b)]
        if not points:
            points = [p for b in catalog.bases() for p in catalog.points(b)]

    solutions = _merge_solutions(aggs, catalog, points)
    groups = _merge_groups(aggs, catalog)

    # section-фильтр: растворы — только поточковый свод; медикаменты — только
    # группы; None — оба раздела.
    if section is not None:
        if section == CATEGORY_SOLUTIONS:
            groups = {}
        elif section == CATEGORY_MEDICAMENTS:
            solutions = {}

    # group-фильтр: «Растворы» — только поточковый свод; иначе одна группа.
    if group is not None:
        if group == SOLUTIONS_GROUP:
            groups = {}
        else:
            solutions = {}
            groups = {group: groups[group]} if group in groups else {}

    return {
        "solutions": solutions,
        "groups": groups,
        "points": points,
        "period_label": period_label(from_week, to_week),
        "week_label": period_label(from_week, to_week),  # шапка build_xlsx/html_table
        "from_week": from_week,
        "to_week": to_week,
        "base": base,
        "group": group,
        "section": section,
        "generated_at": datetime.now().isoformat(),
    }
