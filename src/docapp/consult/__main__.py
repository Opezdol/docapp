"""CLI сборки индекса консультанта.

    python -m docapp.consult <папка_с_приказами>

Читает конфигурацию из окружения (CONSULT_*), пересобирает индекс из всех
.docx/.pdf в папке и печатает результат. Код возврата: 0 — успех,
1 — нет аргументов.
"""

from __future__ import annotations

import sys

from docapp.consult.build import build_index
from docapp.consult.config import load_consult_config
from docapp.consult.store import SqliteConsultStore


def main(argv: list[str] | None = None) -> int:
    """Собрать индекс из папки документов, заданной первым аргументом.

    argv — аргументы командной строки (без имени программы); при None
    берутся sys.argv[1:]. Возвращает код возврата: 0 — успех, 1 — ошибка.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Использование: python -m docapp.consult <папка_с_приказами>")
        return 1
    config = load_consult_config()
    db_path = config.index_dir / "consult.db"
    n_chunks = build_index(args[0], config, db_path)
    with SqliteConsultStore(db_path) as store:
        n_docs = len(store.list_documents())
    print(f"Индексировано {n_chunks} фрагментов из {n_docs} документов в {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
