"""Точка входа Phusion Passenger — финальная.

Ключевое: numpy/OpenBLAS при импорте создаёт пул потоков, что ЗАВИСАЕТ
под Passenger. Ограничиваем потоки ДО импорта numpy (в самом начале файла).
Также используем a2wsgi.ASGIMiddleware (ASGI->WSGI) — правильный класс.
"""

import os
import sys
import traceback
from pathlib import Path

# КРИТИЧНО: до любых импортов numpy/тяжёлых библиотек ограничиваем потоки
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

LOG = Path(__file__).resolve().parent / "passenger.log"

try:
    SITE_ROOT = Path(__file__).resolve().parent
    VENV_DIR = SITE_ROOT / "venv"
    SITE_PACKAGES = VENV_DIR / "lib" / "python3.12" / "site-packages"
    sys.path.insert(0, str(SITE_ROOT))
    if SITE_PACKAGES.exists():
        sys.path.insert(0, str(SITE_PACKAGES))

    APP_DIR = SITE_ROOT.parent.parent / "docapp"
    sys.path.insert(0, str(APP_DIR))
    sys.path.insert(0, str(APP_DIR / "src"))

    # Загрузить .env
    env_file = APP_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

    # Относительные пути из .env резолвим от каталога приложения
    for _var in ("DOCAPP_DB", "NEEDS_DB", "NEEDS_CATALOG", "CONSULT_INDEX_DIR", "CONSULT_DOCS_DIR"):
        _val = os.environ.get(_var)
        if _val and not Path(_val).is_absolute():
            os.environ[_var] = str(APP_DIR / _val)

    from a2wsgi import ASGIMiddleware
    from docapp.config import db_path, session_secret
    from docapp.web.app import create_app

    _fastapi_app = create_app(db_path=db_path(), secret=session_secret())
    application = ASGIMiddleware(_fastapi_app)
    LOG.write_text("OK: application создан\n", encoding="utf-8")
except Exception:
    LOG.write_text("ОШИБКА:\n" + traceback.format_exc() + "\n", encoding="utf-8")
    raise
