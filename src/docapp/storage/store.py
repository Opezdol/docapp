"""Порты хранилища: контракты для работы с сотрудниками и анестезиями.

Домен не знает про SQLite — он работает с этими интерфейсами.
Реализация может быть любой (SQLite, файл, тестовая память).
"""

from abc import ABC, abstractmethod

from docapp.domain.anesthesia import Anesthesia
from docapp.domain.employee import Employee


class EmployeeStore(ABC):
    """Контракт хранилища сотрудников."""

    @abstractmethod
    def add(self, employee: Employee) -> Employee:
        """Сохранить нового сотрудника (id=None) и вернуть копию с id."""

    @abstractmethod
    def get_by_id(self, employee_id: int) -> Employee | None:
        """Вернуть сотрудника по id или None, если его нет."""

    @abstractmethod
    def get_by_login(self, login: str) -> Employee | None:
        """Вернуть сотрудника по логину или None, если его нет."""

    @abstractmethod
    def list_all(self) -> list[Employee]:
        """Все сотрудники, отсортированы по фамилии, затем имени."""

    @abstractmethod
    def list_nurses(self) -> list[Employee]:
        """Только медсёстры, отсортированы по фамилии, затем имени."""

    @abstractmethod
    def update_buh_id(self, employee_id: int, buh_id: str) -> None:
        """Проставить/заменить номер в бухгалтерии.

        Поднимает KeyError, если сотрудника с таким id нет.
        """


class AnesthesiaStore(ABC):
    """Контракт хранилища записей об анестезиях."""

    @abstractmethod
    def add(self, anesthesia: Anesthesia) -> Anesthesia:
        """Сохранить новую запись (id=None) и вернуть копию с id."""

    @abstractmethod
    def get_by_id(self, anesthesia_id: int) -> Anesthesia | None:
        """Вернуть запись по id или None, если её нет."""

    @abstractmethod
    def list_by_doctor(self, doctor_id: int) -> list[Anesthesia]:
        """Все записи врача, свежие по дате сверху."""

    @abstractmethod
    def list_by_nurse(self, nurse_id: int) -> list[Anesthesia]:
        """Все записи, где сестра — nurse_id, свежие по дате сверху."""

    @abstractmethod
    def update(self, anesthesia: Anesthesia) -> None:
        """Перезаписать запись с тем же id.

        Поднимает KeyError, если записи с таким id нет.
        """

    @abstractmethod
    def delete(self, anesthesia_id: int) -> None:
        """Удалить запись. Несуществующий id — просто ничего."""


class ActiveNurseStore(ABC):
    """Контракт хранилища «активной сестры» врача (ADR-4)."""

    @abstractmethod
    def get_active_nurse(self, doctor_id: int) -> int | None:
        """Вернуть id медсестры, выбранной врачом последней, или None."""

    @abstractmethod
    def set_active_nurse(self, doctor_id: int, nurse_id: int) -> None:
        """Запомнить выбор врача. Повторный вызов перезаписывает (upsert)."""
