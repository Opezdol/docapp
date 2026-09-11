"""Сервис подприложения «Потребности»: бизнес-правила поверх хранилища и каталога.

Слой между SQLite-хранилищем (store.py) и HTTP-роутером (T6): проверка прав,
правила правок из ТЗ F4/F9, снимки unit/grp из каталога в строки заявок,
доска старшей, закрытие/переоткрытие недель с предупреждением
о неотправленных точках (ТЗ F6/A2).

Статусы заявки и недели описаны таблицей переходов (needs/statuses.py, общий
механизм core/statuses, ADR-0018), неделя считается общим механизмом периода
(core/period): своих `monday_of_week` и кортежа ролей здесь больше нет.

Ошибки прав и закрытой недели бросаются исключениями-подклассами ValueError;
роутер (T6) транслирует их в HTTP 403 (NeedsForbidden) и 409 (NeedsClosed).
"""

from docapp.core import access, period
from docapp.domain.employee import NURSE
from docapp.needs import statuses
from docapp.needs.catalog import (
    CATEGORY_MEDICAMENTS,
    CATEGORY_SOLUTIONS,
    SOLUTIONS_GROUP,
    Catalog,
)
from docapp.needs.store import SqliteNeedsStore

class NeedsForbidden(ValueError):
    """Нет прав на операцию (роутер отвечает 403)."""


