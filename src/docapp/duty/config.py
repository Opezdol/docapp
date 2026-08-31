"""Настройки подприложения «Дежурства»: env DUTY_DB, DUTY_TZ, окно смены, базы.

В духе docapp.config и needs/config.py: os.environ + Path, без pydantic.
Путь БД по умолчанию — data/duty/duty.db (переопределяется DUTY_DB).
Часовой пояс смены — IANA-имя DUTY_TZ (по умолчанию Europe/Moscow); при
отсутствии tz-базы откатывается на фиксированный UTC+3 (Москва без DST).
"""

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB_PATH = "data/duty/duty.db"

#: Базы отделения в порядке вывода секций в разлиновке.
BASES = ("Таймырская", "Ленская")

#: Окно смены (доступ к вкладке и правка): с 16:00 до 09:30 следующего дня.
WINDOW_START = time(16, 0)
WINDOW_END = time(9, 30)

#: Рабочее время дежурства (в него должны укладываться операции): 16:00 → 08:00.
#: 16 часов = 960 минут.
OPERATION_WINDOW_MINUTES = 16 * 60


def _tz(name: str) -> tzinfo:
    """IANA-зона по имени; при отсутствии tz-базы — фиксированный UTC+3."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=3))


@dataclass(frozen=True)
class DutyConfig:
    """Конфигурация «Дежурств»: путь к БД и часовой пояс смены."""

    db_path: Path
    tz: tzinfo


def load_duty_config() -> DutyConfig:
    """Прочитать DUTY_DB и DUTY_TZ из окружения и собрать DutyConfig."""
    db_override = os.environ.get("DUTY_DB")
    tz_name = os.environ.get("DUTY_TZ", "Europe/Moscow")
    return DutyConfig(
        db_path=Path(db_override).resolve() if db_override else Path(DEFAULT_DB_PATH).resolve(),
        tz=_tz(tz_name),
    )
