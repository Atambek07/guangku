#!/usr/bin/env bash
# Установка зависимостей проекта графа-плоттера (Linux / Ubuntu)
set -e

echo "==> Обновляем pip"
python3 -m pip install --upgrade pip

echo "==> Ставим зависимости Python"
python3 -m pip install -r requirements.txt

echo "==> Готово. Запуск:"
echo "    cd /home/atambek/projects/guangku"
echo "    uvicorn backend.main:app --host 0.0.0.0 --port 8000"
echo "    Откройте http://<ip-компьютера>:8000 со смартфона в локальной сети."
