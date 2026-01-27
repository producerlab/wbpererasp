"""
API для работы со складами Wildberries.

Endpoints:
- GET /api/v1/warehouses - список всех складов WB
- GET /api/v3/warehouses - склады продавца (FBS)
"""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from .client import WBApiClient, Endpoint

logger = logging.getLogger(__name__)


@dataclass
class Warehouse:
    """Модель склада WB"""
    id: int
    name: str
    city: Optional[str] = None
    region: Optional[str] = None          # Федеральный округ
    address: Optional[str] = None
    accepts_goods: bool = True
    cargo_type: Optional[int] = None      # 0 - короб, 1 - монопаллет
    delivery_type: Optional[int] = None   # 1 - Доставка на склад, 2 - Курьер

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'Warehouse':
        """Создаёт Warehouse из ответа API"""
        return cls(
            id=data.get('ID') or data.get('id'),
            name=data.get('name', ''),
            city=data.get('city'),
            region=data.get('address'),  # WB возвращает регион в address
            address=data.get('address'),
            accepts_goods=data.get('acceptsQR', True),
            cargo_type=data.get('cargoType'),
            delivery_type=data.get('deliveryType'),
        )


class WarehousesAPI:
    """
    API для работы со складами WB.

    Использование:
        async with WBApiClient(token) as client:
            api = WarehousesAPI(client)
            warehouses = await api.get_all_warehouses()
    """

    # Популярные склады WB с их регионами
    POPULAR_WAREHOUSES = {
        # Москва и МО
        117501: {"name": "Коледино", "region": "Центральный"},
        117986: {"name": "Электросталь", "region": "Центральный"},
        507: {"name": "Подольск", "region": "Центральный"},
        206348: {"name": "Тула", "region": "Центральный"},
        206236: {"name": "Белые Столбы", "region": "Центральный"},
        208277: {"name": "Чехов 2", "region": "Центральный"},
        211622: {"name": "Котовск", "region": "Центральный"},

        # Санкт-Петербург
        130744: {"name": "Санкт-Петербург (Шушары)", "region": "Северо-Западный"},
        210001: {"name": "СПб Уткина Заводь", "region": "Северо-Западный"},

        # Приволжский ФО
        1733: {"name": "Казань", "region": "Приволжский"},
        208941: {"name": "Самара", "region": "Приволжский"},

        # Уральский ФО
        218210: {"name": "Екатеринбург (Испытателей)", "region": "Уральский"},
        120762: {"name": "Екатеринбург", "region": "Уральский"},

        # Сибирский ФО
        686: {"name": "Новосибирск", "region": "Сибирский"},
        204939: {"name": "Красноярск", "region": "Сибирский"},

        # Южный ФО
        2737: {"name": "Краснодар", "region": "Южный"},
        205228: {"name": "Ростов-на-Дону", "region": "Южный"},
        1193: {"name": "Волгоград", "region": "Южный"},
    }

    def __init__(self, client: WBApiClient):
        self.client = client

    async def get_all_warehouses(self) -> List[Warehouse]:
        """
        Получает список всех складов WB.

        Returns:
            Список объектов Warehouse
        """
        try:
            response = await self.client.get(
                "/api/v1/warehouses",
                Endpoint.WAREHOUSES
            )

            warehouses = []
            if isinstance(response, list):
                for item in response:
                    try:
                        wh = Warehouse.from_api_response(item)
                        # Дополняем информацией из справочника
                        if wh.id in self.POPULAR_WAREHOUSES:
                            info = self.POPULAR_WAREHOUSES[wh.id]
                            wh.region = info.get("region")
                            if not wh.name:
                                wh.name = info.get("name", "")
                        warehouses.append(wh)
                    except Exception as e:
                        logger.warning(f"Failed to parse warehouse: {e}")
                        continue

            logger.info(f"Loaded {len(warehouses)} warehouses")
            return warehouses

        except Exception as e:
            logger.error(f"Failed to get warehouses: {e}")
            raise

    async def get_seller_warehouses(self) -> List[Warehouse]:
        """
        Получает склады продавца (для FBS).

        Returns:
            Список складов продавца
        """
        try:
            response = await self.client.get(
                "/api/v3/warehouses",
                Endpoint.WAREHOUSES,
                base_url=self.client.MARKETPLACE_URL
            )

            warehouses = []
            if isinstance(response, list):
                for item in response:
                    try:
                        warehouses.append(Warehouse.from_api_response(item))
                    except Exception as e:
                        logger.warning(f"Failed to parse seller warehouse: {e}")

            return warehouses

        except Exception as e:
            logger.error(f"Failed to get seller warehouses: {e}")
            raise

    async def get_warehouse_by_id(self, warehouse_id: int) -> Optional[Warehouse]:
        """
        Получает информацию о конкретном складе.

        Args:
            warehouse_id: ID склада

        Returns:
            Warehouse или None если не найден
        """
        warehouses = await self.get_all_warehouses()
        for wh in warehouses:
            if wh.id == warehouse_id:
                return wh
        return None

    def get_warehouses_by_region(
        self,
        warehouses: List[Warehouse],
        region: str
    ) -> List[Warehouse]:
        """
        Фильтрует склады по региону.

        Args:
            warehouses: Список складов
            region: Название региона (Центральный, Северо-Западный, etc.)

        Returns:
            Отфильтрованный список
        """
        return [wh for wh in warehouses if wh.region == region]

    def get_popular_warehouse_ids(self) -> List[int]:
        """Возвращает ID популярных складов для мониторинга"""
        return list(self.POPULAR_WAREHOUSES.keys())
