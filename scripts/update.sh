#!/usr/bin/env bash
# ── docapp: обновление на сервере reg.ru (shared-хостинг, Passenger) ──────
# Полный цикл: бэкап БД → обновление кода (rsync) → зависимости →
# миграции схемы → перезапуск Passenger → проверка HTTPS.
#
# Использование:  ./scripts/update.sh
# (запускать из корня docapp; перед запуском заполните scripts/server.conf)
#
# В отличие от deploy.sh: делает бэкап ПЕРЕД обновлением и применяет
# миграции БД (docapp/consult/needs) до перезапуска приложения.
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
. scripts/lib-ssh.sh

echo "==> 0/6: бэкап БД на сервере (до обновления)"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "cd $DOCAPP_DEPLOY_DIR && ./scripts/backup.sh 2>&1 | tail -6"

echo "==> 1/6: rsync кода на сервер ($SSH_TARGET:$DOCAPP_DEPLOY_DIR)"
rsync -avz --delete \
  --exclude '.venv' --exclude '.git' --exclude 'backups' \
  --exclude 'data' --exclude 'logs' --exclude '.env' \
  --exclude 'scripts/server.conf' --exclude '.DS_Store' \
  -e "$RSYNC_SSH_CMD" \
  ./ "$SSH_TARGET:$DOCAPP_DEPLOY_DIR/"

echo "==> 1.5/6: запись хэша ревизии (REVISION) на сервере"
REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
printf '%s' "$REV" | $SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "cat > $DOCAPP_DEPLOY_DIR/REVISION && echo '  REVISION=$REV'"

echo "==> 2/6: обновление зависимостей (venv приложения)"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "cd $DOCAPP_DEPLOY_DIR && .venv/bin/pip install -e . --quiet 2>&1 | tail -3 || true"

echo "==> 3/6: обновление зависимостей (venv Passenger в корне сайта)"
if [ -n "${DOCAPP_SITE_ROOT:-}" ]; then
  $SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
    "cd $DOCAPP_DEPLOY_DIR && $DOCAPP_SITE_ROOT/venv/bin/pip install -e . --quiet 2>&1 | tail -3 || true"
else
  echo "  (DOCAPP_SITE_ROOT не задан — пропускаем)"
fi

echo "==> 4/6: миграции схемы БД (docapp/consult/needs)"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" bash -s "$DOCAPP_DEPLOY_DIR" <<'EOF'
set -euo pipefail
DEPLOY_DIR="$1"
cd "$DEPLOY_DIR"
export $(grep -v '^#' .env | xargs) 2>/dev/null || true
.venv/bin/python -m docapp.cli migrate
EOF

echo "==> 5/6: перезапуск Passenger (touch tmp/restart.txt)"
if [ -n "${DOCAPP_SITE_ROOT:-}" ]; then
  $SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
    "cd $DOCAPP_DEPLOY_DIR && ./scripts/restart-passenger.sh '$DOCAPP_SITE_ROOT'"
else
  echo "  (DOCAPP_SITE_ROOT не задан — пропускаем)"
fi

echo "==> 6/6: проверка"
sleep 3
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" \
  "curl -sf -o /dev/null -w 'HTTPS ${DOCAPP_DOMAIN:-<домен>}: HTTP %{http_code}\n' https://${DOCAPP_DOMAIN:-127.0.0.1}/ 2>&1 || echo 'FAIL: сайт не отвечает'"

echo "Готово. Откройте https://${DOCAPP_DOMAIN:-<домен>} в браузере."
