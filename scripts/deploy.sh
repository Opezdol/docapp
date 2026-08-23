#!/usr/bin/env bash
# ── docapp: деплой обновлений на сервер ──────────────────────────────────
# Читает scripts/server.conf (через scripts/lib-ssh.sh), переносит код по
# rsync, обновляет зависимости, перезапускает приложение (nohup-процесс,
# systemd на виртуальном хостинге недоступен) и проверяет, что оно отвечает.
#
# Использование:  ./scripts/deploy.sh
# (запускать из корня docapp; перед запуском заполните scripts/server.conf)
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
. scripts/lib-ssh.sh

echo "==> 1/4: rsync кода на сервер ($SSH_TARGET:$DOCAPP_DEPLOY_DIR)"
rsync -avz --delete \
  --exclude '.venv' --exclude '.git' --exclude 'backups' \
  --exclude 'data' --exclude 'logs' --exclude '.env' \
  --exclude 'scripts/server.conf' --exclude '.DS_Store' \
  -e "$RSYNC_SSH_CMD" \
  ./ "$SSH_TARGET:$DOCAPP_DEPLOY_DIR/"

echo "==> 2/4: обновление зависимостей на сервере"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "cd $DOCAPP_DEPLOY_DIR && .venv/bin/pip install -e . --quiet 2>&1 | tail -5 || true"

echo "==> 3/4: перезапуск приложения"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "pkill -f '\\.venv/bin/python main\\.py' 2>/dev/null || true; sleep 1; cd $DOCAPP_DEPLOY_DIR && ./scripts/start.sh"

echo "==> 4/4: проверка"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "sleep 2; pgrep -f '\\.venv/bin/python main\\.py' >/dev/null && curl -sf -o /dev/null -w 'HTTP %{http_code}\\n' http://127.0.0.1:8000/ && echo OK || echo FAIL"

echo "Готово. Проверьте https://${DOCAPP_DOMAIN:-<домен>} в браузере."
