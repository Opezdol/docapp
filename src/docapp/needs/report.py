"""Отчёт-форма для аптеки: агрегация заявок + .xlsx + HTML-представление.

Ядро подприложения (ТЗ F7, задача T4): по закрытии недели старшая получает
отчёт по базе — .xlsx на два листа («Растворы» — поточково, «Потребность» —
остальные группы суммарно, с подытогами) и HTML-таблицу для просмотра.

Агрегация работает по снимкам строк (item/unit/grp фиксируются при
сохранении заявки, ТЗ: правки каталога не ломают историю и прошлые отчёты).
В отчёт попадают только отправленные заявки (status == 'sent') и строки
с qty > 0; черновики и позиции без количества игнорируются (ТЗ п.8).
"""

import html
from datetime import date, datetime, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from docapp.needs.catalog import (
    CATEGORY_MEDICAMENTS,
    CATEGORY_SOLUTIONS,
    SOLUTIONS_GROUP,
    Catalog,
)

#: Отображаемое имя секции для строк, чья снимковая группа пуста (препарата
#: нет в каталоге на момент сохранения). Используется только в .xlsx/HTML;
#: в данных агрегации группа остаётся как в снимке ('').
UNKNOWN_GROUP_LABEL = "Без группы"


def week_label(week_start: str) -> str:
    """Понедельник недели 'YYYY-MM-DD' -> 'ДД.ММ.ГГГГ – ДД.ММ.ГГГГ' (пн–вс).

    Пример: '2026-08-10' -> '10.08.2026 – 16.08.2026'. Тире — en-dash
    с пробелами с обеих сторон.
    """
    monday = date.fromisoformat(week_start)
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%d.%m.%Y')} – {sunday.strftime('%d.%m.%Y')}"


def _group_of(catalog: Catalog, item: str) -> str:
    """Группа препарата по каталогу; '' если позиции в каталоге нет.

    Подстраховка для строк с пустым снимком группы (строки, сохранённые
    напрямую в хранилище в обход сервиса). Отчёты строятся по снимкам,
    поэтому каталог используется только при отсутствии снимка.
    """
    for group, items in catalog.groups().items():
        if item in items:
            return group
    return ""


