"""
WB API клиенты для работы с Wildberries API.

Модули:
- client: Базовый HTTP клиент с rate limiting
- warehouses: API для работы со складами
- coefficients: API для получения коэффициентов приёмки
- supplies: API для создания поставок
"""

from .client import WBApiClient
from .warehouses import WarehousesAPI
from .coefficients import CoefficientsAPI
from .supplies import SuppliesAPI

__all__ = [
    'WBApiClient',
    'WarehousesAPI',
    'CoefficientsAPI',
    'SuppliesAPI',
]
