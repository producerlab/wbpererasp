"""
Инициализация PostgreSQL базы данных для Railway.

Запускается автоматически при первом старте, если DATABASE_URL установлен.
"""

import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_postgres():
    """Инициализирует PostgreSQL схему из init_db.sql"""
    database_url = os.getenv('DATABASE_URL')

    if not database_url:
        logger.info("DATABASE_URL not set, skipping PostgreSQL initialization")
        return

    try:
        logger.info("Connecting to PostgreSQL...")

        # Подключаемся к PostgreSQL
        conn = psycopg2.connect(database_url)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()

        # Читаем SQL скрипт
        with open('init_db.sql', 'r', encoding='utf-8') as f:
            sql_script = f.read()

        logger.info("Executing schema initialization...")

        # Выполняем SQL скрипт
        cursor.execute(sql_script)

        logger.info("✅ PostgreSQL schema initialized successfully")

        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"❌ Failed to initialize PostgreSQL: {e}")
        raise


if __name__ == "__main__":
    init_postgres()