def aggregate_requests(
    requests: list[dict], base: str, category: str | None, week_start: str, catalog: Catalog
) -> dict:
    """Агрегировать заявки базы за неделю в отчёт-форму для аптеки (ТЗ F7/F9).

    Учитываются только заявки со status == 'sent' (и base == base) и строки
    с qty > 0; строки без количества и черновики в отчёт не попадают.

    Параметр category — раздел отчёта: CATEGORY_SOLUTIONS («растворы») —
    только поточковый свод (groups пусто); CATEGORY_MEDICAMENTS
    («медикаменты») — только свод по группам (solutions пусто); None — оба
    раздела (аналитика «Все»).

    'solutions' — особая группа SOLUTIONS_GROUP поточково:
        {препарат: {точка: qty, ..., 'ИТОГО': сумма, 'unit': единица из снимка}}.
    Колонки-точки — все точки базы из каталога (catalog.points(base)) в порядке
    каталога; у точки, где раствор не заказывали, — qty 0. Позиция с суммарным
    qty == 0 в отчёт не попадает (черновики и «нулевые» строки не дают данных).

    'groups' — остальные группы суммарно по препарату:
        {группа: {препарат: {'unit': единица из снимка, 'qty': сумма}}}.
    Группы — в порядке каталога (незнакомые каталогу, появившиеся только
    в снимках, — после них); препарат попадает в отчёт только при сумме > 0.

    Плюс служебные ключи: 'points' (точки базы для шапки), 'week_label',
    'base', 'week_start', 'generated_at' (datetime.now().isoformat()).
    """
    points = catalog.points(base)
    point_set = set(points)

    # Порядок вывода — как в каталоге (YAML-файле).
    solution_names = [item.name for item in catalog.solutions_items()]
    group_order = [group for group in catalog.groups() if group != SOLUTIONS_GROUP]
    items_in_group = catalog.groups()

    solutions: dict[str, dict] = {}
    by_group: dict[str, dict[str, dict]] = {}

    for req in requests:
        if req.get("status") != "sent" or req.get("base") != base:
            continue  # черновики и заявки других баз в отчёт не попадают
        point = req.get("point", "")
        if point not in point_set:
            continue  # точка вне каталога базы — в колонки некуда положить
        for line in req.get("lines", []):
            qty = line.get("qty", 0)
            if not isinstance(qty, (int, float)) or qty <= 0:
                continue  # строки без количества в отчёт не попадают (ТЗ п.8)
            item = line.get("item", "")
            if not item:
                continue
            unit = line.get("unit", "")
            # Группа — из снимка строки; пустой снимок подстраховываем каталогом.
            group = line.get("grp", "") or _group_of(catalog, item)
            if group == SOLUTIONS_GROUP:
                row = solutions.setdefault(item, {"unit": unit})
                row[point] = row.get(point, 0) + qty
                row["ИТОГО"] = row.get("ИТОГО", 0) + qty
            else:
                entries = by_group.setdefault(group, {})
                entry = entries.setdefault(item, {"unit": unit, "qty": 0})
                entry["qty"] += qty
                if not entry["unit"] and unit:
                    entry["unit"] = unit

    # Растворы: позиции с суммой > 0 в порядке каталога; недостающие точки = 0.
    solutions = {
        name: solutions[name]
        for name in solution_names
        if name in solutions and solutions[name]["ИТОГО"] > 0
    }
    for row in solutions.values():
        for point in points:
            row.setdefault(point, 0)

    # Остальные группы: порядок каталога, затем незнакомые (по снимкам);
    # препарат с суммарным qty == 0 не попадает.
    groups: dict[str, dict[str, dict]] = {}
    known_groups = [g for g in group_order if g in by_group]
    unknown_groups = [g for g in by_group if g not in group_order]
    for group in known_groups + unknown_groups:
        items = {name: entry for name, entry in by_group[group].items() if entry["qty"] > 0}
        if not items:
            continue
        # Препараты внутри группы — в порядке каталога, неизвестные — после.
        ordered: dict[str, dict] = {}
        for name in items_in_group.get(group, {}):
            if name in items:
                ordered[name] = items[name]
        for name, entry in items.items():
            ordered.setdefault(name, entry)
        groups[group] = ordered

    # Раздел отчёта: растворы — только поточковый свод, медикаменты — только
    # группы, None — оба (аналитика «Все»).
    if category == CATEGORY_SOLUTIONS:
        groups = {}
    elif category == CATEGORY_MEDICAMENTS:
        solutions = {}

    return {
        "solutions": solutions,
        "groups": groups,
        "points": points,
        "week_label": week_label(week_start),
        "base": base,
        "week_start": week_start,
        "generated_at": datetime.now().isoformat(),
    }


