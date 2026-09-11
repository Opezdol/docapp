"""Проверки фронтенда: JS гоняется без браузера.

Серверную часть закрывает pytest, а JS оставался непроверенным — браузера на
рабочей машине нет (Chromium не установлен). `scripts/check-frontend.mjs`
проверяет общую библиотеку (`dc.esc`, неделя, шаг поля времени), обработчики
полей и баннер, загрузку сценариев страниц в подставном DOM и структуру: нет
копий общего в файлах модулей, шаблоны со сценарием наследуют `base.html`,
класс баннера один, service worker кэширует статику правилом.

Тест пропускается, если в системе нет `node`: серверная приёмка не должна
падать там, где нет JS-среды.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-frontend.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="в системе нет node")


def test_frontend_checks_pass() -> None:
    """Скрипт проверок фронтенда завершается без провалов."""
    result = subprocess.run(
        ["node", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, "проверки фронтенда провалены:\n" + result.stdout + result.stderr
    assert "ПЛОХО" not in result.stdout, result.stdout
