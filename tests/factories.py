"""Общие фабрики тестов: единая БД и сотрудники с предсказуемыми id.

Появились на шаге 4, когда база стала одна (ADR-0016): модульные хранилища в
тестах тоже должны открывать общий файл, иначе внешние ключи на `employees` не
работают — «нет таблицы employees» вместо проверки правила модуля.

`init_db` открывает схемы всех модулей (как `create_app`), `seed_employees`
создаёт сотрудников с id 1…10, чтобы тесты могли ссылаться на них как на авторов
заявок, дежурных врачей и кураторов статей.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable

from docapp.core.db import open_db
from docapp.modules import MODULES
from docapp.people.store import SCHEMA as PEOPLE_SCHEMA

#: id сотрудников, которые есть в тестовой БД после `seed_employees`.
#: 1…10 — обычные участники (авторы заявок, дежурные врачи, кураторы статей);
#: 100 и 200 — «старшая сестра» и «заведующий» в тестах сервисов «Потребностей»:
#: в единой БД у любой ссылки должен быть настоящий сотрудник, поэтому эти id
#: тоже существуют.
EMPLOYEE_IDS: tuple[int, ...] = tuple(range(1, 11)) + (100, 200)


def init_db(db_path: str | Path) -> Path:
    """Создать единую БД со схемами всех модулей и вернуть путь к ней."""
    path = Path(db_path)
    for module in MODULES:
        if module.schema is not None:
            open_db(path, module.schema)
    return path


def seed_employees(
    db_path: str | Path, ids: Iterable[int] = EMPLOYEE_IDS
) -> None:
    """Сотрудники с заданными id — на них ссылаются строки модулей.

    Роль значения не имеет: внешний ключ проверяет только существование строки.
    """
    conn = open_db(db_path, PEOPLE_SCHEMA)
    try:
        for employee_id in ids:
            conn.execute(
                "INSERT OR IGNORE INTO employees (id, last_name, first_name, role) "
                "VALUES (?, ?, 'Тест', 'nurse')",
                (employee_id, f"Тестов{employee_id}"),
            )
        conn.commit()
    finally:
        conn.close()


def make_db(tmp_path, *, seed: bool = True) -> Path:
    """Единая БД для модульных тестов: схемы всех модулей (+ сотрудники)."""
    db = init_db(tmp_path / "app.db")
    if seed:
        seed_employees(db)
    return db


def catalog(db_path, yaml_text: str):
    """Каталог расходки из БД, наполненный YAML-текстом.

    Так же, как при первом старте приложения: пустая БД получает каталог из
    seed-файла, дальше источник правды — таблицы (ADR-0019).
    """
    from docapp.needs.catalog_store import SqliteCatalog
    from docapp.needs.catalog_yaml import parse

    cat = SqliteCatalog(db_path)
    cat.import_catalog(parse(yaml_text, source="тесты"), None, source="тесты")
    return cat


#: Шапка ведомости больницы — как в реальном файле (docs/ТЗ-распределение.md).
VEDOMOST_HEADERS = ("Дата оказания услуги", "Ф.И.О. пациента", "№ договора",
                    "Название услуги", "ФИО врача", "Цена, руб", "Кол-во",
                    "Сумма, руб", "Сумма б/с, руб", "Сумма без НДС, руб", "%",
                    "Врач", "СМП", "ММП")
#: Строка шапки в реальной ведомости — седьмая.
VEDOMOST_HEADER_ROW = 7


def vedomost_xlsx(rows, *, period: str = "Период с 01.06.2026 по 30.06.2026") -> bytes:
    """Синтетическая ведомость больницы: структура как у настоящей.

    Строка 1 — заголовок больницы, 4 — период, 7 — шапка, ниже строки услуг,
    последняя — «Итого по отделению». ФИО в фабрике вымышленные: файл владельца
    в репозиторий не попадает — там ФИО пациентов (ADR-0014).

    `rows` — словари с ключами: date, patient, service, doctor, smp, mmp
    (суммы можно не указывать — тогда строка без денег).
    """
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = "Sheet Name Here"
    sheet.cell(row=1, column=1, value="Распределение больницы")
    sheet.cell(row=4, column=1, value=period)
    for column, header in enumerate(VEDOMOST_HEADERS, start=1):
        sheet.cell(row=VEDOMOST_HEADER_ROW, column=column, value=header)

    totals = {"doctor": 0.0, "smp": 0.0, "mmp": 0.0}
    row_number = VEDOMOST_HEADER_ROW
    for row in rows:
        row_number += 1
        sheet.cell(row=row_number, column=1, value=row.get("date"))
        sheet.cell(row=row_number, column=2, value=row["patient"])
        sheet.cell(row=row_number, column=3, value=row.get("contract", "1-16"))
        sheet.cell(row=row_number, column=4, value=row.get("service", "Тотальная внутривенная анестезия(30 мин.)"))
        sheet.cell(row=row_number, column=6, value=row.get("price", 5000))
        sheet.cell(row=row_number, column=7, value=row.get("qty", 1))
        sheet.cell(row=row_number, column=11, value=row.get("percent", 1724))
        for key, column in (("doctor", 12), ("smp", 13), ("mmp", 14)):
            value = row.get(key)
            if value is not None:
                sheet.cell(row=row_number, column=column, value=value)
                if isinstance(value, (int, float)):
                    totals[key] += float(value)

    last = row_number + 1
    sheet.cell(row=last, column=1, value="Итого по отделению:")
    sheet.cell(row=last, column=12, value=round(totals["doctor"], 2))
    sheet.cell(row=last, column=13, value=round(totals["smp"], 2))
    sheet.cell(row=last, column=14, value=round(totals["mmp"], 2))

    out = BytesIO()
    workbook.save(out)
    return out.getvalue()
