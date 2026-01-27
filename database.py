"""
База данных WB Redistribution Bot.

Таблицы:
- users: пользователи бота
- wb_api_tokens: зашифрованные WB API токены
- monitoring_subscriptions: подписки на мониторинг складов
- slot_bookings: история бронирований слотов
- wb_warehouses: кэш складов WB
- coefficient_history: история коэффициентов (для аналитики)
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

            # Подписки на мониторинг
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS monitoring_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_id INTEGER NOT NULL,
                    warehouse_ids TEXT NOT NULL,
                    target_coefficients TEXT DEFAULT '[0, 0.5, 1]',
                    auto_book INTEGER DEFAULT 0,
                    auto_book_max_coefficient REAL DEFAULT 1.0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (token_id) REFERENCES wb_api_tokens(id)
                )
            ''')

            # История бронирований
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS slot_bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_id INTEGER,
                    warehouse_id INTEGER NOT NULL,
                    warehouse_name TEXT,
                    supply_id TEXT,
                    coefficient REAL NOT NULL,
                    slot_date DATE NOT NULL,
                    box_type TEXT DEFAULT 'Короба',
                    booking_type TEXT DEFAULT 'manual',
                    status TEXT DEFAULT 'pending',
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (token_id) REFERENCES wb_api_tokens(id)
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

            # История коэффициентов (для аналитики)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS coefficient_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    warehouse_id INTEGER NOT NULL,
                    coefficient REAL NOT NULL,
                    date DATE NOT NULL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (warehouse_id) REFERENCES wb_warehouses(id)
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

            # Индексы для быстрого поиска
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_tokens_user
                ON wb_api_tokens(user_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_subscriptions_user
                ON monitoring_subscriptions(user_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_bookings_user
                ON slot_bookings(user_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_coeff_history_warehouse
                ON coefficient_history(warehouse_id, date)
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
            # Сначала удаляем связанные подписки
            cursor.execute('''
                DELETE FROM monitoring_subscriptions WHERE token_id = ?
            ''', (token_id,))
            # Затем сам токен
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
            # Удаляем связанные подписки
            cursor.execute('''
                DELETE FROM monitoring_subscriptions WHERE token_id = ?
            ''', (token_id,))
            # Удаляем токен
            cursor.execute('''
                DELETE FROM wb_api_tokens WHERE id = ? AND user_id = ?
            ''', (token_id, user_id))
            return cursor.rowcount > 0

    # ==================== MONITORING ====================

    def add_monitoring_subscription(
        self,
        user_id: int,
        token_id: int,
        warehouse_ids: List[int],
        target_coefficients: List[float] = None,
        auto_book: bool = False,
        auto_book_max_coefficient: float = 1.0
    ) -> int:
        """Создаёт подписку на мониторинг"""
        if target_coefficients is None:
            target_coefficients = [0, 0.5, 1]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO monitoring_subscriptions
                (user_id, token_id, warehouse_ids, target_coefficients,
                 auto_book, auto_book_max_coefficient)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                user_id,
                token_id,
                json.dumps(warehouse_ids),
                json.dumps(target_coefficients),
                1 if auto_book else 0,
                auto_book_max_coefficient
            ))
            return cursor.lastrowid

    def get_user_subscriptions(self, user_id: int) -> List[Dict]:
        """Получает все подписки пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ms.*, wt.name as token_name
                FROM monitoring_subscriptions ms
                JOIN wb_api_tokens wt ON ms.token_id = wt.id
                WHERE ms.user_id = ? AND ms.is_active = 1
            ''', (user_id,))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d['warehouse_ids'] = json.loads(d['warehouse_ids'])
                d['target_coefficients'] = json.loads(d['target_coefficients'])
                result.append(d)
            return result

    def get_active_subscriptions_for_warehouses(
        self,
        warehouse_ids: List[int]
    ) -> List[Dict]:
        """
        Получает все активные подписки для списка складов.
        Используется мониторингом для рассылки уведомлений.
        """
        from utils.encryption import decrypt_token

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ms.*, wt.encrypted_token
                FROM monitoring_subscriptions ms
                JOIN wb_api_tokens wt ON ms.token_id = wt.id
                WHERE ms.is_active = 1 AND wt.is_active = 1
            ''')
            rows = cursor.fetchall()

            result = []
            warehouse_set = set(warehouse_ids)
            for row in rows:
                d = dict(row)
                sub_warehouses = set(json.loads(d['warehouse_ids']))
                # Проверяем пересечение
                if sub_warehouses & warehouse_set:
                    d['warehouse_ids'] = list(sub_warehouses)
                    d['target_coefficients'] = json.loads(
                        d['target_coefficients']
                    )
                    # Расшифровываем токен
                    encrypted = d.get('encrypted_token', '')
                    d['api_token'] = decrypt_token(encrypted) if encrypted else ''
                    result.append(d)
            return result

    def update_subscription(
        self,
        subscription_id: int,
        **kwargs
    ) -> bool:
        """Обновляет подписку"""
        allowed_fields = {
            'warehouse_ids', 'target_coefficients',
            'auto_book', 'auto_book_max_coefficient', 'is_active'
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        # Сериализуем JSON поля
        if 'warehouse_ids' in updates:
            updates['warehouse_ids'] = json.dumps(updates['warehouse_ids'])
        if 'target_coefficients' in updates:
            updates['target_coefficients'] = json.dumps(
                updates['target_coefficients']
            )

        with self._get_connection() as conn:
            cursor = conn.cursor()
            set_clause = ', '.join(f'{k} = ?' for k in updates.keys())
            values = list(updates.values()) + [subscription_id]
            cursor.execute(f'''
                UPDATE monitoring_subscriptions
                SET {set_clause}
                WHERE id = ?
            ''', values)
            return cursor.rowcount > 0

    def deactivate_subscription(self, subscription_id: int) -> bool:
        """Деактивирует подписку"""
        return self.update_subscription(subscription_id, is_active=0)

    def get_subscription_by_id(self, subscription_id: int) -> Optional[Dict]:
        """Получает подписку по ID"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM monitoring_subscriptions
                WHERE id = ?
            ''', (subscription_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d['warehouse_ids'] = json.loads(d['warehouse_ids'])
                d['target_coefficients'] = json.loads(d['target_coefficients'])
                return d
            return None

    def toggle_subscription(self, subscription_id: int, is_active: bool) -> bool:
        """Включает/выключает подписку"""
        return self.update_subscription(subscription_id, is_active=1 if is_active else 0)

    def delete_subscription(self, subscription_id: int) -> bool:
        """Удаляет подписку"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM monitoring_subscriptions
                WHERE id = ?
            ''', (subscription_id,))
            return cursor.rowcount > 0

    # ==================== BOOKINGS ====================

    def add_booking(
        self,
        user_id: int,
        warehouse_id: int,
        warehouse_name: str,
        coefficient: float,
        slot_date,
        token_id: int = None,
        box_type: str = "Короба",
        booking_type: str = "manual",
        supply_id: str = None,
        status: str = "pending"
    ) -> int:
        """Создаёт запись о бронировании"""
        # Конвертируем дату в строку если нужно
        if hasattr(slot_date, 'isoformat'):
            slot_date = slot_date.isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO slot_bookings
                (user_id, token_id, warehouse_id, warehouse_name, coefficient,
                 slot_date, box_type, booking_type, supply_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id, token_id, warehouse_id, warehouse_name,
                coefficient, slot_date, box_type, booking_type, supply_id, status
            ))
            return cursor.lastrowid

    def update_booking_status(
        self,
        booking_id: int,
        status: str,
        supply_id: str = None,
        error_message: str = None
    ) -> bool:
        """Обновляет статус бронирования"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status in ('confirmed', 'cancelled', 'failed'):
                cursor.execute('''
                    UPDATE slot_bookings
                    SET status = ?, supply_id = ?, error_message = ?,
                        completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, supply_id, error_message, booking_id))
            else:
                cursor.execute('''
                    UPDATE slot_bookings
                    SET status = ?, supply_id = ?, error_message = ?
                    WHERE id = ?
                ''', (status, supply_id, error_message, booking_id))
            return cursor.rowcount > 0

    def update_booking_type(self, supply_id: str, booking_type: str) -> bool:
        """Обновляет тип бронирования по supply_id"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE slot_bookings
                SET booking_type = ?
                WHERE supply_id = ?
            ''', (booking_type, supply_id))
            return cursor.rowcount > 0

    def get_user_bookings(
        self,
        user_id: int,
        limit: int = 20,
        status: str = None
    ) -> List[Dict]:
        """Получает историю бронирований пользователя"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('''
                    SELECT id, user_id, token_id, warehouse_id, warehouse_name,
                           supply_id, coefficient, slot_date, box_type,
                           COALESCE(booking_type, 'manual') as booking_type,
                           status, error_message, created_at, completed_at
                    FROM slot_bookings
                    WHERE user_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (user_id, status, limit))
            else:
                cursor.execute('''
                    SELECT id, user_id, token_id, warehouse_id, warehouse_name,
                           supply_id, coefficient, slot_date, box_type,
                           COALESCE(booking_type, 'manual') as booking_type,
                           status, error_message, created_at, completed_at
                    FROM slot_bookings
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_today_bookings_count(self, user_id: int) -> int:
        """Считает количество бронирований за сегодня"""
        today = datetime.now().strftime('%Y-%m-%d')
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) FROM slot_bookings
                WHERE user_id = ?
                AND DATE(created_at) = ?
                AND status IN ('pending', 'confirmed')
            ''', (user_id, today))
            return cursor.fetchone()[0]

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

    # ==================== COEFFICIENT HISTORY ====================

    def add_coefficient_record(
        self,
        warehouse_id: int,
        coefficient: float,
        date: str
    ):
        """Записывает коэффициент в историю"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO coefficient_history (warehouse_id, coefficient, date)
                VALUES (?, ?, ?)
            ''', (warehouse_id, coefficient, date))

    def get_coefficient_history(
        self,
        warehouse_id: int,
        days: int = 7
    ) -> List[Dict]:
        """Получает историю коэффициентов за N дней"""
        from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, AVG(coefficient) as avg_coefficient,
                       MIN(coefficient) as min_coefficient,
                       MAX(coefficient) as max_coefficient
                FROM coefficient_history
                WHERE warehouse_id = ? AND date >= ?
                GROUP BY date
                ORDER BY date
            ''', (warehouse_id, from_date))
            return [dict(row) for row in cursor.fetchall()]

    # ==================== STATS ====================

    def get_total_stats(self) -> Dict[str, int]:
        """Получает общую статистику"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
            total_users = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM wb_api_tokens WHERE is_active = 1')
            total_tokens = cursor.fetchone()[0]

            cursor.execute(
                'SELECT COUNT(*) FROM monitoring_subscriptions WHERE is_active = 1'
            )
            total_subscriptions = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM slot_bookings')
            total_bookings = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM slot_bookings WHERE status = 'confirmed'"
            )
            successful_bookings = cursor.fetchone()[0]

            return {
                'total_users': total_users,
                'total_tokens': total_tokens,
                'total_subscriptions': total_subscriptions,
                'total_bookings': total_bookings,
                'successful_bookings': successful_bookings
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

            cursor.execute('''
                SELECT COUNT(*) FROM slot_bookings sb
                JOIN suppliers s ON sb.token_id = s.token_id
                WHERE s.id = ?
            ''', (supplier_id,))
            bookings_count = cursor.fetchone()[0]

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
                'operations_count': redistributions_count + bookings_count,
                'redistributions_count': redistributions_count,
                'bookings_count': bookings_count,
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