def build_xlsx(agg: dict) -> bytes:
    """Собрать .xlsx отчёта (openpyxl) и вернуть bytes.

    Рендерит ровно те секции, что есть в агрегате: непустые 'solutions' —
    лист «Растворы» (шапка база/неделя/дата, таблица
    «Раствор | Ед. | <точки> | ИТОГО»); непустые 'groups' — лист
    «Медикаменты» (секции по группам с подытогом «Итого по группе: N»);
    оба — два листа. Жирные заголовки и разумные ширины — без излишеств.
    """
    wb = Workbook()
    bold = Font(bold=True)

    sections: list[tuple[str, str]] = []
    if agg["solutions"]:
        sections.append(("Растворы", "solutions"))
    if agg["groups"]:
        sections.append(("Медикаменты", "groups"))

    first = True
    for title, kind in sections:
        ws = wb.active if first else wb.create_sheet(title)
        assert ws is not None  # активный/созданный лист всегда есть
        ws.title = title
        if kind == "solutions":
            ws.append([f"База: {agg['base']}"])
            ws.append([f"Неделя: {agg['week_label']}"])
            ws.append([f"Сформирован: {agg['generated_at']}"])
            ws.append([])  # разделитель между шапкой и таблицей
            points = agg["points"]
            ws.append(["Раствор", "Ед."] + points + ["ИТОГО"])
            for cell in ws[ws.max_row]:
                cell.font = bold
            for item, row in agg["solutions"].items():
                ws.append(
                    [item, row.get("unit", "")]
                    + [row.get(point, 0) for point in points]
                    + [row["ИТОГО"]]
                )
            widths = [32, 8] + [max(12, len(point) + 2) for point in points] + [10]
            for idx, width in enumerate(widths, start=1):
                ws.column_dimensions[get_column_letter(idx)].width = width
        else:
            for group, items in agg["groups"].items():
                ws.append([group or UNKNOWN_GROUP_LABEL])
                ws[ws.max_row][0].font = bold
                for item, entry in items.items():
                    ws.append([item, entry["unit"], entry["qty"]])
                total = sum(entry["qty"] for entry in items.values())
                ws.append([f"Итого по группе: {total}"])
            ws.column_dimensions["A"].width = 32
            ws.column_dimensions["B"].width = 8
            ws.column_dimensions["C"].width = 12
        first = False

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def html_table(agg: dict) -> str:
    """HTML-фрагмент отчёта (без <html>/<head>) для просмотра на экране.

    Те же данные, что в .xlsx: растворы — таблицей с ИТОГО, остальные группы —
    секциями с подытогом. Все строковые значения проходят html.escape:
    данные приходят из каталога/БД и не должны ломать разметку.

    Таблицы получают классы из needs.css (.report-table, .report-subtotal,
    .report-total-row) — шапка и колонки выровнены, подытоги выделены.
    Заголовок «База/Неделя/сформирован» сюда НЕ входит: его выводит
    report.html (единый источник, без дублирования).
    """
    lines: list[str] = []
    points = agg["points"]

    # Растворы: таблица препарат × точки + ИТОГО (только если секция непуста).
    if agg["solutions"]:
        lines.append('<h3 class="report-group">Растворы</h3>')
        lines.append('<div class="table-wrap">')
        lines.append('<table class="needs-table report-table">')
        lines.append("<thead><tr>")
        lines.append('<th class="report-name">Раствор</th><th class="report-unit">Ед.</th>')
        for point in points:
            lines.append(f'<th class="report-num">{html.escape(point)}</th>')
        lines.append('<th class="report-num">ИТОГО</th>')
        lines.append("</tr></thead><tbody>")
        for item, row in agg["solutions"].items():
            lines.append(
                f'<tr><td class="report-name">{html.escape(item)}</td>'
                f'<td class="report-unit">{html.escape(row.get("unit", ""))}</td>'
            )
            for point in points:
                lines.append(f'<td class="report-num">{row.get(point, 0)}</td>')
            lines.append(f'<td class="report-num report-total">{row["ИТОГО"]}</td></tr>')
        lines.append("</tbody></table>")
        lines.append("</div>")

    # Остальные группы: секции с подытогом (только если секция непуста).
    for group, items in agg["groups"].items():
        lines.append(f'<h3 class="report-group">{html.escape(group or UNKNOWN_GROUP_LABEL)}</h3>')
        lines.append('<div class="table-wrap">')
        lines.append('<table class="needs-table report-table">')
        lines.append('<thead><tr><th class="report-name">Препарат</th>'
                     '<th class="report-unit">Ед.</th><th class="report-num">Кол-во</th></tr></thead>')
        lines.append("<tbody>")
        total = 0
        for item, entry in items.items():
            total += entry["qty"]
            lines.append(
                f'<tr><td class="report-name">{html.escape(item)}</td>'
                f'<td class="report-unit">{html.escape(entry["unit"])}</td>'
                f'<td class="report-num">{entry["qty"]}</td></tr>'
            )
        lines.append(
            f'<tr class="report-subtotal"><td class="report-name" colspan="2">Итого по группе</td>'
            f'<td class="report-num">{total}</td></tr>'
        )
        lines.append("</tbody></table>")
        lines.append("</div>")

    return "\n".join(lines)
