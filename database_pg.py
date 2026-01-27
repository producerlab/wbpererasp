"""
PostgreSQL Database для Railway.

Использует тот же интерфейс что и database.py (SQLite),
но работает с PostgreSQL.
"""

import os
import psycopg2
import psycopg2.extras
from typing import List, Dict, Optional
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)


class DatabasePostgres:
    """PostgreSQL адаптер с тем же интерфейсом что у Database (SQLite)"""

    def __init__(self, database_url: str = None):
        """
        Args:
            database_url: PostgreSQL connection string
        """
        self.database_url = database_url or os.getenv('DATABASE_URL')
        if not self.database_url:
            raise ValueError("DATABASE_URL not provided")

        # Инициализируем схему если нужно
        self._ensure_schema()

    @contextmanager
    def _get_connection(self):
        """Context manager для подключения к PostgreSQL"""
        conn = psycopg2.connect(
            self.database_url,
            cursor_factory=psycopg2.extras.RealDictCursor
        )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self):
        """Убеждаемся что схема инициализирована"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Проверяем есть ли таблица users
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'users'
                    )
                """)
                exists = cursor.fetchone()['exists']

                if not exists:
                    logger.info("Initializing PostgreSQL schema...")
                    # Читаем и выполняем init_db.sql (используем абсолютный путь)
                    sql_file = os.path.join(os.path.dirname(__file__), 'init_db.sql')
                    with open(sql_file, 'r', encoding='utf-8') as f:
                        sql_script = f.read()
                    cursor.execute(sql_script)
                    logger.info("Schema initialized")
        except Exception as e:
            logger.error(f"Failed to ensure schema: {e}")
            raise

    # ==================== USERS ====================

    def add_user(self, telegram_id: int, username: str = None, first_name: str = None) -> bool:
        """Добавляет или обновляет пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (telegram_id, username, first_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_active = CURRENT_TIMESTAMP
            ''', (telegram_id, username, first_name))
            return True

    def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Получает пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE telegram_id = %s', (telegram_id,))
            return dict(cursor.fetchone()) if cursor.rowcount > 0 else None

    # ==================== WB TOKENS ====================

    def add_wb_token(self, user_id: int, encrypted_token: str, name: str = "Основной") -> int:
        """Добавляет WB API токен"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO wb_api_tokens (user_id, name, encrypted_token)
                VALUES (%s, %s, %s)
                RETURNING id
            ''', (user_id, name, encrypted_token))
            return cursor.fetchone()['id']

    def get_wb_tokens(self, user_id: int) -> List[Dict]:
        """Получает все токены пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, is_active, created_at, last_used
                FROM wb_api_tokens
                WHERE user_id = %s AND is_active = TRUE
            ''', (user_id,))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d['last_used_at'] = d.get('last_used')
                result.append(d)
            return result

    def get_user_wb_tokens(self, user_id: int) -> List[Dict]:
        """Алиас для get_wb_tokens"""
        return self.get_wb_tokens(user_id)

    def get_wb_token(self, user_id: int, token_id: int = None) -> Optional[Dict]:
        """Получает токен"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if token_id is not None:
                cursor.execute('''
                    SELECT * FROM wb_api_tokens
                    WHERE id = %s AND user_id = %s
                ''', (token_id, user_id))
            else:
                cursor.execute('''
                    SELECT * FROM wb_api_tokens
                    WHERE user_id = %s AND is_active = TRUE
                    ORDER BY created_at DESC
                    LIMIT 1
                ''', (user_id,))
            return dict(cursor.fetchone()) if cursor.rowcount > 0 else None

    def delete_wb_token(self, user_id: int, token_id: int) -> bool:
        """Удаляет токен (soft delete)"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE wb_api_tokens
                SET is_active = FALSE
                WHERE id = %s AND user_id = %s
            ''', (token_id, user_id))
            return cursor.rowcount > 0

    # ==================== SUPPLIERS ====================

    def add_supplier(self, user_id: int, name: str, token_id: int, is_default: bool = False) -> int:
        """Добавляет поставщика"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO suppliers (user_id, name, token_id, is_default)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            ''', (user_id, name, token_id, is_default))
            return cursor.fetchone()['id']

    def get_suppliers(self, user_id: int) -> List[Dict]:
        """Получает список поставщиков"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.*, t.name as token_name
                FROM suppliers s
                JOIN wb_api_tokens t ON s.token_id = t.id
                WHERE s.user_id = %s
                ORDER BY s.is_default DESC, s.created_at DESC
            ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_supplier(self, supplier_id: int) -> Optional[Dict]:
        """Получает поставщика по ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM suppliers WHERE id = %s', (supplier_id,))
            return dict(cursor.fetchone()) if cursor.rowcount > 0 else None

    def delete_supplier(self, supplier_id: int) -> bool:
        """Удаляет поставщика"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM suppliers WHERE id = %s', (supplier_id,))
            return cursor.rowcount > 0

    # ==================== REDISTRIBUTION REQUESTS ====================

    def add_redistribution_request(self, **kwargs) -> int:
        """Создаёт заявку на перераспределение"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO redistribution_requests (
                    user_id, supplier_id, nm_id, product_name,
                    source_warehouse_id, source_warehouse_name,
                    target_warehouse_id, target_warehouse_name,
                    quantity, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (
                kwargs['user_id'], kwargs['supplier_id'], kwargs['nm_id'],
                kwargs.get('product_name'), kwargs['source_warehouse_id'],
                kwargs.get('source_warehouse_name'), kwargs['target_warehouse_id'],
                kwargs.get('target_warehouse_name'), kwargs['quantity'],
                kwargs.get('status', 'pending')
            ))
            return cursor.fetchone()['id']

    def get_redistribution_requests(self, user_id: int, status: str = None) -> List[Dict]:
        """Получает заявки пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('''
                    SELECT * FROM redistribution_requests
                    WHERE user_id = %s AND status = %s
                    ORDER BY created_at DESC
                ''', (user_id, status))
            else:
                cursor.execute('''
                    SELECT * FROM redistribution_requests
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def update_redistribution_request(self, request_id: int, **kwargs) -> bool:
        """Обновляет заявку"""
        fields = []
        values = []
        for key, value in kwargs.items():
            fields.append(f"{key} = %s")
            values.append(value)

        if not fields:
            return False

        values.append(request_id)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE redistribution_requests
                SET {", ".join(fields)}
                WHERE id = %s
            ''', values)
            return cursor.rowcount > 0

    def delete_redistribution_request(self, request_id: int, user_id: int) -> bool:
        """Удаляет заявку"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM redistribution_requests
                WHERE id = %s AND user_id = %s
            ''', (request_id, user_id))
            return cursor.rowcount > 0

    # ==================== STATS ====================

    def get_total_stats(self) -> Dict:
        """Получает общую статистику"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM users')
            users_count = cursor.fetchone()['count']
            cursor.execute('SELECT COUNT(*) as count FROM redistribution_requests')
            requests_count = cursor.fetchone()['count']
            return {
                'total_users': users_count,
                'total_requests': requests_count
            }
