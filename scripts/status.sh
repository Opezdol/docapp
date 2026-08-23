#!/usr/bin/env bash
# ── docapp: статус сервера и здоровье ────────────────────────────────────
# Читает scripts/server.conf (через scripts/lib-ssh.sh) и по SSH показывает
# сводку: юнит, HTTP/HTTPS, диск, последние бэкапы, лог бэкапа.
#
# Использование:  ./scripts/status.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
. scripts/lib-ssh.sh

echo "=== docapp: статус сервера ($SSH_TARGET) ==="
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" bash -s <<EOF
set -euo pipefail
echo "-- процесс приложения --"
pgrep -af '\\.venv/bin/python main\\.py' | head -3 || echo 'НЕ ЗАПУЩЕН'
echo "-- HTTP 127.0.0.1:8000 --"
curl -sf -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8000/ || echo 'НЕ ОТВЕЧАЕТ'
echo "-- HTTPS снаружи --"
curl -sf -o /dev/null -w 'HTTP %{http_code}\n' "https://${DOCAPP_DOMAIN:-$DOCAPP_HOST}/" || echo 'НЕ ОТВЕЧАЕТ'
echo "-- диск --"
df -h / | tail -1
echo "-- последние бэкапы ($DOCAPP_DEPLOY_DIR/backups) --"
ls -t "$DOCAPP_DEPLOY_DIR/backups" 2>/dev/null | head -5 || echo 'бэкапов нет'
echo "-- хвост лога бэкапа --"
tail -5 "$DOCAPP_DEPLOY_DIR/logs/backup.log" 2>/dev/null || echo 'лога нет'
echo "-- хвост лога приложения --"
tail -5 "$DOCAPP_DEPLOY_DIR/logs/app.log" 2>/dev/null || echo 'лога нет'
echo "-- cron --"
crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$' | head -5 || echo 'cron пуст'
EOF
