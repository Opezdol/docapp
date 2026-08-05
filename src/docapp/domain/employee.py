"""Сотрудник отделения: врач, медсестра, заведующий."""

from dataclasses import dataclass

DOCTOR = "doctor"
NURSE = "nurse"
HEAD = "head"

ROLES = (DOCTOR, NURSE, HEAD)


@dataclass(frozen=True)
class Employee:
    last_name: str
    first_name: str
    role: str
    middle_name: str = ""
    id: int | None = None
    login: str | None = None
    password_hash: str | None = None
    buh_id: str | None = None

    def __post_init__(self) -> None:
        if not self.last_name.strip():
            raise ValueError("last_name не может быть пустым")
        if not self.first_name.strip():
            raise ValueError("first_name не может быть пустым")
        if self.role not in ROLES:
            raise ValueError(f"role должен быть одним из {ROLES}")
        if (self.login is None) != (self.password_hash is None):
            raise ValueError("login и password_hash задаются только вместе")
        if self.buh_id is not None and not self.buh_id.strip():
            raise ValueError("buh_id не может быть пустой строкой")

    @property
    def full_name(self) -> str:
        """Полное ФИО: «Фамилия Имя Отчество» (отчество, если есть)."""
        parts = [self.last_name, self.first_name]
        if self.middle_name.strip():
            parts.append(self.middle_name)
        return " ".join(parts)

    @property
    def short_name(self) -> str:
        """Обращение: «Имя Отчество» (или «Имя», если отчества нет)."""
        parts = [self.first_name]
        if self.middle_name.strip():
            parts.append(self.middle_name)
        return " ".join(parts)
