#!/usr/bin/env bash
# ── docapp: общий код SSH для скриптов (deploy/status/install-cron/restore) ──
# Подключается так:  . scripts/lib-ssh.sh   (после cd в корень docapp)
#
# Читает scripts/server.conf и задаёт:
#   SSH_ARGS     — массив аргументов для ssh (порт, ключ)
#   SCP_ARGS     — массив аргументов для scp (порт -P, ключ)
#   SSH_TARGET   — user@host
#   SSH_BIN      — команда для запуска ssh: "ssh" или "sshpass -e ssh"
#   SCP_BIN      — команда для запуска scp: "scp" или "sshpass -e scp"
#   RSYNC_SSH    — строка для rsync -e (учитывает sshpass)
#
# Вход по паролю: если DOCAPP_SSH_KEY пуст и задан DOCAPP_SSH_PASSWORD,
# используется sshpass (нужен установленный sshpass; пароль передаётся
# через переменную окружения SSHPASS, чтобы не светить его в ps).

set -euo pipefail

if [ ! -f scripts/server.conf ]; then
  echo "Ошибка: нет scripts/server.conf (скопируйте server.conf.example и заполните)" >&2
  exit 1
fi
# shellcheck disable=SC1091
. scripts/server.conf

: "${DOCAPP_HOST:?задайте DOCAPP_HOST в scripts/server.conf}"
: "${DOCAPP_SSH_USER:?задайте DOCAPP_SSH_USER}"
: "${DOCAPP_DEPLOY_DIR:?задайте DOCAPP_DEPLOY_DIR}"
DOCAPP_SSH_PORT="${DOCAPP_SSH_PORT:-22}"
DOCAPP_SERVICE="${DOCAPP_SERVICE:-docapp}"

SSH_TARGET="$DOCAPP_SSH_USER@$DOCAPP_HOST"

# Базовые аргументы: порт (для ssh — -p, для scp — -P)
SSH_ARGS=(-p "$DOCAPP_SSH_PORT")
SCP_ARGS=(-P "$DOCAPP_SSH_PORT")

# Если задан ключ и он существует — вход по ключу
USE_KEY=""
if [ -n "${DOCAPP_SSH_KEY:-}" ] && [ -f "${DOCAPP_SSH_KEY/#\~/$HOME}" ]; then
  SSH_ARGS+=(-i "${DOCAPP_SSH_KEY/#\~/$HOME}")
  SCP_ARGS+=(-i "${DOCAPP_SSH_KEY/#\~/$HOME}")
  USE_KEY=1
fi

# Команда запуска ssh
if [ -n "$USE_KEY" ]; then
  SSH_BIN="ssh"
  SCP_BIN="scp"
  RSYNC_SSH="ssh"
else
  # Вход по паролю — нужен sshpass
  if [ -z "${DOCAPP_SSH_PASSWORD:-}" ]; then
    echo "Ошибка: не задан ни DOCAPP_SSH_KEY, ни DOCAPP_SSH_PASSWORD в scripts/server.conf" >&2
    exit 1
  fi
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "Ошибка: вход по паролю, но sshpass не установлен." >&2
    echo "  macOS:  brew install hudochenkov/sshpass/sshpass" >&2
    echo "  Ubuntu: sudo apt install sshpass" >&2
    exit 1
  fi
  SSH_BIN="sshpass -e ssh"
  SCP_BIN="sshpass -e scp"
  RSYNC_SSH="sshpass -e ssh"
fi
export SSHPASS="${DOCAPP_SSH_PASSWORD:-}"

# Собрать строку для rsync -e (ssh или sshpass -e ssh + аргументы)
RSYNC_SSH_CMD="$RSYNC_SSH ${SSH_ARGS[*]}"
