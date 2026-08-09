"""CLI сборки индекса консультанта.

    python -m docapp.consult [<папка_с_приказами>]

Читает конфигурацию из окружения (CONSULT_*), пересобирает индекс из всех
.docx/.pdf в папке и печатает результат. Без аргумента индексируется
статичная папка приказов config.docs_dir (CONSULT_DOCS_DIR, по умолчанию
data/consult/documents/) — F9 ТЗ; пустая папка — штатный случай, код
возврата 0 с предупреждением.
"""

from __future__ import annotations

import sys

from docapp.consult.build import build_index
from docapp.consult.config import load_consult_config
from docapp.consult.store import SqliteConsultStore


def main(argv: list[str] | None = None) -> int:
    """Собрать индекс из папки документов, заданной первым аргументом.

    argv — аргументы командной строки (без имени программы); при None
    берутся sys.argv[1:]. Без аргумента — статичная папка config.docs_dir.
    Возвращает код возврата: всегда 0 при успешной сборке (пустая папка —
    не ошибка, печатается предупреждение).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    config = load_consult_config()
    if args:
        docs_dir = args[0]
    else:
        docs_dir = None
        print(f"Папка приказов (по умолчанию): {config.docs_dir}")
    db_path = config.index_dir / "consult.db"
    n_chunks = build_index(docs_dir, config, db_path)
    with SqliteConsultStore(db_path) as store:
        n_docs = len(store.list_documents())
    print(f"Индексировано {n_chunks} фрагментов из {n_docs} документов в {db_path}")
    if n_docs == 0:
        print("Документов не найдено. Положите .docx/.pdf в папку и повторите.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
