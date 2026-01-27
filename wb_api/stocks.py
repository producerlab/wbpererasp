"""
API для работы с остатками товаров на складах Wildberries.

Endpoints:
- POST /api/v3/stocks/{warehouseId} - остатки на складе продавца (FBS)
- GET /content/v2/get/cards/list - список карточек товаров
"""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

from .client import WBApiClient, Endpoint

logger = logging.getLogger(__name__)


@dataclass
class StockItem:
    """Остаток товара на складе"""
    sku: str                              # Артикул продавца
    barcode: str                          # Баркод
    nm_id: int                            # Номенклатура WB
    warehouse_id: int                     # ID склада
    warehouse_name: str                   # Название склада
    quantity: int                         # Количество
    in_way_to_client: int = 0             # В пути к клиенту
    in_way_from_client: int = 0           # В пути от клиента
    product_name: str = ""                # Название товара

    @property
    def available(self) -> int:
        """Доступно для перемещения"""
        return max(0, self.quantity - self.in_way_to_client)

    @classmethod
    def from_api_response(
        cls,
        data: Dict[str, Any],
        warehouse_id: int = 0,
        warehouse_name: str = ""
    ) -> 'StockItem':
        """Создаёт StockItem из ответа API"""
        return cls(
            sku=data.get('sku', '') or data.get('supplierArticle', ''),
            barcode=data.get('barcode', ''),
            nm_id=data.get('nmId', 0),
            warehouse_id=warehouse_id or data.get('warehouseId', 0),
            warehouse_name=warehouse_name or data.get('warehouseName', ''),
            quantity=data.get('quantity', 0) or data.get('stock', 0),
            in_way_to_client=data.get('inWayToClient', 0),
            in_way_from_client=data.get('inWayFromClient', 0),
            product_name=data.get('subject', '') or data.get('name', '')
        )


@dataclass
class ProductInfo:
    """Информация о товаре"""
    nm_id: int
    imt_id: int
    sku: str                   # Артикул продавца
    vendor_code: str           # Артикул производителя
    title: str                 # Название
    brand: str                 # Бренд
    photos: List[str] = field(default_factory=list)
    barcodes: List[str] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'ProductInfo':
        """Создаёт ProductInfo из ответа API"""
        photos = []
        if 'photos' in data:
            photos = [p.get('big', '') or p.get('c246x328', '') for p in data['photos']]

        sizes = data.get('sizes', [])
        barcodes = []
        for size in sizes:
            barcodes.extend(size.get('skus', []))

        return cls(
            nm_id=data.get('nmID', 0),
            imt_id=data.get('imtID', 0),
            sku=data.get('vendorCode', ''),
            vendor_code=data.get('vendorCode', ''),
            title=data.get('title', '') or data.get('subjectName', ''),
            brand=data.get('brand', ''),
            photos=photos,
            barcodes=barcodes
        )


@dataclass
class StocksByWarehouse:
    """Остатки товара сгруппированные по складам"""
    sku: str
    product_name: str
    nm_id: int
    total_quantity: int
    warehouses: Dict[int, 'StockItem']  # warehouse_id -> StockItem


