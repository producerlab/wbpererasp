"""
Шифрование WB API токенов.

Использует Fernet (симметричное шифрование) для безопасного хранения токенов.
"""

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
