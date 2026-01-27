"""
Telegram handlers для бота перераспределения остатков WB.

Модули:
- token_management: Управление WB API токенами
- supplier_management: Управление поставщиками
- monitoring: Настройка мониторинга коэффициентов
- booking: Бронирование слотов
- redistribution: Перераспределение остатков между складами
"""

from .token_management import router as token_router
from .supplier_management import router as supplier_router
from .monitoring import router as monitoring_router
from .booking import router as booking_router
from .redistribution import router as redistribution_router

__all__ = [
    'token_router',
    'supplier_router',
    'monitoring_router',
    'booking_router',
    'redistribution_router',
]
