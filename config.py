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

    # ========== TELEGRAM ==========
    BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')
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

        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN не задан")

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
        return f"""
=== Конфигурация WB Redistribution Bot ===
Администраторов: {len(cls.ADMIN_IDS)}
База данных: {cls.DATABASE_PATH}

=== WB API ===
Rate limit: {cls.WB_RATE_LIMIT_REQUESTS} req/{cls.WB_RATE_LIMIT_PERIOD}s
Redis: {'Настроен' if cls.REDIS_URL else 'Не настроен'}
==========================================
"""
