"""
Telegram handlers для бота перераспределения остатков WB.

Модули:
- redistribution: Перераспределение остатков между складами
- browser_auth: Авторизация через SMS в ЛК WB
"""

from .redistribution import router as redistribution_router
from .browser_auth import router as browser_auth_router

__all__ = [
    'redistribution_router',
    'browser_auth_router',
]
