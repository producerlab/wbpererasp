"""
Rate Limiter для защиты от спама команд.

Ограничивает частоту выполнения действий (авторизация, отправка SMS кодов и т.д.)
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class RateLimitEntry:
    """Запись о попытках пользователя"""
    attempts: int = 0
    first_attempt: float = field(default_factory=time.time)
    last_attempt: float = field(default_factory=time.time)
    blocked_until: Optional[float] = None


class RateLimiter:
    """
    Rate limiter для защиты от спама.

    Использование:
        limiter = RateLimiter(max_attempts=3, window_seconds=3600, block_seconds=900)

        # Проверить можно ли выполнить действие
        if limiter.is_allowed(user_id):
            # Выполнить действие
            limiter.record_attempt(user_id)
        else:
            # Заблокировано
            remaining = limiter.get_block_remaining(user_id)
    """

    def __init__(
        self,
        max_attempts: int = 3,
        window_seconds: int = 3600,
        block_seconds: int = 900,
        name: str = "default"
    ):
        """
        Args:
            max_attempts: Максимум попыток в окне
            window_seconds: Размер окна в секундах (по умолчанию 1 час)
            block_seconds: Время блокировки в секундах (по умолчанию 15 минут)
            name: Название лимитера для логов
        """
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self.name = name
        self._entries: Dict[int, RateLimitEntry] = {}

    def _get_entry(self, user_id: int) -> RateLimitEntry:
        """Получает или создаёт запись для пользователя"""
        if user_id not in self._entries:
            self._entries[user_id] = RateLimitEntry()
        return self._entries[user_id]

    def _cleanup_entry(self, entry: RateLimitEntry) -> None:
        """Очищает устаревшие данные из записи"""
        now = time.time()

        # Если прошло больше времени чем окно - сбрасываем счётчик
        if now - entry.first_attempt > self.window_seconds:
            entry.attempts = 0
            entry.first_attempt = now

        # Если блокировка истекла - снимаем её
        if entry.blocked_until and now > entry.blocked_until:
            entry.blocked_until = None
            entry.attempts = 0
            entry.first_attempt = now

    def is_allowed(self, user_id: int) -> bool:
        """
        Проверяет, разрешено ли действие для пользователя.

        Args:
            user_id: ID пользователя

        Returns:
            True если действие разрешено
        """
        entry = self._get_entry(user_id)
        self._cleanup_entry(entry)

        # Если заблокирован
        if entry.blocked_until:
            return False

        # Если превышен лимит
        if entry.attempts >= self.max_attempts:
            # Блокируем
            entry.blocked_until = time.time() + self.block_seconds
            logger.warning(
                f"[RateLimit:{self.name}] User {user_id} blocked for {self.block_seconds}s "
                f"after {entry.attempts} attempts"
            )
            return False

        return True

    def record_attempt(self, user_id: int) -> None:
        """
        Записывает попытку.

        Args:
            user_id: ID пользователя
        """
        entry = self._get_entry(user_id)
        self._cleanup_entry(entry)

        entry.attempts += 1
        entry.last_attempt = time.time()

        logger.debug(
            f"[RateLimit:{self.name}] User {user_id}: attempt {entry.attempts}/{self.max_attempts}"
        )

    def get_block_remaining(self, user_id: int) -> int:
        """
        Возвращает оставшееся время блокировки в секундах.

        Args:
            user_id: ID пользователя

        Returns:
            Секунды до разблокировки или 0 если не заблокирован
        """
        entry = self._get_entry(user_id)
        self._cleanup_entry(entry)

        if entry.blocked_until:
            remaining = int(entry.blocked_until - time.time())
            return max(0, remaining)
        return 0

    def reset(self, user_id: int) -> None:
        """
        Сбрасывает счётчик для пользователя (например, после успешной операции).

        Args:
            user_id: ID пользователя
        """
        if user_id in self._entries:
            del self._entries[user_id]

    def get_attempts_remaining(self, user_id: int) -> int:
        """
        Возвращает количество оставшихся попыток.

        Args:
            user_id: ID пользователя

        Returns:
            Количество оставшихся попыток
        """
        entry = self._get_entry(user_id)
        self._cleanup_entry(entry)
        return max(0, self.max_attempts - entry.attempts)


# ========================================
# Предустановленные лимитеры
# ========================================

# Лимит на авторизацию: 3 попытки в час, блокировка на 15 минут
auth_limiter = RateLimiter(
    max_attempts=3,
    window_seconds=3600,  # 1 час
    block_seconds=900,    # 15 минут
    name="auth"
)

# Лимит на SMS коды: 5 неверных кодов, блокировка на 10 минут
sms_code_limiter = RateLimiter(
    max_attempts=5,
    window_seconds=1800,  # 30 минут
    block_seconds=600,    # 10 минут
    name="sms_code"
)

# Лимит на команды бота: 30 команд в минуту
command_limiter = RateLimiter(
    max_attempts=30,
    window_seconds=60,    # 1 минута
    block_seconds=60,     # 1 минута
    name="commands"
)


def format_remaining_time(seconds: int) -> str:
    """
    Форматирует оставшееся время в читаемый вид.

    Args:
        seconds: Секунды

    Returns:
        Строка вида "5 мин 30 сек"
    """
    if seconds <= 0:
        return "0 сек"

    minutes = seconds // 60
    secs = seconds % 60

    if minutes > 0:
        return f"{minutes} мин {secs} сек"
    return f"{secs} сек"
