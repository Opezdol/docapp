"""Сотрудник отделения: врач, медсестра, заведующий."""

from dataclasses import dataclass

DOCTOR = "doctor"
NURSE = "nurse"
HEAD = "head"

ROLES = (DOCTOR, NURSE, HEAD)


@dataclass(frozen=True)
class Employee:
    full_name: str
    role: str
    id: int | None = None
    login: str | None = None
    password_hash: str | None = None
    buh_id: str | None = None

    def __post_init__(self) -> None:
        if not self.full_name.strip():
            raise ValueError("full_name не может быть пустым")
        if self.role not in ROLES:
            raise ValueError(f"role должен быть одним из {ROLES}")
        if (self.login is None) != (self.password_hash is None):
            raise ValueError("login и password_hash задаются только вместе")
        if self.role == NURSE and self.login is not None:
            raise ValueError("у медсестры не может быть логина")
        if self.buh_id is not None and not self.buh_id.strip():
            raise ValueError("buh_id не может быть пустой строкой")
