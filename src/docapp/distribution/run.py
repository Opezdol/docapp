"""Прогон ведомости на диске: между «Проверить» и «Опубликовать» (ADR-0027).

Прогон один на раздел и хранится двумя файлами в `DISTRIBUTION_DIR`:

- `run.xlsx` — результат: тот самый файл, который заведующий скачивает;
- `run.json` — метаданные: месяц, когда прогнали, имя исходного файла, id
  совпавших записей (по ним публикация ставит метку «учтена»), счётчики и
  таблицы для страницы, время публикации.

Новый прогон заменяет прежний, поэтому каталог не растёт, а страница раздела
всегда описывает одно состояние. В БД здесь не пишется ничего: история
распределений отсутствует (ADR-0024). Файлы содержат ФИО пациентов, поэтому
каталог не попадает в git (`.gitignore`), но попадает в бэкап вместе с `data/`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

#: Имена файлов прогона: результат и его метаданные.
RUN_FILE = "run.xlsx"
META_FILE = "run.json"


class RunError(ValueError):
    """Прогона нет или он повреждён — понятное сообщение вместо падения."""


def now() -> str:
    """Момент прогона/публикации — локальное время, как его видит заведующий."""
    return datetime.now().isoformat(timespec="seconds")


def human(moment: str | None) -> str:
    """Момент для страницы: «11.10.2026 21:35» вместо ISO."""
    if not moment:
        return ""
    try:
        return datetime.fromisoformat(moment).strftime("%d.%m.%Y %H:%M")
    except ValueError:          # метаданные правили руками — показываем как есть
        return moment


@dataclass(frozen=True)
class Run:
    """Один прогон: что разнесли, когда и с каким итогом."""

    month: str                              # «ГГГГ-ММ», выбран заведующим
    created_at: str                         # когда прогнали
    source_name: str                        # имя загруженного файла — для страницы
    matched_ids: tuple[int, ...]            # id записей, попавших в ведомость
    counters: dict[str, int]                # строки, с парой, «Внимание», без пары, записи
    tables: dict[str, list[dict]]           # врачи, сёстры, строки «Внимание», без ID
    published_at: str | None = None         # когда месяц закрыли

    @property
    def published(self) -> bool:
        """Опубликован ли прогон: метка «учтена» уже у людей."""
        return self.published_at is not None

    def to_dict(self) -> dict:
        """Метаданные для `run.json` — без самого файла результата."""
        return {
            "month": self.month,
            "created_at": self.created_at,
            "source_name": self.source_name,
            "matched_ids": list(self.matched_ids),
            "counters": self.counters,
            "tables": self.tables,
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Run:
        """Метаданные из `run.json`; старые версии не поддерживаем — прогон одноразовый."""
        return cls(
            month=str(data["month"]),
            created_at=str(data["created_at"]),
            source_name=str(data.get("source_name", "")),
            matched_ids=tuple(int(value) for value in data.get("matched_ids", ())),
            counters={str(key): int(value) for key, value in data["counters"].items()},
            tables=data.get("tables", {}),
            published_at=data.get("published_at"),
        )


class RunStore:
    """Хранилище прогона: два файла в каталоге `DISTRIBUTION_DIR`."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        """Каталог прогона — для сообщений об ошибках и для проверок в тестах."""
        return self._directory

    @property
    def _payload_path(self) -> Path:
        return self._directory / RUN_FILE

    @property
    def _meta_path(self) -> Path:
        return self._directory / META_FILE

    def save(self, run: Run, payload: bytes) -> None:
        """Сохранить прогон: сначала файл результата, потом метаданные.

        Порядок не случаен: наличие `run.json` означает, что прогон есть, и
        рядом с ним уже лежит результат. Прежний прогон перезаписывается —
        второй папки и старых копий не остаётся.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        self._payload_path.write_bytes(payload)
        self._write(run)

    def load(self) -> Run | None:
        """Текущий прогон или None, если его ещё не было."""
        if not (self._meta_path.exists() and self._payload_path.exists()):
            return None
        return self._read()

    def payload(self) -> bytes:
        """Файл результата текущего прогона."""
        try:
            return self._payload_path.read_bytes()
        except OSError as exc:
            raise RunError(
                "файл прогона не читается: прогоните ведомость заново"
            ) from exc

    def published(self, run: Run, moment: str) -> Run:
        """Отметить прогон опубликованным; файл результата не трогаем."""
        published = replace(run, published_at=moment)
        self._write(published)
        return published

    def _write(self, run: Run) -> None:
        """Записать метаданные: временный файл рядом, затем замена.

        Так обрыв записи не оставит половину JSON, из которого потом ничего
        не прочитать.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = self._meta_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(run.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._meta_path)

    def _read(self) -> Run:
        try:
            data = json.loads(self._meta_path.read_text(encoding="utf-8"))
            return Run.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RunError(
                "метаданные прогона испорчены: удалите каталог "
                f"{self._directory} и прогоните ведомость заново"
            ) from exc
