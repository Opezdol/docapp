"""Точечное переименование логинов сотрудников по id.

Служит для ручной правки логинов, сгенерированных автоматически
(напр. после scripts/import_workers.py или scripts/reset_credentials.py),
когда нужно исправить коллизии/опечатки у конкретных сотрудников.

Запуск (из корня docapp):
    .venv/bin/python scripts/rename_logins.py [--db data/docapp.db] <id> <login> [<id> <login> ...]

После правки печатает результат и проверяет, что дублей логинов не осталось.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Переименование логинов по id")
    parser.add_argument("--db", type=Path, default=Path("data/docapp.db"))
    parser.add_argument(
        "pairs",
        nargs="+",
        metavar="ID LOGIN",
        help="Пары «id сотрудника и новый логин» (чётное число аргументов)",
    )
    args = parser.parse_args()

    if len(args.pairs) % 2 != 0:
        print("Ошибка: аргументы должны идти парами <id> <login>", file=sys.stderr)
        return 2

    changes: list[tuple[int, str]] = []
    for i in range(0, len(args.pairs), 2):
        try:
            emp_id = int(args.pairs[i])
        except ValueError:
            print(f"Ошибка: «{args.pairs[i]}» не число (ожидался id сотрудника)", file=sys.stderr)
            return 2
        changes.append((emp_id, args.pairs[i + 1]))

    conn = sqlite3.connect(str(args.db))
    try:
        for emp_id, new_login in changes:
            cur = conn.execute(
                "UPDATE employees SET login = ? WHERE id = ?", (new_login, emp_id)
            )
            print(f"id={emp_id} -> {new_login} (строк: {cur.rowcount})")
        conn.commit()

        dups = conn.execute(
            "SELECT login, COUNT(*) FROM employees GROUP BY login HAVING COUNT(*) > 1"
        ).fetchall()
        print("дубли логинов:", dups if dups else "нет")

        ids = [emp_id for emp_id, _ in changes]
        placeholders = ",".join("?" for _ in ids)
        for row in conn.execute(
            f"SELECT id, last_name, first_name, middle_name, login "
            f"FROM employees WHERE id IN ({placeholders}) ORDER BY id",
            ids,
        ):
            print(row)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
