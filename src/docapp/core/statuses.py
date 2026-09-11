"""Статусы шагов работы: черновик → отправлено → закрыто (ADR-0018).

Один узор на «Потребности» и «Дежурства». Модуль объявляет свою таблицу
переходов (`needs/statuses.py`, `duty/statuses.py`), каркас даёт механику:
`can(status, action, role)`, `next_status(...)`, `actions(...)`.

Роли здесь не перечисляются: в правилах указывается **разрешение** из таблицы
`core/access` (там единственное место, где роль связана с правами, ADR-0023).
Иначе роли расползлись бы во вторую таблицу — ровно то, что перестройка убирает.

Роль можно не указывать (`role=None`): тогда проверяется только статус. Так
делают модули, у которых право на действие уже проверено роутером (например,
ввод отчёта дежурства доступен только врачу — это проверка доступа, а не
перехода).

Правила не конфигурируются из базы: таблица — это код модуля (ADR-0018).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from docapp.core import access

#: Статусы шагов работы. Значения — те же строки, что уходят в интерфейс.
DRAFT = "draft"
SENT = "sent"
CLOSED = "closed"

#: «Записи нет»: пустая точка «Потребностей» или отсутствующий отчёт.
NONE = "none"

#: Период открыт (неделя «Потребностей»): живёт не в заявке, а в отдельном
#: предмете — неделя закрывается целиком (needs_closures).
OPEN = "open"


@dataclass(frozen=True)
class Rule:
    """Один переход: действие, из каких статусов, кем разрешено, во что ведёт.

    `permissions` — разрешения из `core/access`: действие доступно, если у роли
    есть хотя бы одно из них (правило «или»). Пустой кортеж — действие без
    ограничения по ролям: модуль проверяет право сам.
    """

    action: str
    from_statuses: tuple[str, ...]
    to_status: str
    permissions: tuple[str, ...] = ()


class Transitions:
    """Таблица переходов одного предмета: заявка, отчёт, неделя.

    Предмет у модуля может быть не один: у «Потребностей» это заявка раздела и
    неделя (открыта/закрыта) — для каждого своя таблица.
    """

    def __init__(self, rules: Iterable[Rule]) -> None:
        self._rules: dict[str, Rule] = {}
        for rule in rules:
            if rule.action in self._rules:
                raise ValueError(f"Действие {rule.action!r} объявлено дважды")
            self._rules[rule.action] = rule

    @property
    def actions(self) -> tuple[str, ...]:
        """Все действия таблицы — в порядке объявления правил."""
        return tuple(self._rules)

    def rule(self, action: str) -> Rule | None:
        """Правило действия или None, если такого действия нет."""
        return self._rules.get(action)

    def can(self, status: str, action: str, role: str | None = None) -> bool:
        """Разрешён ли переход: статус подходит и (если задана роль) есть право."""
        rule = self._rules.get(action)
        if rule is None or status not in rule.from_statuses:
            return False
        if not rule.permissions or role is None:
            return True
        return access.has(role, *rule.permissions)

    def next_status(self, status: str, action: str, role: str | None = None) -> str | None:
        """Новый статус после перехода или None, если переход не разрешён."""
        if not self.can(status, action, role):
            return None
        rule = self._rules[action]
        return rule.to_status

    def allowed_actions(self, status: str, role: str | None = None) -> tuple[str, ...]:
        """Действия, доступные в этом статусе (для интерфейса и проверок)."""
        return tuple(a for a in self._rules if self.can(status, a, role))