class NeedsClosed(ValueError):
    """Неделя/база закрыта — правки и отправка запрещены (роутер отвечает 409)."""


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
        """Полные права (доска, чужие заявки, закрытие) — из таблицы прав.

        Раньше здесь был свой кортеж ролей; теперь решение принимает
        `core/access` — единственное место, где роль связана с правами (ADR-0023).
        """
        return access.has(role, access.NEEDS_MANAGE)

    @staticmethod
    def _validate_category(category: str) -> None:
        """Раздел обязан быть одним из двух известных; иначе ValueError."""
        if category not in (CATEGORY_SOLUTIONS, CATEGORY_MEDICAMENTS):
            raise ValueError(f"Неизвестный раздел: {category}")

    @staticmethod
    def _validate_lines_section(category: str, snapshots: list[dict]) -> None:
        """Каждая строка (по снимку grp) должна соответствовать разделу.

        Раздел «растворы» принимает только строки группы «Растворы»,
        раздел «медикаменты» — только строки остальных групп. Несоответствие
        бросает ValueError (риск расхождения раздела и строк, ТЗ §7).
        """
        for snap in snapshots:
            grp = snap.get("grp", "")
            if category == CATEGORY_SOLUTIONS and grp != SOLUTIONS_GROUP:
                raise ValueError(
                    f"Строка «{snap['item']}» (группа «{grp or 'Без группы'}») "
                    f"не относится к разделу «Растворы»"
                )
            if category == CATEGORY_MEDICAMENTS and grp == SOLUTIONS_GROUP:
                raise ValueError(
                    f"Строка «{snap['item']}» (группа «Растворы») "
                    f"не относится к разделу «Медикаменты»"
                )

    # ── заявки ────────────────────────────────────────────────────────

    def get_for_user(
        self,
        user_id: int,
        role: str,
        base: str,
        point: str,
        category: str,
        week_start: str,
    ) -> dict | None:
        """Заявка раздела с учётом прав (ТЗ F4): head_nurse/head — любая.

        Медсестра: своя заявка — как есть; чужая отправленная — как есть;
        чужой черновик — скрыт (None). Заявки нет — None.
        """
        request = self._store.get_request(base, point, category, week_start)
        if request is None or self._has_full_rights(role):
            return request
        if request["author_id"] == user_id or request["status"] == statuses.SENT:
            return request
        return None

    def points_for_user(
        self,
        user_id: int,
        role: str,
        category: str,
        week_start: str | None = None,
    ) -> list[dict]:
        """Статусы всех точек раздела с точки зрения пользователя (цветовая индикация).

        Для медсестры: своя заявка — draft/sent, чужая отправленная — sent,
        чужой черновик и отсутствие заявки — 'none' (как get_for_user).
        head_nurse/head видят всё (как board, но без авторства).
        """
        self._validate_category(category)
        week_start = week_start or period.week_start()
        cells: list[dict] = []
        for base, points in self._catalog.bases().items():
            for point in points:
                request = self.get_for_user(
                    user_id, role, base, point, category, week_start
                )
                cells.append(
                    {
                        "base": base,
                        "point": point,
                        "status": request["status"] if request else statuses.NONE,
                    }
                )
        return cells

    def save(
        self,
        user_id: int,
        role: str,
        base: str,
        point: str,
        category: str,
        week_start: str,
        lines: list[dict],
        status: str = statuses.DRAFT,
    ) -> dict:
        """Создать или отредактировать заявку раздела; возвращает полную заявку.

        Права: автор заявки или полная роль (needs.manage); новая заявка
        создаётся медсестрой или полной ролью. Закрытая неделя — NeedsClosed.
        F9: при правке существующей заявки исходный автор сохраняется
        (store.save_request перезаписывает author_id, поэтому автор
        вычисляется здесь). Строки проходят _snapshot_lines и проверку
        соответствия разделу (растворы ↔ группа «Растворы»).
        """
        self._validate_category(category)
        existing = self._store.get_request(base, point, category, week_start)
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
        if self._store.is_closed(base, category, week_start):
            raise NeedsClosed(
                f"Неделя {week_start} для базы «{base}» закрыта — правки запрещены"
            )
        snapshots = self._snapshot_lines(lines)
        self._validate_lines_section(category, snapshots)
        self._store.save_request(
            base, point, category, week_start, author_id, snapshots, status=status
        )
        saved = self._store.get_request(base, point, category, week_start)
        assert saved is not None  # заявка только что сохранена
        return saved

    def submit(
        self,
        user_id: int,
        role: str,
        base: str,
        point: str,
        category: str,
        week_start: str,
    ) -> dict:
        """Отправить заявку раздела (статус 'sent'); возвращает заявку и предупреждения.

        Права как у save: автор или полная роль. Закрытая неделя — NeedsClosed.
        Строки с qty == 0 не блокируют отправку (ТЗ F3/F4) — они попадают
        в warnings как 'item: 0'.
        """
        request = self._store.get_request(base, point, category, week_start)
        if request is None:
            raise NeedsForbidden(
                f"Заявки {base}/{point} за неделю {week_start} нет — отправлять нечего"
            )
        if request["author_id"] != user_id and not self._has_full_rights(role):
            raise NeedsForbidden(
                f"Отправка чужой заявки ({base}/{point}) разрешена только "
                f"автору или старшей сестре"
            )
        if self._store.is_closed(base, category, week_start):
            raise NeedsClosed(
                f"Неделя {week_start} для базы «{base}» закрыта — отправка запрещена"
            )
        # Переход «черновик → отправлено» объявлен в needs/statuses (ADR-0018):
        # из закрытого статуса отправлять нечего, и это видно по таблице.
        assert statuses.REQUEST.can(request["status"], statuses.SUBMIT, role)
        self._store.set_status(request["id"], statuses.SENT)
        updated = self._store.get_request(base, point, category, week_start)
        assert updated is not None  # заявка существует — только что обновлена
        warnings = [
            f"{line['item']}: {line['qty']}"
            for line in updated["lines"]
            if line["qty"] == 0
        ]
        return {"request": updated, "warnings": warnings}

    # ── доска и закрытие недель ───────────────────────────────────────

    def board(self, week_start: str | None = None) -> list[dict]:
        """Доска старшей: каждая точка обеих баз по каждому разделу.

        week_start по умолчанию — понедельник текущей недели. Точка даёт
        до двух ячеек — по одной на раздел (solutions/medicaments). Точка
        без заявки раздела получает status 'none' и пустые
        author_id/request_id/updated_at.
        """
        week_start = week_start or period.week_start()
        cells: list[dict] = []
        for base, points in self._catalog.bases().items():
            for point in points:
                for category in (CATEGORY_SOLUTIONS, CATEGORY_MEDICAMENTS):
                    request = self._store.get_request(base, point, category, week_start)
                    if request is None:
                        cells.append(
                            {
                                "base": base,
                                "point": point,
                                "category": category,
                                "status": statuses.NONE,
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
                                "category": category,
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
        category: str,
        week_start: str | None = None,
    ) -> dict:
        """Закрыть неделю для базы и раздела (только head_nurse/head).

        Неотправленные точки не блокируют закрытие (ТЗ A2) — они
        возвращаются в 'unsent_points' как предупреждение (по разделу).
        """
        if not statuses.WEEK.can(statuses.OPEN, statuses.CLOSE, role):
            raise NeedsForbidden(
                f"Закрытие недели доступно только старшей сестре или заведующему"
            )
        self._validate_category(category)
        week_start = week_start or period.week_start()
        sent_points = {
            req["point"]
            for req in self._store.list_requests(base, category, week_start)
            if req["status"] == statuses.SENT
        }
        unsent_points = [
            point for point in self._catalog.points(base) if point not in sent_points
        ]
        self._store.close(base, category, week_start, user_id)
        return {"closed": True, "unsent_points": unsent_points}

    def reopen(
        self,
        user_id: int,
        role: str,
        base: str,
        category: str,
        week_start: str | None = None,
    ) -> dict:
        """Переоткрыть неделю для базы и раздела (только head_nurse/head)."""
        if not statuses.WEEK.can(statuses.CLOSED, statuses.REOPEN, role):
            raise NeedsForbidden(
                f"Переоткрытие недели доступно только старшей сестре или заведующему"
            )
        self._validate_category(category)
        week_start = week_start or period.week_start()
        self._store.reopen(base, category, week_start)
        return {"reopened": True}

    def is_closed(self, base: str, category: str, week_start: str | None = None) -> bool:
        """Закрыта ли неделя для базы и раздела (пасс-тру в хранилище)."""
        return self._store.is_closed(base, category, week_start or period.week_start())

    def closed_sections(self, week_start: str | None = None) -> list[dict]:
        """Закрытые разделы недели (base, category) — для отображения закрытий."""
        return self._store.closed_sections(week_start or period.week_start())
