#!/usr/bin/env bash
# Оживитель docapp: если процесс не запущен — стартует.
# Вызывается из cron каждую минуту.
set -euo pipefail
cd "$(dirname "$0")/.."
if pgrep -f "\.venv/bin/python main\.py" >/dev/null 2>&1; then
  exit 0
fi
mkdir -p logs
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
nohup .venv/bin/python main.py >> logs/app.log 2>&1 &
echo "$(date +%F\ %T): docapp перезапущен (PID $!)" >> logs/keepalive.log
