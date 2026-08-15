"""Сервис подприложения «Потребности»: бизнес-правила поверх хранилища и каталога.

Слой между SQLite-хранилищем (store.py) и HTTP-роутером (T6): проверка прав,
правила правок из ТЗ F4/F9, снимки unit/grp из каталога в строки заявок,
доска старшей, закрытие/переоткрытие недель с предупреждением
о неотправленных точках (ТЗ F6/A2).

Ошибки прав и закрытой недели бросаются исключениями-подклассами ValueError;
роутер (T6) транслирует их в HTTP 403 (NeedsForbidden) и 409 (NeedsClosed).
"""

from datetime import date, timedelta

from docapp.domain.employee import HEAD, HEAD_NURSE, NURSE
from docapp.needs.catalog import Catalog
from docapp.needs.store import SqliteNeedsStore

#: Роли с полными правами в подприложении: доска, правка чужих заявок,
#: закрытие/переоткрытие недель.
ALLOWED_FULL = (HEAD_NURSE, HEAD)


class NeedsForbidden(ValueError):
    """Нет прав на операцию (роутер отвечает 403)."""


class NeedsClosed(ValueError):
    """Неделя/база закрыта — правки и отправка запрещены (роутер отвечает 409)."""


def monday_of_week(d: date | None = None) -> str:
    """Понедельник недели даты d (по умолчанию — сегодня) как 'YYYY-MM-DD'."""
    if d is None:
        d = date.today()
    return (d - timedelta(days=d.weekday())).isoformat()


