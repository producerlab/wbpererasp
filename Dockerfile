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

# Делаем скрипт исполняемым
RUN chmod +x /app/start.sh

# Запускаем оба сервиса
CMD ["/app/start.sh"]
