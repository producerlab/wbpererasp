# Используем официальный образ Playwright с Python
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Рабочая директория
WORKDIR /app

# Копируем requirements.txt
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код
COPY . .

# Создаем скрипт запуска для обоих сервисов
COPY <<EOF /app/start.sh
#!/bin/bash
set -e

echo "=== Starting services ==="
echo "PORT: \${PORT:-8000}"

# Запускаем FastAPI в фоне
echo "Starting FastAPI..."
uvicorn api.main:app --host 0.0.0.0 --port \${PORT:-8000} &
API_PID=\$!
echo "FastAPI started with PID \$API_PID"

# Ждем 5 секунд
sleep 5

# Запускаем бота
echo "Starting Telegram bot..."
python3 bot.py

# Если бот упал, убиваем API
kill \$API_PID 2>/dev/null || true
EOF

RUN chmod +x /app/start.sh

# Запускаем оба сервиса
CMD ["/app/start.sh"]
