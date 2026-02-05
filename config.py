"""
Конфигурация WB Redistribution Bot.

Все настройки читаются из переменных окружения.
Создайте файл .env на основе .env.example
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)


class Config:
    """Конфигурация бота"""

    # ========== ENVIRONMENT ==========
    # Определяем окружение: production (Railway) или development (локально/Railway)
    # ВАЖНО: Переменная ENVIRONMENT должна быть явно установлена в Railway environment variables
    ENVIRONMENT: str = os.getenv('ENVIRONMENT', 'development')
    IS_PRODUCTION: bool = ENVIRONMENT == 'production'

    # ========== TELEGRAM ==========
    # Два токена для разных окружений
    BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')  # Production token (Railway)
    BOT_TOKEN_DEV: str = os.getenv('BOT_TOKEN_DEV', '')  # Development token (локально)

    # Автоматический выбор токена в зависимости от окружения
    @classmethod
    def get_bot_token(cls) -> str:
        """Возвращает активный токен в зависимости от окружения"""
        if cls.IS_PRODUCTION:
            return cls.BOT_TOKEN
        else:
            # В development используем DEV токен, если он есть, иначе fallback на обычный
            return cls.BOT_TOKEN_DEV if cls.BOT_TOKEN_DEV else cls.BOT_TOKEN

    ADMIN_IDS: list = [
        int(x) for x in os.getenv('ADMIN_IDS', '').split(',')
        if x.strip().isdigit()
    ]

    # ========== WEBAPP ==========
    WEBAPP_URL: str = os.getenv('WEBAPP_URL', 'http://localhost:8080')

    # ========== DATABASE ==========
    DATABASE_URL: str = os.getenv('DATABASE_URL', '')  # PostgreSQL URL (Railway)
    DATABASE_PATH: str = os.getenv('DATABASE_PATH', 'bot_data.db')  # SQLite fallback

    # ========== WB API ==========
    WB_API_BASE_URL: str = os.getenv(
        'WB_API_BASE_URL', 'https://common-api.wildberries.ru'
    )
    WB_MARKETPLACE_URL: str = os.getenv(
        'WB_MARKETPLACE_URL', 'https://marketplace-api.wildberries.ru'
    )
    WB_SUPPLIES_URL: str = os.getenv(
        'WB_SUPPLIES_URL', 'https://supplies-api.wildberries.ru'
    )

    # ========== RATE LIMITING ==========
    WB_RATE_LIMIT_REQUESTS: int = int(os.getenv('WB_RATE_LIMIT_REQUESTS', '10'))
    WB_RATE_LIMIT_PERIOD: int = int(os.getenv('WB_RATE_LIMIT_PERIOD', '60'))

    # ========== ШИФРОВАНИЕ ==========
    WB_ENCRYPTION_KEY: str = os.getenv('WB_ENCRYPTION_KEY', '')

    # ========== REDIS (опционально) ==========
    REDIS_URL: str = os.getenv('REDIS_URL', '')

    @classmethod
    def validate(cls) -> None:
        """Проверяет обязательные параметры конфигурации"""
        errors = []
        warnings = []

        # Проверяем активный токен (зависит от окружения)
        active_token = cls.get_bot_token()
        if not active_token:
            if cls.IS_PRODUCTION:
                errors.append("BOT_TOKEN (production) не задан")
            else:
                errors.append("BOT_TOKEN_DEV (development) не задан. Создайте dev бота через @BotFather или используйте BOT_TOKEN")

        # Критическая проверка encryption key
        if not cls.WB_ENCRYPTION_KEY:
            errors.append(
                "⚠️ WB_ENCRYPTION_KEY не задан - токены НЕЛЬЗЯ сохранять безопасно!\n"
                "   Сгенерируйте ключ: python scripts/setup.py"
            )
        elif len(cls.WB_ENCRYPTION_KEY) < 32:
            errors.append(
                f"WB_ENCRYPTION_KEY слишком короткий ({len(cls.WB_ENCRYPTION_KEY)} символов, нужно минимум 32)\n"
                "   Сгенерируйте новый ключ: python scripts/setup.py"
            )

        # Проверка WEBAPP_URL - Telegram требует HTTPS для Mini App
        if cls.WEBAPP_URL:
            if not cls.WEBAPP_URL.startswith("https://"):
                warnings.append(
                    f"⚠️ WEBAPP_URL ({cls.WEBAPP_URL}) не использует HTTPS!\n"
                    "   Mini App кнопки НЕ будут работать в Telegram.\n"
                    "   Установите WEBAPP_URL с HTTPS (например: https://your-app.railway.app)"
                )
        else:
            warnings.append("WEBAPP_URL не задан - Mini App будет недоступен")

        if warnings:
            import logging
            logger = logging.getLogger(__name__)
            for warning in warnings:
                logger.warning(warning)

        if errors:
            raise ValueError(
                f"Критические ошибки конфигурации:\n" + "\n".join(f"- {e}" for e in errors)
            )

    @classmethod
    def get_summary(cls) -> str:
        """Возвращает сводку конфигурации"""
        bot_mode = "Production" if cls.IS_PRODUCTION else "Development"
        active_token = cls.get_bot_token()
        token_preview = active_token[:10] + "..." if active_token else "НЕ ЗАДАН"

        return f"""
=== Конфигурация WB Redistribution Bot ===
Окружение: {cls.ENVIRONMENT} ({bot_mode})
Токен бота: {token_preview}
Администраторов: {len(cls.ADMIN_IDS)}
База данных: {cls.DATABASE_PATH}

=== WB API ===
Rate limit: {cls.WB_RATE_LIMIT_REQUESTS} req/{cls.WB_RATE_LIMIT_PERIOD}s
Redis: {'Настроен' if cls.REDIS_URL else 'Не настроен'}
==========================================
"""
