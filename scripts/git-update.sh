#!/usr/bin/env bash
# ── docapp: обновление с GitHub на сервере (git pull + миграции + Passenger) ──
# Запускать НА СЕРВЕРЕ (reg.ru shared-хостинг) из каталога приложения:
#   cd ~/data/docapp && ./scripts/git-update.sh
#
# Делает:
#   0. бэкап данных (единая БД + PDF-источники + ключ сессий)
#   1. git pull (обновление кода с GitHub)
#   2. обновление зависимостей (venv приложения + venv Passenger)
#   3. миграции схемы БД (единая база, все модули реестра)
#   4. перезапуск Passenger (touch tmp/restart.txt в корне сайта)
#   5. проверка HTTPS
#
# Требует: git, SSH-ключ сервера в GitHub (deploy key), настроенный remote.
# Это единственный путь выкладки: rsync-скрипты убраны — они уносили с сервера
# файлы, которых нет в репозитории, и допускали два расходящихся состояния кода.
set -euo pipefail
cd "$(dirname "$0")/.."

# Пути (при необходимости переопределите переменными окружения)
DOCAPP_DIR="$(pwd)"
SITE_ROOT="${DOCAPP_SITE_ROOT:-/var/www/u3617050/data/www/phhmn.ru}"
DOMAIN="${DOCAPP_DOMAIN:-phhmn.ru}"

echo "==> 0/6: бэкап данных до обновления"
./scripts/backup.sh

echo "==> 1/6: git pull ($(git remote get-url origin 2>/dev/null || echo 'remote не настроен'))"
git pull --ff-only origin main
# Значок версии в шапке: файл REVISION важнее git в config.git_revision().
git rev-parse --short HEAD > REVISION

echo "==> 2/6: обновление зависимостей (venv приложения)"
.venv/bin/pip install -e . --quiet 2>&1 | tail -3 || true

echo "==> 3/6: обновление зависимостей (venv Passenger в корне сайта)"
if [ -d "$SITE_ROOT/venv" ]; then
  "$SITE_ROOT/venv/bin/pip" install -e . --quiet 2>&1 | tail -3 || true
else
  echo "  (нет $SITE_ROOT/venv — пропускаем)"
fi

echo "==> 4/6: миграции схемы БД"
.venv/bin/python -m docapp.cli migrate

echo "==> 5/6: перезапуск Passenger (touch tmp/restart.txt)"
./scripts/restart-passenger.sh "$SITE_ROOT"
echo "  Passenger перезагрузит приложение при следующем запросе"

echo "==> 6/6: проверка HTTPS"
sleep 3
code=$(curl -s -o /dev/null -w "%{http_code}" -L "https://$DOMAIN/" || echo 000)
if [ "$code" = "200" ] || [ "$code" = "303" ]; then
  echo "OK: https://$DOMAIN отвечает HTTP $code"
else
  echo "FAIL: https://$DOMAIN отвечает HTTP $code"
  exit 1
fi

echo "Готово."
