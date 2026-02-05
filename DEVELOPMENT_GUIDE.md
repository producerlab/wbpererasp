# 🛠 Руководство по разработке

## Проблема: Конфликт между локальной разработкой и продакшеном

Когда бот запущен на Railway (продакшен) и вы хотите тестировать изменения локально, возникает **TelegramConflictError** - Telegram не позволяет двум экземплярам одного бота работать одновременно.

---

## ✅ Решение 1: Два отдельных бота (РЕКОМЕНДУЕТСЯ)

### Шаг 1: Создайте тестового бота

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot`
3. Назовите его, например: `WB Redistribution Bot DEV`
4. Username: `your_bot_dev_bot` (должен заканчиваться на `_bot`)
5. Скопируйте токен

### Шаг 2: Настройте локальное окружение

Создайте файл `.env.local`:

```bash
# Development environment (local testing)
BOT_TOKEN=1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw  # DEV bot token
ADMIN_IDS=8590836333
WEBAPP_URL=http://localhost:8080
DATABASE_PATH=bot_data_dev.db  # Separate database for dev
WB_ENCRYPTION_KEY=your_same_encryption_key  # Same key for consistency

# Database
DATABASE_URL=  # Empty = use SQLite locally

```

Оставьте `.env` для продакшена (Railway):

```bash
# Production environment (Railway)
BOT_TOKEN=8590836333:AAH...  # PROD bot token
WEBAPP_URL=https://your-railway-app.up.railway.app
DATABASE_URL=postgresql://...  # Railway PostgreSQL
# ... остальные настройки
```

### Шаг 3: Запуск локально

```bash
# Для разработки используйте:
python3 run_dev.py

# Он автоматически загрузит .env.local
```

### Шаг 4: Добавьте в .gitignore

```bash
echo ".env.local" >> .gitignore
echo "bot_data_dev.db" >> .gitignore
```

### ✅ Преимущества этого подхода:

- ✅ **Полная изоляция** - тестовый бот не влияет на продакшен
- ✅ **Разные базы данных** - можно экспериментировать с данными
- ✅ **Безопасность** - реальные пользователи не видят баги
- ✅ **Простота** - просто запускаете `run_dev.py`

---

## ✅ Решение 2: Webhook (Railway) + Polling (локально)

Если хотите использовать **один и тот же бот**, настройте Railway на webhook, а локально используйте polling с переключателем.

### Настройка Railway на webhook

1. В Railway добавьте переменную окружения:
   ```
   USE_WEBHOOK=true
   WEBHOOK_URL=https://your-app.up.railway.app/webhook
   ```

2. Измените `bot.py` для поддержки webhook:

```python
import os
from config import Config

async def main():
    use_webhook = os.getenv('USE_WEBHOOK', 'false').lower() == 'true'
    webhook_url = os.getenv('WEBHOOK_URL', '')

    if use_webhook and webhook_url:
        logger.info(f"Starting in WEBHOOK mode: {webhook_url}")
        # Удаляем старый webhook
        await bot.delete_webhook(drop_pending_updates=True)
        # Устанавливаем новый
        await bot.set_webhook(webhook_url)

        # Запускаем FastAPI (для обработки webhook)
        import uvicorn
        from api.main import app

        @app.post("/webhook")
        async def webhook_handler(update: dict):
            from aiogram.types import Update
            telegram_update = Update(**update)
            await dp.feed_update(bot, telegram_update)
            return {"ok": True}

        uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
    else:
        logger.info("Starting in POLLING mode (local development)")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
```

### Локально используйте polling

В `.env.local`:
```bash
USE_WEBHOOK=false
# Webhook не настроен, используется polling
```

### ⚠️ Недостаток:
- Нужно каждый раз ждать ~1-2 минуты при переключении между webhook и polling
- Сложнее в настройке

---

## 🔄 Workflow разработки

### Ежедневная работа:

1. **Утром** - запустите dev-бота локально:
   ```bash
   python3 run_dev.py
   ```

2. **Разрабатывайте и тестируйте** с dev-ботом:
   - Добавляйте функции
   - Тестируйте с реальными токенами WB
   - Проверяйте Mini App

3. **Перед коммитом** - протестируйте:
   ```bash
   # Запустите тесты (если есть)
   pytest tests/

   # Проверьте импорты
   python3 -c "import bot; print('OK')"
   ```

4. **Деплой на Railway**:
   ```bash
   git add .
   git commit -m "feat: add new feature"
   git push origin main
   # Railway автоматически задеплоит
   ```

5. **Проверьте продакшен**:
   - Откройте продакшн-бота в Telegram
   - Проверьте что всё работает
   - Мониторьте логи на Railway

---

## 📦 Структура файлов окружения

```
wbpereraspr/
├── .env                 # Production (Railway) - в git НЕ включается
├── .env.local           # Development (local) - в git НЕ включается
├── .env.example         # Шаблон для обоих - ВКЛЮЧАЕТСЯ в git
├── run.py               # Основной runner (использует .env)
├── run_dev.py           # Dev runner (использует .env.local)
└── bot_data.db          # Production DB (если SQLite)
└── bot_data_dev.db      # Development DB (в git НЕ включается)
```

---

## 🚨 Важно: Остановка Railway для локального тестирования

Если вы используете **один бот** и хотите протестировать локально:

1. **Зайдите на Railway Dashboard**:
   ```
   https://railway.app/project/YOUR_PROJECT_ID
   ```

2. **Остановите деплой**:
   - Settings → Pause deployment
   - Или временно удалите `BOT_TOKEN` из переменных окружения

3. **Запустите локально**:
   ```bash
   python3 run.py
   ```

4. **После тестирования** - возобновите Railway:
   - Settings → Resume deployment

⚠️ **Недостаток**: Пользователи не смогут пользоваться ботом во время вашего тестирования.

---

## 🎯 Рекомендация

Для профессиональной разработки используйте **Решение 1: Два отдельных бота**.

### Быстрый старт:

```bash
# 1. Создайте dev-бота через @BotFather
# 2. Скопируйте токен

# 3. Создайте .env.local
cat > .env.local << 'EOF'
BOT_TOKEN=YOUR_DEV_BOT_TOKEN
ADMIN_IDS=8590836333
WEBAPP_URL=http://localhost:8080
DATABASE_PATH=bot_data_dev.db
WB_ENCRYPTION_KEY=same_as_production
EOF

# 4. Добавьте в .gitignore
echo ".env.local" >> .gitignore
echo "bot_data_dev.db" >> .gitignore

# 5. Запустите dev-бота
python3 run_dev.py
```

Теперь у вас:
- ✅ Продакшн-бот работает на Railway 24/7
- ✅ Dev-бот запущен локально для тестирования
- ✅ Нет конфликтов
- ✅ Можно разрабатывать безопасно

---

## 🔍 Проверка текущего состояния

```bash
# Проверить какие боты запущены
ps aux | grep -E "bot.py|run.py" | grep -v grep

# Остановить все локальные экземпляры
pkill -f "python.*run"

# Проверить логи Railway
railway logs

# Запустить dev-бота
python3 run_dev.py
```

---

## 📚 Дополнительные ресурсы

- [Aiogram Webhook Setup](https://docs.aiogram.dev/en/latest/dispatcher/webhook.html)
- [Railway Environment Variables](https://docs.railway.app/develop/variables)
- [Telegram Bot API - Getting Updates](https://core.telegram.org/bots/api#getting-updates)

---

**Версия**: 1.0
**Дата**: 2026-01-27
**Автор**: Claude Code
