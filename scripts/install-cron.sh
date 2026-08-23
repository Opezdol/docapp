#!/usr/bin/env bash
# ── docapp: установка cron-задач на сервере ──────────────────────────────
# Копирует scripts/backup.sh и scripts/keepalive.sh на сервер, делает их
# исполняемыми и добавляет в crontab пользователя:
#   - @reboot        → автозапуск приложения (start.sh)
#   - * * * * *      → keepalive-оживитель (виртуальный хостинг убивает процессы)
#   - 0 3 * * *      → ежедневный бэкап БД (backup.sh)
#
# Использование:  ./scripts/install-cron.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
. scripts/lib-ssh.sh

echo "==> Копирую скрипты на сервер"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "mkdir -p $DOCAPP_DEPLOY_DIR/scripts $DOCAPP_DEPLOY_DIR/logs"
$SCP_BIN "${SCP_ARGS[@]}" scripts/backup.sh scripts/start.sh scripts/keepalive.sh \
  "$SSH_TARGET:$DOCAPP_DEPLOY_DIR/scripts/"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "chmod +x $DOCAPP_DEPLOY_DIR/scripts/backup.sh $DOCAPP_DEPLOY_DIR/scripts/start.sh $DOCAPP_DEPLOY_DIR/scripts/keepalive.sh"

echo "==> Добавляю cron-задачи"
REBOOT_LINE="@reboot $DOCAPP_DEPLOY_DIR/scripts/start.sh >> $DOCAPP_DEPLOY_DIR/logs/app.log 2>&1"
KEEP_LINE="* * * * * $DOCAPP_DEPLOY_DIR/scripts/keepalive.sh"
BACKUP_LINE="0 3 * * * cd $DOCAPP_DEPLOY_DIR && ./scripts/backup.sh >> logs/backup.log 2>&1"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" bash -s "$REBOOT_LINE" "$KEEP_LINE" "$BACKUP_LINE" <<'EOF'
set -euo pipefail
REBOOT_LINE="$1"; KEEP_LINE="$2"; BACKUP_LINE="$3"
( crontab -l 2>/dev/null \
    | grep -vF "$REBOOT_LINE" | grep -vF "$KEEP_LINE" | grep -vF "$BACKUP_LINE"; \
  echo "$REBOOT_LINE"; echo "$KEEP_LINE"; echo "$BACKUP_LINE" ) | crontab -
EOF

echo "==> Текущий crontab:"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" "crontab -l"

echo "==> Пробный запуск бэкапа (проверка):"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "cd $DOCAPP_DEPLOY_DIR && ./scripts/backup.sh 2>&1 | tail -8"

echo "Готово: автозапуск, keepalive и ежедневный бэкап установлены."
