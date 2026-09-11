"""Статусы отчёта «Дежурств»: черновик → отправлено → закрыто (ADR-0018).

Права на ввод и отправку проверяет роутер (ввод отчёта — только врачу,
`duty.edit_own`), закрытие и переоткрытие — заведующий (`duty.manage`). Здесь
описано, какие переходы вообще возможны из каждого статуса:

- `draft` — черновик смены, правится и отправляется;
- `sent` — отправлен: правки возможны до закрытия, повторная отправка ничего не
  меняет;
- `closed` — закрыт (заведующим или авто-закрытием в 09:30): только чтение,
  вернуть в работу может лишь переоткрытие.
"""

from __future__ import annotations

from docapp.core import access
from docapp.core.statuses import CLOSED, DRAFT, SENT, Rule, Transitions

#: Действия над отчётом за смену.
EDIT = "edit"
SUBMIT = "submit"
CLOSE = "close"
REOPEN = "reopen"

REPORT = Transitions(
    (
        # Ввод и отправка — только автор отчёта (`duty.edit_own`): заведующий
        # видит все отчёты и закрывает их, но чужой отчёт не вводит.
        Rule(EDIT, (DRAFT, SENT), DRAFT, (access.DUTY_EDIT_OWN,)),
        Rule(SUBMIT, (DRAFT, SENT), SENT, (access.DUTY_EDIT_OWN,)),
        Rule(CLOSE, (DRAFT, SENT), CLOSED, (access.DUTY_MANAGE,)),
        Rule(REOPEN, (CLOSED,), SENT, (access.DUTY_MANAGE,)),
    )
)
