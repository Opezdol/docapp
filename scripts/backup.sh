#!/usr/bin/env bash
# Бэкап данных docapp: единая БД + PDF-источники «Чата» + ключ сессий + каталог.
# Запуск: ./scripts/backup.sh
# (cron: 0 3 * * * cd /путь/к/docapp && ./scripts/backup.sh >> logs/backup.log 2>&1)
#
# После слияния баз (ADR-0016) данные лежат в одном файле, поэтому бэкап
# двухчастный (ADR-0020): снимок БД + архив папки PDF-источников, которые
# в базе не хранятся.
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

# 1. Единая БД (люди, записи, «Чат», «Потребности», «Дежурства»)
backup_db data/docapp.db "$BACKUP_DIR/docapp-$stamp.db"

# 2. PDF-источники «Чата» — файлы, в базе их нет
if [ -d data/wiki/sources ] && [ -n "$(ls -A data/wiki/sources 2>/dev/null)" ]; then
  tar czf "$BACKUP_DIR/wiki-sources-$stamp.tar.gz" -C data/wiki sources
  echo "OK: data/wiki/sources -> $BACKUP_DIR/wiki-sources-$stamp.tar.gz"
else
  echo "skip: data/wiki/sources (папки нет или пуста)"
fi

# 3. Ключ подписи сессий и каталог расходки (правятся файлом)
if [ -f data/secret.key ]; then
  cp -p data/secret.key "$BACKUP_DIR/secret-$stamp.key"
  echo "OK: data/secret.key -> $BACKUP_DIR/secret-$stamp.key"
fi
if [ -f data/needs/catalog.yaml ]; then
  cp -p data/needs/catalog.yaml "$BACKUP_DIR/catalog-$stamp.yaml"
  echo "OK: data/needs/catalog.yaml -> $BACKUP_DIR/catalog-$stamp.yaml"
fi

# 4. Удаляем старое (и снимки, и архивы, и файлы рядом)
find "$BACKUP_DIR" \
  \( -name '*.db' -o -name '*.yaml' -o -name '*.key' -o -name '*.tar.gz' \) \
  -mtime +$KEEP_DAYS -delete
echo "Готово. Старые бэкапы (>$KEEP_DAYS дней) удалены."
