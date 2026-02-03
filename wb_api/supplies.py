"""
API для создания и управления поставками Wildberries.

Endpoints:
- POST /api/v1/supplies - создание поставки
- GET /api/v1/supplies - список поставок
- POST /api/v1/acceptance/options - доступные опции приёмки
"""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import IntEnum

from .client import WBApiClient, Endpoint, WBApiError

logger = logging.getLogger(__name__)


class SupplyStatus(IntEnum):
    """Статусы поставки"""
    NEW = 0                 # Новая
    IN_PROGRESS = 1         # В процессе
    DELIVERED = 2           # Доставлена
    ACCEPTED = 3            # Принята
    REJECTED = 4            # Отклонена
    CANCELLED = 5           # Отменена


class CargoType(IntEnum):
    """Типы грузов"""
    BOX = 0                 # Короба
    MONOPALLET = 1          # Монопаллеты


@dataclass
class AcceptanceOption:
    """Опция приёмки на склад"""
    warehouse_id: int
    warehouse_name: str
    date: date
    coefficient: float
    cargo_type: CargoType
    box_type_id: Optional[int] = None
    box_type_name: Optional[str] = None

    @property
    def is_profitable(self) -> bool:
        """Выгодный коэффициент"""
        return 0 <= self.coefficient <= 1

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'AcceptanceOption':
        """Создаёт AcceptanceOption из ответа API"""
        date_str = data.get('date', '')
        try:
            opt_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            opt_date = date.today()

        return cls(
            warehouse_id=data.get('warehouseID', 0),
            warehouse_name=data.get('warehouseName', ''),
            date=opt_date,
            coefficient=data.get('coefficient', -1),
            cargo_type=CargoType(data.get('cargoType', 0)),
            box_type_id=data.get('boxTypeID'),
            box_type_name=data.get('boxTypeName'),
        )


@dataclass
class Supply:
    """Поставка на склад WB"""
    id: str                           # ID в формате WB-GI-XXXXXXX
    name: str
    warehouse_id: int
    status: SupplyStatus
    cargo_type: CargoType
    created_at: datetime
    closed_at: Optional[datetime] = None
    scan_date: Optional[date] = None  # Дата приёмки
    orders_count: int = 0

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'Supply':
        """Создаёт Supply из ответа API"""
        created_str = data.get('createdAt', '')
        try:
            created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
        except ValueError:
            created = datetime.now()

        closed = None
        if data.get('closedAt'):
            try:
                closed = datetime.fromisoformat(
                    data['closedAt'].replace('Z', '+00:00')
                )
            except ValueError:
                pass

        scan_date = None
        if data.get('scanDt'):
            try:
                scan_date = datetime.strptime(
                    data['scanDt'][:10], '%Y-%m-%d'
                ).date()
            except ValueError:
                pass

        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            warehouse_id=data.get('warehouseId', 0),
            status=SupplyStatus(data.get('status', 0)),
            cargo_type=CargoType(data.get('cargoType', 0)),
            created_at=created,
            closed_at=closed,
            scan_date=scan_date,
            orders_count=data.get('ordersCount', 0),
        )


@dataclass
class BookingResult:
    """Результат бронирования слота"""
    success: bool
    supply_id: Optional[str] = None
    warehouse_id: Optional[int] = None
    coefficient: Optional[float] = None
    date: Optional[date] = None
    error_message: Optional[str] = None
    raw_response: Optional[Dict] = None
    user_id: Optional[int] = None  # ID пользователя (для автобронирования)


class SuppliesAPI:
    """
    API для работы с поставками WB.

    Использование:
        async with WBApiClient(token) as client:
            api = SuppliesAPI(client)
            options = await api.get_acceptance_options(skus=['123', '456'])
            result = await api.create_supply(warehouse_id=117501, name='My Supply')
    """

    def __init__(self, client: WBApiClient):
        self.client = client

    async def get_acceptance_options(
        self,
        skus: List[str],
        quantities: Optional[List[int]] = None
    ) -> List[AcceptanceOption]:
        """
        Получает доступные опции приёмки для товаров.

        Args:
            skus: Список артикулов/баркодов
            quantities: Количество каждого товара (опционально)

        Returns:
            Список доступных опций приёмки
        """
        try:
            payload = {"skus": skus}
            if quantities:
                payload["quantities"] = quantities

            response = await self.client.post(
                "/api/v1/acceptance/options",
                Endpoint.ACCEPTANCE,
                base_url=self.client.SUPPLIES_URL,
                json=payload
            )

            options = []
            data = response if isinstance(response, list) else response.get('data', [])

            for item in data:
                try:
                    options.append(AcceptanceOption.from_api_response(item))
                except Exception as e:
                    logger.warning(f"Failed to parse acceptance option: {e}")

            return options

        except Exception as e:
            logger.error(f"Failed to get acceptance options: {e}")
            raise

    async def create_supply(
        self,
        name: str,
        warehouse_id: Optional[int] = None,
        cargo_type: CargoType = CargoType.BOX
    ) -> BookingResult:
        """
        Создаёт новую поставку.

        Args:
            name: Название поставки
            warehouse_id: ID склада (опционально, WB подберёт)
            cargo_type: Тип груза

        Returns:
            Результат создания поставки
        """
        try:
            payload = {
                "name": name,
                "cargoType": cargo_type.value
            }

            if warehouse_id:
                payload["preOrderID"] = warehouse_id  # или warehouseID в зависимости от версии API

            response = await self.client.post(
                "/api/v1/supplies",
                Endpoint.SUPPLIES,
                base_url=self.client.SUPPLIES_URL,
                json=payload
            )

            supply_id = response.get('id') or response.get('supplyId')

            if supply_id:
                logger.info(f"Created supply {supply_id} for warehouse {warehouse_id}")
                return BookingResult(
                    success=True,
                    supply_id=supply_id,
                    warehouse_id=warehouse_id,
                    raw_response=response
                )
            else:
                return BookingResult(
                    success=False,
                    error_message="No supply ID in response",
                    raw_response=response
                )

        except WBApiError as e:
            logger.error(f"Failed to create supply: {e}")
            return BookingResult(
                success=False,
                error_message=str(e),
                raw_response={"error": e.response} if e.response else None
            )
