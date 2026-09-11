"""Перенос данных с прежних баз на единую и проверка результата (шаг 4b, ADR-0016).

Одноразовый инструмент, а не рабочий модуль приложения: он читает четыре прежние
базы и складывает их в `data/docapp.db`. Запуск — только на копиях прежних баз
(`docapp import-legacy --core … --needs … --wiki … --duty …`), проверка результата —
`docapp check`.
"""

from docapp.legacy.checks import CheckReport, check
from docapp.legacy.importer import (
    ImportReport,
    LegacyImportError,
    find_orphans,
    import_legacy,
)

__all__ = [
    "CheckReport",
    "ImportReport",
    "LegacyImportError",
    "check",
    "find_orphans",
    "import_legacy",
]
