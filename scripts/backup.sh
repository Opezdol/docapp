#!/usr/bin/env bash
# Бэкап БД docapp: основная + консультант. Запуск: ./scripts/backup.sh
# (cron: 0 3 * * * cd /путь/к/docapp && ./scripts/backup.sh >> logs/backup.log 2>&1)
set -euo pipefail
cd "$(dirname "$0")/.."
BACKUP_DIR="backups"
KEEP_DAYS=14
mkdir -p "$BACKUP_DIR" logs
stamp=$(date +%Y%m%d-%H%M%S)
# Бэкап через sqlite3 .backup (безопасен при WAL: консистентная копия)
backup_db() {
  local db="$1" out="$2"
  if [ -f "$db" ]; then
    python3 - "$db" "$out" <<'PY'
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
backup_db data/consult/consult.db "$BACKUP_DIR/consult-$stamp.db"
find "$BACKUP_DIR" -name '*.db' -mtime +$KEEP_DAYS -delete
echo "Готово. Старые бэкапы (>$KEEP_DAYS дней) удалены."