class NeedsService:
    """Бизнес-правила подприложения «Потребности».

    store — SQLite-хранилище (SqliteNeedsStore), catalog — YAML-каталог (Catalog).
    Сервис ничего не знает про HTTP: доступность по ролям — исключениями.
    """

    def __init__(self, store: SqliteNeedsStore, catalog: Catalog) -> None:
        self._store = store
        self._catalog = catalog

    # ── внутренние помощники ──────────────────────────────────────────

    def _snapshot_lines(self, lines: list[dict]) -> list[dict]:
        """Снимки строк: unit и grp из каталога по item (ТЗ: отчёты по снимкам).

        unit берётся из unit_of, группа — поиском позиции в all_items().
        Препарат, отсутствующий в каталоге, не роняет сервис: unit='' и grp=''.
        """
        items_by_name = {item.name: item for item in self._catalog.all_items()}
        snapshots: list[dict] = []
        for line in lines:
            name = line.get("item", "")
            catalog_item = items_by_name.get(name)
            snapshots.append(
                {
                    "item": name,
                    "unit": self._catalog.unit_of(name) if catalog_item else "",
                    "grp": catalog_item.group if catalog_item else "",
                    "qty": line.get("qty", 0),
                }
            )
        return snapshots

    def _has_full_rights(self, role: str) -> bool:
        """Полные права (доска, чужие заявки, закрытие): head_nurse/head."""
        return role in ALLOWED_FULL

    # ── заявки ────────────────────────────────────────────────────────

    def get_for_user(
        self,
        user_id: int,
        role: str,
        base: str,
        point: str,
        week_start: str,
    ) -> dict | None:
        """Заявка с учётом прав (ТЗ F4): head_nurse/head — любая.

        Медсестра: своя заявка — как есть; чужая отправленная — как есть;
        чужой черновик — скрыт (None). Заявки нет — None.
        """
        request = self._store.get_request(base, point, week_start)
        if request is None or self._has_full_rights(role):
            return request
        if request["author_id"] == user_id or request["status"] == "sent":
            return request
        return None

    def save(
        self,
        user_id: int,
        role: str,
        base: str,
        point: str,
        week_start: str,
        lines: list[dict],
        status: str = "draft",
    ) -> dict:
        """Создать или отредактировать заявку; возвращает полную заявку.

        Права: автор заявки или роль из ALLOWED_FULL; новая заявка
        создаётся медсестрой или полной ролью. Закрытая неделя — NeedsClosed.
        F9: при правке существующей заявки исходный автор сохраняется
        (store.save_request перезаписывает author_id, поэтому автор
        вычисляется здесь). Строки проходят _snapshot_lines.
        """
        existing = self._store.get_request(base, point, week_start)
        if existing is not None:
            # Правка существующей заявки: автор или полная роль (F4, F9).
            if existing["author_id"] != user_id and not self._has_full_rights(role):
                raise NeedsForbidden(
                    f"Правка чужой заявки ({base}/{point}, {week_start}) разрешена "
                    f"только автору или старшей сестре"
                )
            author_id = existing["author_id"]  # F9: исходного автора не трогаем
        else:
            # Новая заявка: автор — сам пользователь (медсестра или полная роль).
            if role != NURSE and not self._has_full_rights(role):
                raise NeedsForbidden(
                    f"Создание заявки доступно медсестре, старшей сестре или заведующему"
                )
            author_id = user_id
        if self._store.is_closed(base, week_start):
            raise NeedsClosed(
                f"Неделя {week_start} для базы «{base}» закрыта — правки запрещены"
            )
        snapshots = self._snapshot_lines(lines)
        self._store.save_request(
            base, point, week_start, author_id, snapshots, status=status
        )
        saved = self._store.get_request(base, point, week_start)
        assert saved is not None  # заявка только что сохранена
        return saved

    def submit(
        self,
        user_id: int,
        role: str,
        base: str,
        point: str,
        week_start: str,
    ) -> dict:
        """Отправить заявку (статус 'sent'); возвращает заявку и предупреждения.

        Права как у save: автор или ALLOWED_FULL. Закрытая неделя — NeedsClosed.
        Строки с qty == 0 не блокируют отправку (ТЗ F3/F4) — они попадают
        в warnings как 'item: 0'.
        """
        request = self._store.get_request(base, point, week_start)
        if request is None:
            raise NeedsForbidden(
                f"Заявки {base}/{point} за неделю {week_start} нет — отправлять нечего"
            )
        if request["author_id"] != user_id and not self._has_full_rights(role):
            raise NeedsForbidden(
                f"Отправка чужой заявки ({base}/{point}) разрешена только "
                f"автору или старшей сестре"
            )
        if self._store.is_closed(base, week_start):
            raise NeedsClosed(
                f"Неделя {week_start} для базы «{base}» закрыта — отправка запрещена"
            )
        self._store.set_status(request["id"], "sent")
        updated = self._store.get_request(base, point, week_start)
        assert updated is not None  # заявка существует — только что обновлена
        warnings = [
            f"{line['item']}: {line['qty']}"
            for line in updated["lines"]
            if line["qty"] == 0
        ]
        return {"request": updated, "warnings": warnings}

    # ── доска и закрытие недель ───────────────────────────────────────

    def board(self, week_start: str | None = None) -> list[dict]:
        """Доска старшей: каждая точка обеих баз с состоянием заявки.

        week_start по умолчанию — понедельник текущей недели. Точка без
        заявки получает status 'none' и пустые author_id/request_id/updated_at.
        """
        week_start = week_start or monday_of_week()
        cells: list[dict] = []
        for base, points in self._catalog.bases().items():
            for point in points:
                request = self._store.get_request(base, point, week_start)
                if request is None:
                    cells.append(
                        {
                            "base": base,
                            "point": point,
                            "status": "none",
                            "author_id": None,
                            "request_id": None,
                            "updated_at": None,
                        }
                    )
                else:
                    cells.append(
                        {
                            "base": base,
                            "point": point,
                            "status": request["status"],
                            "author_id": request["author_id"],
                            "request_id": request["id"],
                            "updated_at": request["updated_at"],
                        }
                    )
        return cells

    def close(
        self,
        user_id: int,
        role: str,
        base: str,
        week_start: str | None = None,
    ) -> dict:
        """Закрыть неделю для базы (только head_nurse/head).

        Неотправленные точки не блокируют закрытие (ТЗ A2) — они
        возвращаются в 'unsent_points' как предупреждение.
        """
        if not self._has_full_rights(role):
            raise NeedsForbidden(
                f"Закрытие недели доступно только старшей сестре или заведующему"
            )
        week_start = week_start or monday_of_week()
        sent_points = {
            req["point"]
            for req in self._store.list_requests(base, week_start)
            if req["status"] == "sent"
        }
        unsent_points = [
            point for point in self._catalog.points(base) if point not in sent_points
        ]
        self._store.close(base, week_start, user_id)
        return {"closed": True, "unsent_points": unsent_points}

    def reopen(
        self,
        user_id: int,
        role: str,
        base: str,
        week_start: str | None = None,
    ) -> dict:
        """Переоткрыть неделю для базы (только head_nurse/head)."""
        if not self._has_full_rights(role):
            raise NeedsForbidden(
                f"Переоткрытие недели доступно только старшей сестре или заведующему"
            )
        week_start = week_start or monday_of_week()
        self._store.reopen(base, week_start)
        return {"reopened": True}

    def is_closed(self, base: str, week_start: str | None = None) -> bool:
        """Закрыта ли неделя для базы (пасс-тру в хранилище)."""
        return self._store.is_closed(base, week_start or monday_of_week())
