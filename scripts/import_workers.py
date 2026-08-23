"""Импорт сотрудников из workers.json в БД docapp.

Читает workers.json (ФИО, табельный номер, роль), генерирует для каждого
логин (транслитерация фамилии, уникальный) и случайный пароль, создаёт
сотрудников через SqliteEmployeeStore и печатает таблицу:

    ФИО | логин | пароль | роль

Запуск (из корня docapp):
    .venv/bin/python scripts/import_workers.py [--json workers.json] [--db data/docapp.db]

Идемпотентность: если сотрудник с таким табельным номером (buh_id) уже
существует — пропускается. Логины уникализируются суффиксом при совпадении.
"""

import argparse
import json
import secrets
import string
import sys
from pathlib import Path

from docapp.auth.passwords import hash_password
from docapp.domain.employee import DOCTOR, HEAD, HEAD_NURSE, NURSE, Employee
from docapp.storage.sqlite_store import SqliteEmployeeStore

_PASSWORD_CHARS = string.ascii_letters + string.digits

# Таблица транслитерации (для логинов): кириллица -> латиница
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

ROLE_NAMES = {
    DOCTOR: "врач",
    NURSE: "медсестра",
    HEAD: "заведующий",
    HEAD_NURSE: "старшая сестра",
}


def translit(text: str) -> str:
    """Транслитерация русского текста в латиницу (для логина)."""
    out = []
    for ch in text.lower():
        out.append(_TRANSLIT.get(ch, ""))
    return "".join(out)


def random_password(length: int = 8) -> str:
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))


def load_workers(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт сотрудников в docapp")
    parser.add_argument("--json", type=Path, default=Path("workers.json"))
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--no-create", action="store_true",
        help="Только показать таблицу логинов/паролей, не создавать в БД",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Сохранить таблицу (ФИО;логин;пароль;роль) в CSV-файл",
    )
    args = parser.parse_args()

    if not args.json.exists():
        print(f"Ошибка: {args.json} не найден", file=sys.stderr)
        return 1

    workers = load_workers(args.json)
    db = args.db if args.db else Path("data/docapp.db")

    # Считаем частоту фамилий: если фамилия встречается несколько раз,
    # всем носителям даём логин с инициалами (фамилия + 1-я буква имени + отчества),
    # иначе логин = просто фамилия.
    from collections import Counter
    last_counts = Counter(w["last_name"] for w in workers)

    # Уникальные логины
    used_logins = set()
    # Уже существующие табельные (для идемпотентности)
    existing_buh = set()

    if not args.no_create:
        # открываем БД, собираем существующих
        import sqlite3
        if db.exists():
            conn = sqlite3.connect(str(db))
            for (buh,) in conn.execute("SELECT buh_id FROM employees WHERE buh_id IS NOT NULL"):
                existing_buh.add(buh)
            for (login,) in conn.execute("SELECT login FROM employees WHERE login IS NOT NULL"):
                used_logins.add(login)
            conn.close()

    rows = []
    skipped = 0
    created = 0

    for w in workers:
        last = w["last_name"]
        first = w["first_name"]
        middle = w.get("middle_name", "")
        role = w.get("role", NURSE)
        buh_id = w.get("buh_id") or None

        if buh_id in existing_buh:
            skipped += 1
            continue

        # логин: фамилия транслитом; если фамилия не уникальна в списке —
        # сразу добавляем инициалы (первая буква имени + первая буква отчества),
        # напр. Иванова Елена -> ivanovaev, Иванова Татьяна -> ivanovatv
        base = translit(last)
        if not base:
            base = translit(first) or "user"
        if last_counts[last] > 1:
            initials = (translit(first)[:1] + translit(middle)[:1])
            login = base + initials
        else:
            login = base
        n = 2
        while login in used_logins:
            login = f"{base}{n}"
            n += 1
        used_logins.add(login)

        password = random_password()
        password_hash = hash_password(password)

        employee = Employee(
            last_name=last,
            first_name=first,
            middle_name=middle,
            role=role,
            login=login,
            password_hash=password_hash,
            buh_id=buh_id,
        )

        if not args.no_create:
            with SqliteEmployeeStore(db) as store:
                store.add(employee)
            created += 1

        rows.append({
            "name": employee.full_name,
            "login": login,
            "password": password,
            "role": role,
            "buh_id": buh_id,
        })

    # Вывод таблицы
    print(f"{'ФИО':<40} {'Логин':<20} {'Пароль':<12} {'Роль'}")
    print("-" * 100)
    for r in rows:
        print(f"{r['name']:<40} {r['login']:<20} {r['password']:<12} {ROLE_NAMES.get(r['role'], r['role'])}")

    print("-" * 100)
    print(f"Всего в списке: {len(workers)} | создано: {created} | пропущено (уже есть): {skipped}")

    # Сохранить CSV, если просили (пароли видны только здесь/в этом файле)
    if args.out:
        import csv
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["ФИО", "Логин", "Пароль", "Роль", "Табельный"])
            for r in rows:
                w.writerow([r["name"], r["login"], r["password"],
                            ROLE_NAMES.get(r["role"], r["role"]), r.get("buh_id", "")])
        print(f"Таблица сохранена в {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
