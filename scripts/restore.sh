#!/usr/bin/env bash
# ── docapp: восстановление БД из бэкапа на сервере ───────────────────────
# Останавливает юнит, кладёт выбранный бэкап на место (по имени файла
# определяет, какая это БД), запускает юнит и проверяет здоровье.
#
# Использование:  ./scripts/restore.sh <файл-бэкапа>
#   пример: ./scripts/restore.sh backups/docapp-20260815-225709.db
# ВАЖНО: перезаписывает текущие данные. Сначала снимите свежий бэкап.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 1 ]; then
  echo "Использование: $0 <файл-бэкапа (имя в backups/ на сервере)>" >&2
  exit 1
fi
BACKUP_NAME="$1"

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/lib-ssh.sh

# По имени бэкапа определяем, куда класть: docapp-*.db→data/docapp.db,
# consult-*.db→data/consult/consult.db, needs-*.db→data/needs/needs.db,
# catalog-*.yaml→data/needs/catalog.yaml
case "$BACKUP_NAME" in
  docapp-*.db)   DEST="data/docapp.db" ;;
  consult-*.db)  DEST="data/consult/consult.db" ;;
  needs-*.db)    DEST="data/needs/needs.db" ;;
  catalog-*.yaml) DEST="data/needs/catalog.yaml" ;;
  *)
    echo "Ошибка: не могу определить тип по имени «$BACKUP_NAME»" >&2
    echo "Ожидается: docapp-*.db | consult-*.db | needs-*.db | catalog-*.yaml" >&2
    exit 1
    ;;
esac

echo "==> Восстановление $BACKUP_NAME -> $DEST (на $SSH_TARGET)"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" bash -s "$BACKUP_NAME" "$DEST" <<'EOF'
set -euo pipefail
BACKUP_NAME="$1"; DEST="$2"
SRC="$DOCAPP_DEPLOY_DIR/backups/$BACKUP_NAME"
if [ ! -f "$SRC" ]; then
  echo "Ошибка: нет файла $SRC на сервере" >&2
  exit 1
fi
echo "-- стоп приложения --"
pkill -f '\\.venv/bin/python main\\.py' 2>/dev/null || true
sleep 1
echo "-- копия --"
cp "$SRC" "$DOCAPP_DEPLOY_DIR/$DEST"
echo "-- старт приложения --"
cd "$DOCAPP_DEPLOY_DIR" && ./scripts/start.sh
sleep 3
pgrep -f '\\.venv/bin/python main\\.py' >/dev/null && echo "процесс запущен" || echo "ПРОЦЕСС НЕ ЗАПУЩЕН"
curl -sf -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8000/ || echo 'НЕ ОТВЕЧАЕТ'
echo "Восстановление завершено."
EOF
