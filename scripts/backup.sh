#!/usr/bin/env bash
# Бэкап БД docapp: основная + «Компендиум» + потребности. Запуск: ./scripts/backup.sh
# (cron: 0 3 * * * cd /путь/к/docapp && ./scripts/backup.sh >> logs/backup.log 2>&1)
set -euo pipefail
cd "$(dirname "$0")/.."
BACKUP_DIR="backups"
KEEP_DAYS=14
mkdir -p "$BACKUP_DIR" logs
stamp=$(date +%Y%m%d-%H%M%S)
# Бэкап через sqlite3 .backup (безопасен при WAL: консистентная копия).
# Нужен Python >= 3.7 (в 3.6 у sqlite3.Connection нет метода .backup).
# На сервере AlmaLinux системный python3 — 3.6, поэтому предпочитаем python3.12.
if command -v python3.12 >/dev/null 2>&1; then
  PY_BIN=python3.12
elif command -v python3.11 >/dev/null 2>&1; then
  PY_BIN=python3.11
else
  PY_BIN=python3
fi
backup_db() {
  local db="$1" out="$2"
  if [ -f "$db" ]; then
    "$PY_BIN" - "$db" "$out" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(src)
out = sqlite3.connect(dst)
con.backup(out)
out.close(); con.close()
PY
    echo "OK: $db -> $out"
  else
    echo "skip: $db (нет файла)"
  fi
}
backup_db data/docapp.db "$BACKUP_DIR/docapp-$stamp.db"
backup_db data/wiki/wiki.db "$BACKUP_DIR/wiki-$stamp.db"
backup_db data/needs/needs.db "$BACKUP_DIR/needs-$stamp.db"
# Каталог «Потребностей» — YAML (правится файлом), копируем как есть
if [ -f data/needs/catalog.yaml ]; then
  cp data/needs/catalog.yaml "$BACKUP_DIR/catalog-$stamp.yaml"
  echo "OK: data/needs/catalog.yaml -> $BACKUP_DIR/catalog-$stamp.yaml"
else
  echo "skip: data/needs/catalog.yaml (нет файла)"
fi
# Удаляем старые бэкапы (и .db, и .yaml) старше KEEP_DAYS дней
find "$BACKUP_DIR" \( -name '*.db' -o -name '*.yaml' \) -mtime +$KEEP_DAYS -delete
echo "Готово. Старые бэкапы (>$KEEP_DAYS дней) удалены."
