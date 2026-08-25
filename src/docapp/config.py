"""Общие настройки приложения: путь к БД и секрет сессий."""

import os
import secrets
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def db_path() -> Path:
    """Путь к файлу БД: из окружения DOCAPP_DB или data/docapp.db."""
    override = os.environ.get("DOCAPP_DB")
    return Path(override) if override else DATA_DIR / "docapp.db"


def https_only() -> bool:
    """Требовать HTTPS-куки сессий: env DOCAPP_HTTPS_ONLY=1 (продакшн за TLS)."""
    return os.environ.get("DOCAPP_HTTPS_ONLY", "") == "1"


def git_revision() -> str:
    """Короткий хэш текущего коммита git (для бейджа версии на страницах).

    Источники по приоритету:
    1. env DOCAPP_GIT_REVISION — на сервере, где нет git/`.git` (rsync-деплой),
       задаётся скриптом обновления (scripts/update.sh) при выкладке;
    2. файл REVISION в корне проекта — тот же механизм, но файлом;
    3. `git rev-parse --short HEAD` — в разработке/на сервере с `.git`;
    4. пустая строка, если ничего не доступно (бейдж не отображается).

    Функция никогда не бросает исключений: при любой ошибке возвращает ''.
    """
    env_rev = os.environ.get("DOCAPP_GIT_REVISION", "").strip()
    if env_rev:
        return env_rev
    rev_file = BASE_DIR / "REVISION"
    if rev_file.exists():
        value = rev_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — git может отсутствовать на хостинге
        return ""


def session_secret() -> str:
    """Секрет подписи сессий. Хранится в data/secret.key, чтобы
    перезапуски сервера не убивали сессии врачей."""
    key_file = DATA_DIR / "secret.key"
    if key_file.exists():
        return key_file.read_text().strip()
    secret = secrets.token_hex(32)
    key_file.write_text(secret)
    return secret
