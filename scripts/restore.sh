#!/usr/bin/env bash
# ── docapp: восстановление данных из бэкапа ─────────────────────────────
# Кладёт выбранный бэкап на место (по имени файла определяет, что это),
# перезапускает Passenger и проверяет, что приложение отвечает.
#
# Использование:  ./scripts/restore.sh <имя-бэкапа>
#   пример: ./scripts/restore.sh docapp-20260927-030001.db
#          ./scripts/restore.sh wiki-sources-20260927-030001.tar.gz
#
# ВАЖНО: перезаписывает текущие данные. Перед восстановлением снимите свежий
# бэкап (./scripts/backup.sh) — пути назначения ниже.
#
# Перезапуск — через tmp/restart.txt в корне сайта (PassengerAppRoot): pkill на
# shared-хостинге убьёт процессы соседних сайтов (см. restart-passenger.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -ne 1 ]; then
  echo "Использование: $0 <имя-бэкапа (файл в backups/ на сервере)>" >&2
  exit 1
fi
BACKUP_NAME="$1"

# По имени бэкапа определяем, куда класть
case "$BACKUP_NAME" in
  docapp-*.db)           DEST="data/docapp.db" ;;
  secret-*.key)          DEST="data/secret.key" ;;
  catalog-*.yaml)        DEST="data/needs/catalog.yaml" ;;
  wiki-sources-*.tar.gz) DEST="data/wiki (архив распаковывается)" ;;
  *)
    echo "Ошибка: не могу определить тип по имени «$BACKUP_NAME»" >&2
    echo "Ожидается: docapp-*.db | secret-*.key | catalog-*.yaml | wiki-sources-*.tar.gz" >&2
    exit 1
    ;;
esac

# shellcheck disable=SC1091
. scripts/lib-ssh.sh

echo "==> Восстановление $BACKUP_NAME -> $DEST (на $SSH_TARGET)"
$SSH_BIN "${SSH_ARGS[@]}" "$SSH_TARGET" bash -s "$BACKUP_NAME" "$DEST" "$DOCAPP_SITE_ROOT" <<'EOF'
set -euo pipefail
BACKUP_NAME="$1"; DEST="$2"; SITE_ROOT="${3:-}"
DIR="$DOCAPP_DEPLOY_DIR"
SRC="$DIR/backups/$BACKUP_NAME"
if [ ! -f "$SRC" ]; then
  echo "Ошибка: нет файла $SRC на сервере" >&2
  exit 1
fi

echo "-- копия текущего состояния (на случай отката) --"
stamp=$(date +%Y%m%d-%H%M%S)
mkdir -p "$DIR/backups"
case "$BACKUP_NAME" in
  docapp-*.db)   [ -f "$DIR/data/docapp.db" ] && cp -p "$DIR/data/docapp.db" "$DIR/backups/docapp-before-restore-$stamp.db" ;;
  secret-*.key)  [ -f "$DIR/data/secret.key" ] && cp -p "$DIR/data/secret.key" "$DIR/backups/secret-before-restore-$stamp.key" ;;
esac

echo "-- восстановление --"
if [ "$BACKUP_NAME" != "${BACKUP_NAME%.tar.gz}" ]; then
  rm -rf "$DIR/data/wiki/sources"
  tar xzf "$SRC" -C "$DIR/data/wiki"
  echo "распаковано: $DIR/data/wiki/sources"
else
  mkdir -p "$(dirname "$DIR/$DEST")"
  cp -p "$SRC" "$DIR/$DEST"
  echo "положено: $DIR/$DEST"
fi

echo "-- перезапуск Passenger --"
if [ -n "$SITE_ROOT" ]; then
  mkdir -p "$SITE_ROOT/tmp"
  touch "$SITE_ROOT/tmp/restart.txt"
  echo "restart.txt обновлён в $SITE_ROOT/tmp"
else
  echo "ВНИМАНИЕ: корень сайта не задан (DOCAPP_SITE_ROOT) — перезапустите вручную" >&2
fi

echo "-- проверка --"
sleep 3
code=$(curl -s -o /dev/null -w '%{http_code}' -L "https://${DOCAPP_DOMAIN:-phhmn.ru}/" || echo 000)
echo "https://${DOCAPP_DOMAIN:-phhmn.ru}/ отвечает HTTP $code"
echo "Восстановление завершено. Проверьте данные на страницах приложения."
EOF
