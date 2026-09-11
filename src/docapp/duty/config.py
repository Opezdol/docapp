"""Настройки модуля «Дежурства»: env DUTY_TZ, окно смены, базы.

Путь к БД сюда не входит: база одна на всё приложение (ADR-0016), её передаёт
AppContext. Часовой пояс смены — IANA-имя DUTY_TZ (по умолчанию Europe/Moscow);
при отсутствии tz-базы откат на фиксированный UTC+3 (Москва без DST).
"""

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    """Конфигурация «Дежурств»: часовой пояс смены."""

    tz: tzinfo


def load_duty_config() -> DutyConfig:
    """Прочитать DUTY_TZ из окружения и собрать DutyConfig."""
    return DutyConfig(tz=_tz(os.environ.get("DUTY_TZ", "Europe/Moscow")))
