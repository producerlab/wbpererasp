#!/bin/bash
set -e

echo "=== Starting WB Redistribution Bot ==="
echo "PORT: ${PORT:-8000}"
echo "BOT_TOKEN set: $(if [ -n "$BOT_TOKEN" ]; then echo "YES"; else echo "NO"; fi)"
echo "ADMIN_IDS: ${ADMIN_IDS}"

# Запускаем FastAPI в фоне
echo "Starting FastAPI..."
uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} &
API_PID=$!
echo "FastAPI started with PID $API_PID"

# Ждем 5 секунд
sleep 5

# Запускаем бота
echo "Starting Telegram bot..."
python3 bot.py

# Если бот упал, убиваем API
kill $API_PID 2>/dev/null || true
