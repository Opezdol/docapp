#!/usr/bin/env bash
# ── docapp: перезапуск приложения Passenger ────────────────────────────────
# Создаёт (обновляет mtime) tmp/restart.txt в корне сайта (PassengerAppRoot) —
# штатный сигнал Passenger перезагрузить приложение при следующем запросе.
#
# Почему не pkill: на shared-хостинге cmdline wsgi-loader не содержит имени
# пользователя (/opt/python/.../python .../wsgi-loader.py), поэтому маска
# «uXXXXXX.*wsgi-loader» не срабатывает, а глобальный pkill убьёт чужие
# процессы соседних сайтов. restart.txt — безопасный и точный механизм.
#
# Использование (на сервере):
#   ./scripts/restart-passenger.sh [ПУТЬ_К_КОРНЮ_САЙТА]
# Корень сайта берётся из $1 или DOCAPP_SITE_ROOT (иначе — ошибка).

set -euo pipefail

SITE_ROOT="${1:-${DOCAPP_SITE_ROOT:-}}"
if [ -z "$SITE_ROOT" ]; then
  echo "Ошибка: укажите корень сайта (PassengerAppRoot) аргументом или через DOCAPP_SITE_ROOT" >&2
  exit 1
fi

mkdir -p "$SITE_ROOT/tmp"
touch "$SITE_ROOT/tmp/restart.txt"
echo "Passenger: restart.txt обновлён в $SITE_ROOT/tmp — приложение перезагрузится при следующем запросе."
