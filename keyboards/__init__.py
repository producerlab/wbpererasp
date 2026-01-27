"""
Клавиатуры для Telegram бота.
"""

from .menus import (
    get_main_menu,
    get_token_menu,
    get_warehouses_keyboard,
    get_coefficients_keyboard,
)

__all__ = [
    'get_main_menu',
    'get_token_menu',
    'get_warehouses_keyboard',
    'get_coefficients_keyboard',
]
