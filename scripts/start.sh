#!/usr/bin/env bash
# Автозапуск docapp (виртуальный хостинг, без systemd)
# OPENBLAS_NUM_THREADS=1 обязателен: на 40-ядерном хостинге OpenBLAS
# пытается выделить память под 40 потоков и падает с out of memory.
set -euo pipefail
cd "$(dirname "$0")/.."
if pgrep -f "\.venv/bin/python main\.py" >/dev/null 2>&1; then
  echo "docapp уже запущен"
  exit 0
fi
mkdir -p logs
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
nohup .venv/bin/python main.py >> logs/app.log 2>&1 &
echo "docapp запущен (PID $!)"
