"""Статусы «Потребностей»: заявка раздела и неделя (ADR-0018).

Два предмета со своим статусом:

- **заявка** точки и раздела: черновик → отправлено; если заявки нет — `none`;
- **неделя** (база + раздел): открыта → закрыта. Закрытие — отдельная сущность
  (`needs_closures`), а не статус заявки: закрывается неделя целиком, вместе со
  всеми точками базы (ADR-0018). Здесь оно описано как переход, чтобы право на
  закрытие и переоткрытие жило в одном месте.

Правка и отправка — автор заявки (`needs.edit_own`) или старшая сестра и
заведующий (`needs.manage`). Чья именно заявка, таблица не решает: авторство —
свойство конкретной записи, его проверяет сервис.
"""

from __future__ import annotations

from docapp.core import access
from docapp.core.statuses import (
    CLOSED,
    DRAFT,
    NONE,
    OPEN,
    SENT,
    Rule,
    Transitions,
)

#: Действия над заявкой раздела.
EDIT = "edit"
SUBMIT = "submit"

#: Действия над неделей базы и раздела.
CLOSE = "close"
REOPEN = "reopen"

#: Таблица переходов заявки: черновик ↔ отправлено, создание из пустого статуса.
REQUEST = Transitions(
    (
        Rule(EDIT, (NONE, DRAFT, SENT), DRAFT, (access.NEEDS_EDIT_OWN, access.NEEDS_MANAGE)),
        Rule(
            SUBMIT,
            (DRAFT, SENT),
            SENT,
            (access.NEEDS_EDIT_OWN, access.NEEDS_MANAGE),
        ),
    )
)

#: Таблица переходов недели: закрытие и переоткрытие — старшая сестра и заведующий.
WEEK = Transitions(
    (
        Rule(CLOSE, (OPEN,), CLOSED, (access.NEEDS_MANAGE,)),
        Rule(REOPEN, (CLOSED,), OPEN, (access.NEEDS_MANAGE,)),
    )
)
