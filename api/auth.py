"""
Валидация Telegram WebApp initData.

Проверка подлинности данных полученных от Telegram Mini App.
"""

import hmac
import hashlib
import json
from urllib.parse import parse_qsl
from typing import Optional, Dict

from fastapi import Header, HTTPException, status


def validate_telegram_web_app_data(
    init_data: str,
    bot_token: str
) -> Dict:
    """
    Валидирует initData от Telegram WebApp.

    Args:
        init_data: Строка initData из Telegram.WebApp.initData
        bot_token: Токен бота

    Returns:
        Словарь с данными пользователя

    Raises:
        HTTPException: Если данные невалидны
    """
    try:
        # Парсим параметры
        parsed_data = dict(parse_qsl(init_data))

        # Извлекаем hash
        received_hash = parsed_data.pop('hash', None)
        if not received_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing hash"
            )

        # Сортируем оставшиеся параметры
        data_check_string = '\n'.join(
            f'{k}={v}' for k, v in sorted(parsed_data.items())
        )

        # Вычисляем hash
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()

        # Проверяем hash
        if not hmac.compare_digest(received_hash, calculated_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid hash"
            )

        # Парсим user
        user_data = json.loads(parsed_data.get('user', '{}'))

        return {
            'user_id': user_data.get('id'),
            'username': user_data.get('username'),
            'first_name': user_data.get('first_name'),
            'last_name': user_data.get('last_name'),
            'auth_date': parsed_data.get('auth_date'),
            'query_id': parsed_data.get('query_id')
        }

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user data"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Validation error: {str(e)}"
        )


async def get_telegram_user(
    x_telegram_init_data: Optional[str] = Header(None),
    bot_token: str = None
) -> Dict:
    """
    Dependency для проверки Telegram WebApp данных.

    Usage:
        @app.get("/api/me")
        async def get_me(user: Dict = Depends(get_telegram_user)):
            return user
    """
    if not x_telegram_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram init data"
        )

    return validate_telegram_web_app_data(x_telegram_init_data, bot_token)
