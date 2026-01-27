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

# Пока запускаем ТОЛЬКО бота (без FastAPI для отладки)
CMD ["python3", "-u", "bot.py"]
