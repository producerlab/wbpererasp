"""
Database Factory - выбирает правильную БД в зависимости от окружения.

- Если DATABASE_URL установлен → PostgreSQL (Railway)
- Иначе → SQLite (локально)
"""

import os
import logging

logger = logging.getLogger(__name__)


def get_database():
    """
    Возвращает экземпляр БД в зависимости от окружения.

    Returns:
        Database: SQLite или PostgreSQL адаптер
    """
    database_url = os.getenv('DATABASE_URL')

    if database_url:
        # Railway / Production - используем PostgreSQL
        logger.info("Using PostgreSQL database")
        from database_pg import DatabasePostgres
        return DatabasePostgres(database_url)
    else:
        # Локально - используем SQLite
        logger.info("Using SQLite database")
        from database import Database
        from config import Config
        return Database(Config.DATABASE_PATH)
