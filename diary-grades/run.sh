#!/usr/bin/env bash
# Один клик на Fedora: из меню приложений (diary.desktop) или ./run.sh.
# Первый запуск готовит .venv, последующие — просто открывают окно.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [[ ! -f .venv/.diary-ready ]]; then
  echo "Первый запуск: готовим окружение (один раз, пара минут)..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip --quiet
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m playwright install chromium
  touch .venv/.diary-ready
  echo "Окружение готово."
fi

# GUI без терминала: .desktop ставит Terminal=false, отсюда exec без обёрток.
exec .venv/bin/python app.py "$@"
