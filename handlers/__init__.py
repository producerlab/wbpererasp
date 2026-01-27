"""
Telegram handlers для бота перераспределения остатков WB.

Модули:
- redistribution: Перераспределение остатков между складами
- browser_auth: Авторизация через SMS в ЛК WB
- payment_handler: Оплата и управление балансом
"""

from .redistribution import router as redistribution_router
from .browser_auth import router as browser_auth_router
from .payment_handler import router as payment_router

__all__ = [
    'redistribution_router',
    'browser_auth_router',
    'payment_router',
]
