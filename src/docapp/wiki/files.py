"""PDF-источники «Компендиума» на диске (ADR-0020).

Источник — файл, а не BLOB в БД: PDF весит мегабайты, из-за него каждая выгрузка
базы и каждый бэкап раздуваются, а SQLite держит его в страницах вместе с
данными отделения.

Имя файла в хранилище — хеш содержимого:

- в имени не остаётся ничего лишнего: в отделении попадаются PDF, названные по
  пациенту, а имя файла живёт в файловой системе и в бэкапах;
- одинаковые файлы не дублируются: хеш совпал — файл уже лежит;
- имя не зависит от порядка и времени загрузки.

Имя, под которым файл загрузили, хранит БД (`wiki_sources.filename`) и
показывает человеку; на диске оно не используется.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Расширение файлов хранилища (принимаем только PDF).
SUFFIX = ".pdf"

#: Длина имени файла: 32 шестнадцатеричных знака хеша — с запасом.
DIGEST_LENGTH = 32


class SourceFileError(RuntimeError):
    """Файл источника недоступен: нет на диске или имя недопустимо."""


def _checked(name: str) -> str:
    """Имя файла из хранилища: без каталогов и переходов вверх.

    Значение приходит из БД, а не от человека, но проверка дешёвая: имя попадает
    в путь, и «../../secret.key» в нём означало бы чтение чужого файла.
    """
    if not name or Path(name).name != name or name in {".", ".."}:
        raise SourceFileError(f"недопустимое имя файла источника: {name!r}")
    return name


def path_of(sources_dir: str | Path, name: str) -> Path:
    """Путь к файлу источника в хранилище."""
    return Path(sources_dir) / _checked(name)


def exists(sources_dir: str | Path, name: str) -> bool:
    """Есть ли файл в хранилище (пустое имя — файла нет)."""
    if not name:
        return False
    return path_of(sources_dir, name).is_file()


def store(data: bytes, sources_dir: str | Path) -> str:
    """Положить PDF в хранилище и вернуть имя файла на диске.

    Запись идёт через временный файл и `replace`: прерванная запись не оставит
    половину PDF под правильным именем.
    """
    name = f"{hashlib.sha256(data).hexdigest()[:DIGEST_LENGTH]}{SUFFIX}"
    directory = Path(sources_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if not path.exists():
        temporary = directory / f".{name}.part"
        temporary.write_bytes(data)
        temporary.replace(path)
    return name


def read(sources_dir: str | Path, name: str) -> bytes:
    """Прочитать PDF из хранилища."""
    path = path_of(sources_dir, name)
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise SourceFileError(f"файл источника не найден: {path}") from exc


def remove(sources_dir: str | Path, name: str) -> None:
    """Убрать файл источника (нет файла — не ошибка)."""
    if not name:
        return
    path_of(sources_dir, name).unlink(missing_ok=True)
