"""
Шифрование WB API токенов и чувствительных данных.

Использует Fernet (симметричное шифрование) для безопасного хранения токенов,
cookies и номеров телефонов.
"""

import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from config import Config

logger = logging.getLogger(__name__)

# Глобальный экземпляр Fernet
_fernet: Optional[Fernet] = None


def _get_fernet() -> Optional[Fernet]:
    """Получает экземпляр Fernet с ключом из конфига"""
    global _fernet

    if _fernet is not None:
        return _fernet

    key = Config.WB_ENCRYPTION_KEY
    if not key:
        logger.warning("WB_ENCRYPTION_KEY не задан - токены не будут шифроваться!")
        return None

    try:
        _fernet = Fernet(key.encode())
        return _fernet
    except Exception as e:
        logger.error(f"Ошибка инициализации Fernet: {e}")
        return None


def encrypt_token(token: str) -> str:
    """
    Шифрует WB API токен.

    Args:
        token: Исходный токен

    Returns:
        Зашифрованный токен (base64)

    Raises:
        RuntimeError: Если шифрование не настроено или произошла ошибка
    """
    fernet = _get_fernet()
    if fernet is None:
        # КРИТИЧНО: НИКОГДА не сохранять токены в открытом виде
        raise RuntimeError(
            "WB_ENCRYPTION_KEY не настроен! Невозможно безопасно сохранить токен.\n"
            "Сгенерируйте ключ с помощью: python scripts/setup.py"
        )

    try:
        encrypted = fernet.encrypt(token.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Ошибка шифрования токена: {e}")
        raise RuntimeError(f"Не удалось зашифровать токен: {e}")


def decrypt_token(encrypted_token: str) -> str:
    """
    Расшифровывает WB API токен.

    Args:
        encrypted_token: Зашифрованный токен

    Returns:
        Расшифрованный токен или исходный если расшифровка не удалась
    """
    fernet = _get_fernet()
    if fernet is None:
        # Если шифрование не настроено, возвращаем как есть
        return encrypted_token

    try:
        decrypted = fernet.decrypt(encrypted_token.encode())
        return decrypted.decode()
    except InvalidToken:
        # Возможно токен не был зашифрован (старые данные)
        logger.debug("Токен не зашифрован или повреждён, возвращаем как есть")
        return encrypted_token
    except Exception as e:
        logger.error(f"Ошибка расшифровки токена: {e}")
        return encrypted_token


# ========================================
# Функции для работы с номерами телефонов
# ========================================

def hash_phone(phone: str) -> str:
    """
    Создаёт SHA-256 хеш номера телефона для поиска.

    Хеш позволяет искать по телефону без хранения в открытом виде.

    Args:
        phone: Номер телефона (например, +79991234567)

    Returns:
        SHA-256 хеш номера
    """
    # Нормализуем номер перед хешированием
    normalized = phone.strip().replace(' ', '').replace('-', '')
    return hashlib.sha256(normalized.encode()).hexdigest()


def encrypt_phone(phone: str) -> str:
    """
    Шифрует номер телефона для безопасного хранения.

    Args:
        phone: Номер телефона

    Returns:
        Зашифрованный номер (base64)

    Raises:
        RuntimeError: Если шифрование не настроено
    """
    return encrypt_token(phone)


def decrypt_phone(encrypted_phone: str) -> str:
    """
    Расшифровывает номер телефона.

    Args:
        encrypted_phone: Зашифрованный номер

    Returns:
        Расшифрованный номер или исходный если расшифровка не удалась
    """
    return decrypt_token(encrypted_phone)


def get_phone_last4(phone: str) -> str:
    """
    Возвращает последние 4 цифры номера телефона.

    Используется для отображения пользователю без раскрытия полного номера.

    Args:
        phone: Номер телефона

    Returns:
        Последние 4 символа номера
    """
    if not phone:
        return "****"
    return phone[-4:] if len(phone) >= 4 else phone


def mask_phone(phone: str) -> str:
    """
    Маскирует номер телефона для безопасного логирования.

    Пример: +79991234567 -> ****4567

    Args:
        phone: Номер телефона

    Returns:
        Замаскированный номер
    """
    if not phone:
        return "****"
    last4 = phone[-4:] if len(phone) >= 4 else phone
    return f"****{last4}"
