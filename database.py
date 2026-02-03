"""
База данных WB Redistribution Bot.

Таблицы:
- users: пользователи бота
- wb_api_tokens: зашифрованные WB API токены
- suppliers: поставщики (мультиаккаунт)
- wb_warehouses: кэш складов WB
- redistribution_requests: заявки на перемещение
"""

import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)


class Database:
    """Менеджер базы данных SQLite"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DATABASE_PATH
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Контекстный менеджер для соединения с БД"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Инициализация таблиц БД"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Пользователи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active INTEGER DEFAULT 1
                )
            ''')

            # WB API токены (зашифрованные)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS wb_api_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT DEFAULT 'Основной',
                    encrypted_token TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id)
                )
            ''')

            # Кэш складов WB
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS wb_warehouses (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    address TEXT,
                    work_time TEXT,
                    accept_types TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Поставщики (мультиаккаунт)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS suppliers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    token_id INTEGER NOT NULL,
                    is_default INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (token_id) REFERENCES wb_api_tokens(id)
                )
            ''')

            # Заявки на перемещение
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS redistribution_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    supplier_id INTEGER NOT NULL,
                    nm_id INTEGER NOT NULL,
                    product_name TEXT,
                    source_warehouse_id INTEGER NOT NULL,
                    source_warehouse_name TEXT,
                    target_warehouse_id INTEGER NOT NULL,
                    target_warehouse_name TEXT,
                    quantity INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    supply_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
                )
            ''')

            # Browser sessions (для авторизации через SMS)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    cookies_encrypted TEXT,
                    supplier_name TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id)
                )
            ''')

            # Индексы для быстрого поиска
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_tokens_user
                ON wb_api_tokens(user_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_suppliers_user
                ON suppliers(user_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_redistribution_user
                ON redistribution_requests(user_id, status)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_browser_sessions_user
                ON browser_sessions(user_id, status)
            ''')

            logger.info("Database initialized successfully")

    # ==================== USERS ====================

    def add_user(
        self,
        telegram_id: int,
        username: str = None,
        first_name: str = None
    ) -> bool:
        """Добавляет или обновляет пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (telegram_id, username, first_name)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_active = CURRENT_TIMESTAMP
            ''', (telegram_id, username, first_name))
            return True

    def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Получает пользователя по telegram_id"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM users WHERE telegram_id = ?',
                (telegram_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_user_activity(self, telegram_id: int):
        """Обновляет время последней активности"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET last_active = CURRENT_TIMESTAMP
                WHERE telegram_id = ?
            ''', (telegram_id,))

    # ==================== WB TOKENS ====================

    def add_wb_token(
        self,
        user_id: int,
        encrypted_token: str,
        name: str = "Основной"
    ) -> int:
        """Добавляет зашифрованный WB API токен"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO wb_api_tokens (user_id, name, encrypted_token)
                VALUES (?, ?, ?)
            ''', (user_id, name, encrypted_token))
            return cursor.lastrowid

    def get_wb_tokens(self, user_id: int) -> List[Dict]:
        """Получает все токены пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, is_active, created_at, last_used
                FROM wb_api_tokens
                WHERE user_id = ? AND is_active = 1
            ''', (user_id,))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                # Алиас для совместимости
                d['last_used_at'] = d.get('last_used')
                result.append(d)
            return result

    # Алиас для совместимости с handlers
    def get_user_wb_tokens(self, user_id: int) -> List[Dict]:
        """Алиас для get_wb_tokens"""
        return self.get_wb_tokens(user_id)

    def get_wb_token(self, user_id: int, token_id: int = None) -> Optional[Dict]:
        """
        Получает токен.

        Если token_id указан - возвращает конкретный токен.
        Если token_id не указан - возвращает активный токен пользователя.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if token_id is not None:
                cursor.execute('''
                    SELECT * FROM wb_api_tokens
                    WHERE id = ? AND user_id = ?
                ''', (token_id, user_id))
            else:
                cursor.execute('''
                    SELECT * FROM wb_api_tokens
                    WHERE user_id = ? AND is_active = 1
                    ORDER BY created_at DESC
                    LIMIT 1
                ''', (user_id,))

            row = cursor.fetchone()
            if row:
                d = dict(row)
                # Расшифровываем токен
                from utils.encryption import decrypt_token
                encrypted = d.get('encrypted_token', '')
                d['token'] = decrypt_token(encrypted) if encrypted else ''
                d['last_used_at'] = d.get('last_used')
                return d
            return None

    def get_active_token(self, user_id: int) -> Optional[Dict]:
        """Получает активный токен пользователя (алиас)"""
        return self.get_wb_token(user_id)

    def get_wb_token_by_id(self, token_id: int) -> Optional[Dict]:
        """Получает токен по ID без привязки к пользователю"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM wb_api_tokens
                WHERE id = ?
            ''', (token_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def update_token_last_used(self, token_id: int):
        """Обновляет время последнего использования токена"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE wb_api_tokens SET last_used = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (token_id,))

    def deactivate_token(self, token_id: int) -> bool:
        """Деактивирует токен"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE wb_api_tokens SET is_active = 0
                WHERE id = ?
            ''', (token_id,))
            return cursor.rowcount > 0

    def delete_token(self, token_id: int) -> bool:
        """Удаляет токен полностью"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Удаляем токен
            cursor.execute('''
                DELETE FROM wb_api_tokens WHERE id = ?
            ''', (token_id,))
            return cursor.rowcount > 0

    def delete_wb_token(self, user_id: int, token_id: int) -> bool:
        """Удаляет токен пользователя (с проверкой владельца)"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Проверяем, что токен принадлежит пользователю
            cursor.execute('''
                SELECT id FROM wb_api_tokens
                WHERE id = ? AND user_id = ?
            ''', (token_id, user_id))
            if not cursor.fetchone():
                return False
            # Удаляем токен
            cursor.execute('''
                DELETE FROM wb_api_tokens WHERE id = ? AND user_id = ?
            ''', (token_id, user_id))
            return cursor.rowcount > 0

    # ==================== WAREHOUSES ====================

    def update_warehouses(self, warehouses: List[Dict]):
        """Обновляет кэш складов"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for wh in warehouses:
                cursor.execute('''
                    INSERT INTO wb_warehouses (id, name, address, work_time, accept_types)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        address = excluded.address,
                        work_time = excluded.work_time,
                        accept_types = excluded.accept_types,
                        updated_at = CURRENT_TIMESTAMP
                ''', (
                    wh['id'],
                    wh.get('name', ''),
                    wh.get('address', ''),
                    wh.get('workTime', ''),
                    json.dumps(wh.get('acceptTypes', []))
                ))

    def get_warehouses(self) -> List[Dict]:
        """Получает список всех складов"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM wb_warehouses ORDER BY name
            ''')
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get('accept_types'):
                    d['accept_types'] = json.loads(d['accept_types'])
                result.append(d)
            return result

    def get_warehouse(self, warehouse_id: int) -> Optional[Dict]:
        """Получает склад по ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM wb_warehouses WHERE id = ?',
                (warehouse_id,)
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                if d.get('accept_types'):
                    d['accept_types'] = json.loads(d['accept_types'])
                return d
            return None

    def get_warehouse_name(self, warehouse_id: int) -> Optional[str]:
        """Получает название склада по ID"""
        # Сначала проверяем в кэше БД
        warehouse = self.get_warehouse(warehouse_id)
        if warehouse:
            return warehouse.get('name')

        # Если нет в БД, используем справочник популярных складов
        from wb_api.warehouses import WarehousesAPI
        popular = WarehousesAPI.POPULAR_WAREHOUSES.get(warehouse_id)
        if popular:
            return popular.get('name')

        return None

    # ==================== STATS ====================

    def get_total_stats(self) -> Dict[str, int]:
        """Получает общую статистику"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
            total_users = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM wb_api_tokens WHERE is_active = 1')
            total_tokens = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM redistribution_requests')
            total_requests = cursor.fetchone()[0]

            return {
                'total_users': total_users,
                'total_tokens': total_tokens,
                'total_requests': total_requests
            }

    # ==================== SUPPLIERS ====================

    def add_supplier(
        self,
        user_id: int,
        name: str,
        token_id: int,
        is_default: bool = False
    ) -> int:
        """Добавляет поставщика"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO suppliers (user_id, name, token_id, is_default)
                VALUES (?, ?, ?, ?)
            ''', (user_id, name, token_id, 1 if is_default else 0))
            return cursor.lastrowid

    def get_suppliers(self, user_id: int) -> List[Dict]:
        """Получает список поставщиков пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.*, t.name as token_name
                FROM suppliers s
                JOIN wb_api_tokens t ON s.token_id = t.id
                WHERE s.user_id = ?
                ORDER BY s.is_default DESC, s.created_at DESC
            ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_supplier(self, supplier_id: int) -> Optional[Dict]:
        """Получает поставщика по ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM suppliers WHERE id = ?', (supplier_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_supplier(self, supplier_id: int) -> bool:
        """Удаляет поставщика"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM suppliers WHERE id = ?', (supplier_id,))
            return cursor.rowcount > 0

    def get_user_suppliers(self, user_id: int) -> List[Dict]:
        """Получает всех поставщиков пользователя с информацией о токенах"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.*, t.name as token_name, t.last_used, t.is_active as token_active
                FROM suppliers s
                JOIN wb_api_tokens t ON s.token_id = t.id
                WHERE s.user_id = ?
                ORDER BY s.is_default DESC, s.created_at DESC
            ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def set_default_supplier(self, user_id: int, supplier_id: int) -> bool:
        """Устанавливает поставщика по умолчанию"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Сначала убрать default со всех поставщиков пользователя
            cursor.execute('''
                UPDATE suppliers SET is_default = 0
                WHERE user_id = ?
            ''', (user_id,))
            # Установить default для выбранного
            cursor.execute('''
                UPDATE suppliers SET is_default = 1
                WHERE id = ? AND user_id = ?
            ''', (supplier_id, user_id))
            return cursor.rowcount > 0

    def update_supplier_name(self, supplier_id: int, name: str) -> bool:
        """Переименовывает поставщика"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE suppliers SET name = ?
                WHERE id = ?
            ''', (name, supplier_id))
            return cursor.rowcount > 0

    def get_supplier_stats(self, supplier_id: int) -> Dict:
        """Получает статистику по поставщику"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Количество операций
            cursor.execute('''
                SELECT COUNT(*) FROM redistribution_requests
                WHERE supplier_id = ?
            ''', (supplier_id,))
            redistributions_count = cursor.fetchone()[0]

            # Последняя активность - берем из last_used токена
            cursor.execute('''
                SELECT t.last_used
                FROM suppliers s
                JOIN wb_api_tokens t ON s.token_id = t.id
                WHERE s.id = ?
            ''', (supplier_id,))
            row = cursor.fetchone()
            last_used = dict(row)['last_used'] if row else None

            return {
                'operations_count': redistributions_count,
                'redistributions_count': redistributions_count,
                'last_used': last_used or 'никогда'
            }

    # ==================== REDISTRIBUTION REQUESTS ====================

    def add_redistribution_request(
        self,
        user_id: int,
        supplier_id: int,
        nm_id: int,
        product_name: str,
        source_warehouse_id: int,
        source_warehouse_name: str,
        target_warehouse_id: int,
        target_warehouse_name: str,
        quantity: int
    ) -> int:
        """Создаёт заявку на перемещение"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO redistribution_requests
                (user_id, supplier_id, nm_id, product_name,
                 source_warehouse_id, source_warehouse_name,
                 target_warehouse_id, target_warehouse_name,
                 quantity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id, supplier_id, nm_id, product_name,
                source_warehouse_id, source_warehouse_name,
                target_warehouse_id, target_warehouse_name,
                quantity
            ))
            return cursor.lastrowid

    def get_redistribution_requests(
        self,
        user_id: int,
        status: str = None
    ) -> List[Dict]:
        """Получает заявки пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('''
                    SELECT r.*, s.name as supplier_name
                    FROM redistribution_requests r
                    JOIN suppliers s ON r.supplier_id = s.id
                    WHERE r.user_id = ? AND r.status = ?
                    ORDER BY r.created_at DESC
                ''', (user_id, status))
            else:
                cursor.execute('''
                    SELECT r.*, s.name as supplier_name
                    FROM redistribution_requests r
                    JOIN suppliers s ON r.supplier_id = s.id
                    WHERE r.user_id = ?
                    ORDER BY r.created_at DESC
                ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_redistribution_request(self, request_id: int) -> Optional[Dict]:
        """Получает заявку по ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT r.*, s.name as supplier_name
                FROM redistribution_requests r
                JOIN suppliers s ON r.supplier_id = s.id
                WHERE r.id = ?
            ''', (request_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_redistribution_request(
        self,
        request_id: int,
        **kwargs
    ) -> bool:
        """Обновляет заявку"""
        allowed_fields = {'quantity', 'status', 'supply_id', 'completed_at'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        with self._get_connection() as conn:
            cursor = conn.cursor()
            set_clause = ', '.join(f'{k} = ?' for k in updates.keys())
            values = list(updates.values()) + [request_id]
            cursor.execute(f'''
                UPDATE redistribution_requests
                SET {set_clause}
                WHERE id = ?
            ''', values)
            return cursor.rowcount > 0

    def delete_redistribution_request(self, request_id: int) -> bool:
        """Удаляет заявку"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM redistribution_requests WHERE id = ?', (request_id,))
            return cursor.rowcount > 0

    # ==================== BROWSER SESSIONS ====================

    def add_browser_session(
        self,
        user_id: int,
        phone: str,
        cookies_encrypted: str,
        supplier_name: str = None,
        expires_days: int = 30
    ) -> int:
        """
        Добавляет или обновляет браузерную сессию пользователя.

        Args:
            user_id: Telegram ID пользователя
            phone: Номер телефона
            cookies_encrypted: Зашифрованные cookies
            supplier_name: Название магазина
            expires_days: Количество дней до истечения сессии

        Returns:
            ID созданной сессии
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Деактивируем старые сессии этого пользователя
            cursor.execute('''
                UPDATE browser_sessions
                SET status = 'inactive'
                WHERE user_id = ? AND status = 'active'
            ''', (user_id,))

            # Создаем новую сессию
            expires_at = datetime.now() + timedelta(days=expires_days)
            cursor.execute('''
                INSERT INTO browser_sessions (
                    user_id, phone, cookies_encrypted, supplier_name,
                    status, last_used_at, expires_at
                )
                VALUES (?, ?, ?, ?, 'active', CURRENT_TIMESTAMP, ?)
            ''', (user_id, phone, cookies_encrypted, supplier_name, expires_at))

            return cursor.lastrowid

    def get_browser_session(self, user_id: int) -> Optional[Dict]:
        """
        Получает активную браузерную сессию пользователя.

        Returns:
            Словарь с данными сессии или None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM browser_sessions
                WHERE user_id = ? AND status = 'active'
                AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                ORDER BY created_at DESC
                LIMIT 1
            ''', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_browser_sessions(self, user_id: int, active_only: bool = True) -> List[Dict]:
        """Получает все сессии браузера пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if active_only:
                cursor.execute('''
                    SELECT * FROM browser_sessions
                    WHERE user_id = ? AND status = 'active'
                    ORDER BY created_at DESC
                ''', (user_id,))
            else:
                cursor.execute('''
                    SELECT * FROM browser_sessions
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_browser_session_by_id(self, session_id: int) -> Optional[Dict]:
        """Получает сессию по ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM browser_sessions WHERE id = ?', (session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_browser_session_last_used(self, session_id: int) -> bool:
        """Обновляет время последнего использования сессии"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE browser_sessions
                SET last_used_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (session_id,))
            return cursor.rowcount > 0

    def update_browser_session_status(
        self,
        session_id: int,
        status: str
    ) -> bool:
        """
        Обновляет статус сессии.

        Args:
            session_id: ID сессии
            status: Новый статус ('active', 'inactive', 'expired')

        Returns:
            True если обновлено успешно
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE browser_sessions
                SET status = ?
                WHERE id = ?
            ''', (status, session_id))
            return cursor.rowcount > 0

    def invalidate_browser_session(self, user_id: int) -> bool:
        """Деактивирует все сессии пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE browser_sessions
                SET status = 'inactive'
                WHERE user_id = ? AND status = 'active'
            ''', (user_id,))
            return cursor.rowcount > 0

    def delete_browser_session(self, session_id: int) -> bool:
        """Удаляет сессию"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM browser_sessions WHERE id = ?', (session_id,))
            return cursor.rowcount > 0

    def cleanup_expired_sessions(self) -> int:
        """
        Очищает истекшие сессии.

        Returns:
            Количество удаленных сессий
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE browser_sessions
                SET status = 'expired'
                WHERE status = 'active'
                AND expires_at IS NOT NULL
                AND expires_at < CURRENT_TIMESTAMP
            ''')
            return cursor.rowcount
