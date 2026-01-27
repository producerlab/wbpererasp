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
RUN echo '#!/bin/bash\n\
# Запускаем FastAPI в фоне\n\
uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} &\n\
# Ждем 5 секунд чтобы API запустился\n\
sleep 5\n\
# Запускаем бота\n\
python3 bot.py\n' > /app/start.sh && chmod +x /app/start.sh

# Запускаем оба сервиса
CMD ["/app/start.sh"]
