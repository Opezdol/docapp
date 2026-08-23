"""Сброс логинов и паролей всех сотрудников.

Логины перегенерируются по схеме «фамилияИО» (транслит фамилии + первая
буква имени + первая буква отчества), напр. Иванова Елена Вячеславовна
-> ivanovaev, Чупалаева Альбина Мурадбеговна -> chupalaevaam.
Если отчества нет — «фамилия + первая буква имени» (amanbaeva).
Пароли — новые случайные (8 символов). Старые пароли перестают работать.

Вывод: ФИО;логин;пароль;роль;табельный (CSV, разделитель ;).

Запуск: .venv/bin/python scripts/reset_credentials.py [--db data/docapp.db] [--out FILE.csv]
"""

import argparse
import csv
import secrets
import string
import sys
from pathlib import Path

from docapp.auth.passwords import hash_password

_PASSWORD_CHARS = string.ascii_letters + string.digits

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

ROLE_NAMES = {
    "doctor": "врач",
    "nurse": "медсестра",
    "head": "заведующий",
    "head_nurse": "старшая сестра",
}


def translit(text: str) -> str:
    return "".join(_TRANSLIT.get(ch, "") for ch in text.lower())


def random_password(length: int = 8) -> str:
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description="Сброс логинов и паролей")
    parser.add_argument("--db", type=Path, default=Path("data/docapp.db"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    import sqlite3

    db = args.db
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    employees = conn.execute(
        "SELECT id, last_name, first_name, middle_name, role, buh_id FROM employees ORDER BY id"
    ).fetchall()

    used_logins: dict[str, int] = {}
    rows = []
    updated = 0

    for emp in employees:
        last = emp["last_name"]
        first = emp["first_name"]
        middle = emp["middle_name"] or ""

        base = translit(last)
        if not base:
            base = translit(first) or "user"
        # фамилия + первая буква имени (+ первая буква отчества, если есть)
        initials = translit(first)[:1]
        if middle:
            initials += translit(middle)[:1]
        login = base + initials

        # уникализация при коллизии (напр. однофамильцы-тёзки)
        n = 2
        while login in used_logins:
            login = f"{base}{initials}{n}"
            n += 1
        used_logins[login] = emp["id"]

        password = random_password()
        password_hash = hash_password(password)

        conn.execute(
            "UPDATE employees SET login = ?, password_hash = ? WHERE id = ?",
            (login, password_hash, emp["id"]),
        )
        updated += 1

        rows.append({
            "name": f"{last} {first} {middle}".strip(),
            "login": login,
            "password": password,
            "role": ROLE_NAMES.get(emp["role"], emp["role"]),
            "buh_id": emp["buh_id"],
        })

    conn.commit()
    conn.close()

    # Вывод
    print(f"{'ФИО':<40} {'Логин':<20} {'Пароль':<12} {'Роль'}")
    print("-" * 100)
    for r in rows:
        print(f"{r['name']:<40} {r['login']:<20} {r['password']:<12} {r['role']}")

    print("-" * 100)
    print(f"Сброшено: {updated} сотрудников")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["ФИО", "Логин", "Пароль", "Роль", "Табельный"])
            for r in rows:
                w.writerow([r["name"], r["login"], r["password"], r["role"], r["buh_id"]])
        print(f"Таблица сохранена в {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