class StocksAPI:
    """
    API для работы с остатками товаров.

    Использование:
        async with WBApiClient(token) as client:
            api = StocksAPI(client)
            stocks = await api.get_stocks()
    """

    CONTENT_URL = "https://content-api.wildberries.ru"
    STATISTICS_URL = "https://statistics-api.wildberries.ru"

    def __init__(self, client: WBApiClient):
        self.client = client

    async def get_warehouse_stocks(
        self,
        warehouse_id: int,
        skus: Optional[List[str]] = None
    ) -> List[StockItem]:
        """
        Получает остатки на складе продавца (FBS).

        Args:
            warehouse_id: ID склада продавца
            skus: Фильтр по артикулам (до 1000)

        Returns:
            Список остатков
        """
        try:
            payload = {}
            if skus:
                payload['skus'] = skus[:1000]

            response = await self.client.post(
                f"/api/v3/stocks/{warehouse_id}",
                Endpoint.STOCKS,
                base_url=self.client.MARKETPLACE_URL,
                json=payload if payload else None
            )

            stocks = []
            data = response.get('stocks', []) if isinstance(response, dict) else response

            for item in data:
                try:
                    stock = StockItem.from_api_response(
                        item,
                        warehouse_id=warehouse_id
                    )
                    stocks.append(stock)
                except Exception as e:
                    logger.warning(f"Failed to parse stock item: {e}")

            return stocks

        except Exception as e:
            logger.error(f"Failed to get warehouse stocks: {e}")
            raise

    async def get_all_stocks(
        self,
        date_from: Optional[datetime] = None
    ) -> List[StockItem]:
        """
        Получает все остатки по всем складам WB (FBW).

        Args:
            date_from: Дата начала (по умолчанию сегодня)

        Returns:
            Список всех остатков
        """
        try:
            if date_from is None:
                date_from = datetime.now()

            params = {
                'dateFrom': date_from.strftime('%Y-%m-%dT00:00:00')
            }

            response = await self.client.get(
                "/api/v1/supplier/stocks",
                Endpoint.STOCKS,
                base_url=self.STATISTICS_URL,
                params=params
            )

            stocks = []
            data = response if isinstance(response, list) else response.get('stocks', [])

            for item in data:
                try:
                    stocks.append(StockItem.from_api_response(item))
                except Exception as e:
                    logger.warning(f"Failed to parse stock item: {e}")

            return stocks

        except Exception as e:
            logger.error(f"Failed to get all stocks: {e}")
            raise

    async def get_stocks_grouped_by_sku(self) -> Dict[str, StocksByWarehouse]:
        """
        Получает остатки сгруппированные по артикулам.

        Returns:
            Словарь {sku: StocksByWarehouse}
        """
        all_stocks = await self.get_all_stocks()

        grouped: Dict[str, StocksByWarehouse] = {}

        for stock in all_stocks:
            if stock.sku not in grouped:
                grouped[stock.sku] = StocksByWarehouse(
                    sku=stock.sku,
                    product_name=stock.product_name,
                    nm_id=stock.nm_id,
                    total_quantity=0,
                    warehouses={}
                )

            grouped[stock.sku].warehouses[stock.warehouse_id] = stock
            grouped[stock.sku].total_quantity += stock.quantity

        return grouped

    async def get_stocks_for_sku(
        self,
        sku: str
    ) -> List[StockItem]:
        """
        Получает остатки для конкретного артикула по всем складам.

        Args:
            sku: Артикул продавца

        Returns:
            Список остатков на разных складах
        """
        all_stocks = await self.get_all_stocks()
        return [s for s in all_stocks if s.sku == sku]

    async def get_products_list(
        self,
        limit: int = 100,
        offset: int = 0,
        filter_nm_id: Optional[int] = None
    ) -> List[ProductInfo]:
        """
        Получает список карточек товаров.

        Args:
            limit: Лимит (1-100)
            offset: Смещение
            filter_nm_id: Фильтр по nmId

        Returns:
            Список товаров
        """
        try:
            payload = {
                "settings": {
                    "cursor": {
                        "limit": min(limit, 100)
                    },
                    "filter": {
                        "withPhoto": -1
                    }
                }
            }

            if filter_nm_id:
                payload["settings"]["filter"]["nmID"] = filter_nm_id

            response = await self.client.post(
                "/content/v2/get/cards/list",
                Endpoint.STOCKS,
                base_url=self.CONTENT_URL,
                json=payload
            )

            products = []
            cards = response.get('cards', []) if isinstance(response, dict) else []

            for card in cards:
                try:
                    products.append(ProductInfo.from_api_response(card))
                except Exception as e:
                    logger.warning(f"Failed to parse product: {e}")

            return products

        except Exception as e:
            logger.error(f"Failed to get products list: {e}")
            raise

    async def search_product_by_sku(
        self,
        sku: str
    ) -> Optional[ProductInfo]:
        """
        Ищет товар по артикулу продавца.

        Args:
            sku: Артикул

        Returns:
            ProductInfo или None
        """
        try:
            payload = {
                "vendorCodes": [sku]
            }

            response = await self.client.post(
                "/content/v2/get/cards/list",
                Endpoint.STOCKS,
                base_url=self.CONTENT_URL,
                json=payload
            )

            cards = response.get('cards', [])
            if cards:
                return ProductInfo.from_api_response(cards[0])

            return None

        except Exception as e:
            logger.error(f"Failed to search product: {e}")
            return None
