"""
Сервис перераспределения остатков между складами WB.

Реализует:
- Получение остатков по артикулам
- Создание заявок на перемещение
- Проверку возможности перемещения
"""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import date

from database import Database
from wb_api.client import WBApiClient
from wb_api.stocks import StocksAPI, StockItem, StocksByWarehouse
from wb_api.supplies import SuppliesAPI, BookingResult, CargoType
from wb_api.warehouses import WarehousesAPI
from wb_api.coefficients import CoefficientsAPI

logger = logging.getLogger(__name__)


@dataclass
class RedistributionRequest:
    """Запрос на перераспределение"""
    sku: str
    source_warehouse_id: int
    target_warehouse_id: int
    quantity: int
    source_warehouse_name: str = ""
    target_warehouse_name: str = ""
    product_name: str = ""


@dataclass
class RedistributionResult:
    """Результат перераспределения"""
    success: bool
    request: RedistributionRequest
    supply_id: Optional[str] = None
    error_message: Optional[str] = None
    coefficient: Optional[float] = None


class RedistributionService:
    """
    Сервис перераспределения остатков.

    Использование:
        service = RedistributionService(db)
        stocks = await service.get_stocks_for_redistribution(api_token)
        result = await service.create_redistribution(api_token, request)
    """

    def __init__(self, db: Database):
        self.db = db

    async def get_stocks_for_redistribution(
        self,
        api_token: str
    ) -> Dict[str, StocksByWarehouse]:
        """
        Получает остатки для перераспределения.

        Args:
            api_token: WB API токен

        Returns:
            Остатки сгруппированные по SKU
        """
        async with WBApiClient(api_token) as client:
            api = StocksAPI(client)
            return await api.get_stocks_grouped_by_sku()

    async def get_stocks_for_sku(
        self,
        api_token: str,
        sku: str
    ) -> List[StockItem]:
        """
        Получает остатки для конкретного артикула.

        Args:
            api_token: WB API токен
            sku: Артикул

        Returns:
            Список остатков на разных складах
        """
        async with WBApiClient(api_token) as client:
            api = StocksAPI(client)
            return await api.get_stocks_for_sku(sku)

    async def get_available_quantity(
        self,
        api_token: str,
        sku: str,
        warehouse_id: int
    ) -> int:
        """
        Получает доступное количество на складе.

        Args:
            api_token: WB API токен
            sku: Артикул
            warehouse_id: ID склада

        Returns:
            Доступное количество
        """
        stocks = await self.get_stocks_for_sku(api_token, sku)

        for stock in stocks:
            if stock.warehouse_id == warehouse_id:
                return stock.available

        return 0

    async def get_target_warehouse_coefficient(
        self,
        api_token: str,
        warehouse_id: int,
        target_date: Optional[date] = None
    ) -> Optional[float]:
        """
        Получает коэффициент для склада-назначения.

        Args:
            api_token: WB API токен
            warehouse_id: ID склада
            target_date: Целевая дата

        Returns:
            Коэффициент или None
        """
        async with WBApiClient(api_token) as client:
            api = CoefficientsAPI(client)
            coefficients = await api.get_acceptance_coefficients([warehouse_id])

            if not coefficients:
                return None

            # Ищем ближайшую доступную дату
            available = [c for c in coefficients if c.is_available]
            if not available:
                return None

            # Если есть целевая дата - ищем её
            if target_date:
                for c in available:
                    if c.date == target_date:
                        return c.coefficient

            # Иначе берём ближайшую
            sorted_coeffs = sorted(available, key=lambda x: x.date)
            return sorted_coeffs[0].coefficient if sorted_coeffs else None

    async def validate_redistribution(
        self,
        api_token: str,
        request: RedistributionRequest
    ) -> tuple[bool, Optional[str]]:
        """
        Проверяет возможность перераспределения.

        Args:
            api_token: WB API токен
            request: Запрос на перераспределение

        Returns:
            (success, error_message)
        """
        # Проверяем наличие товара на складе-источнике
        available = await self.get_available_quantity(
            api_token,
            request.sku,
            request.source_warehouse_id
        )

        if available < request.quantity:
            return False, f"Недостаточно товара. Доступно: {available}, запрошено: {request.quantity}"

        # Проверяем что склады разные
        if request.source_warehouse_id == request.target_warehouse_id:
            return False, "Склад-источник и склад-назначение совпадают"

        # Проверяем количество
        if request.quantity <= 0:
            return False, "Количество должно быть больше 0"

        return True, None

    async def create_redistribution(
        self,
        api_token: str,
        user_id: int,
        request: RedistributionRequest
    ) -> RedistributionResult:
        """
        Создаёт заявку на перераспределение.

        Алгоритм:
        1. Проверяем валидность запроса
        2. Получаем коэффициент для склада-назначения
        3. Создаём поставку на склад-назначение
        4. Сохраняем в БД

        Args:
            api_token: WB API токен
            user_id: ID пользователя
            request: Запрос на перераспределение

        Returns:
            RedistributionResult
        """
        # Валидация
        is_valid, error = await self.validate_redistribution(api_token, request)
        if not is_valid:
            return RedistributionResult(
                success=False,
                request=request,
                error_message=error
            )

        # Получаем названия складов
        warehouses = WarehousesAPI.POPULAR_WAREHOUSES
        source_name = warehouses.get(request.source_warehouse_id, {}).get('name', str(request.source_warehouse_id))
        target_name = warehouses.get(request.target_warehouse_id, {}).get('name', str(request.target_warehouse_id))

        request.source_warehouse_name = source_name
        request.target_warehouse_name = target_name

        # Получаем коэффициент
        coefficient = await self.get_target_warehouse_coefficient(
            api_token,
            request.target_warehouse_id
        )

        try:
            async with WBApiClient(api_token) as client:
                api = SuppliesAPI(client)

                # Создаём поставку
                supply_name = f"Перемещение {request.sku} → {target_name}"
                result = await api.create_supply(
                    name=supply_name,
                    warehouse_id=request.target_warehouse_id,
                    cargo_type=CargoType.BOX
                )

                if result.success:
                    # Сохраняем в БД
                    self._save_redistribution(
                        user_id=user_id,
                        request=request,
                        supply_id=result.supply_id,
                        coefficient=coefficient
                    )

                    logger.info(
                        f"Created redistribution: {request.sku} "
                        f"{source_name} → {target_name} "
                        f"({request.quantity} шт)"
                    )

                    return RedistributionResult(
                        success=True,
                        request=request,
                        supply_id=result.supply_id,
                        coefficient=coefficient
                    )
                else:
                    return RedistributionResult(
                        success=False,
                        request=request,
                        error_message=result.error_message
                    )

        except Exception as e:
            logger.error(f"Redistribution failed: {e}")
            return RedistributionResult(
                success=False,
                request=request,
                error_message=str(e)
            )

    def _save_redistribution(
        self,
        user_id: int,
        request: RedistributionRequest,
        supply_id: str,
        coefficient: Optional[float]
    ):
        """Сохраняет перераспределение в БД"""
        self.db.add_booking(
            user_id=user_id,
            warehouse_id=request.target_warehouse_id,
            warehouse_name=request.target_warehouse_name,
            coefficient=coefficient or 0,
            slot_date=date.today(),
            supply_id=supply_id,
            booking_type='redistribution',
            status='pending'
        )

    def get_warehouse_name(self, warehouse_id: int) -> str:
        """Получает название склада"""
        return WarehousesAPI.POPULAR_WAREHOUSES.get(
            warehouse_id, {}
        ).get('name', f'Склад {warehouse_id}')

    def format_redistribution_summary(
        self,
        request: RedistributionRequest,
        coefficient: Optional[float] = None
    ) -> str:
        """
        Форматирует сводку перераспределения.

        Args:
            request: Запрос
            coefficient: Коэффициент приёмки

        Returns:
            Отформатированная строка
        """
        source_name = request.source_warehouse_name or self.get_warehouse_name(request.source_warehouse_id)
        target_name = request.target_warehouse_name or self.get_warehouse_name(request.target_warehouse_id)

        text = f"""
📦 <b>Перераспределение остатков</b>

🏷 <b>Артикул:</b> <code>{request.sku}</code>
{f'📝 Товар: {request.product_name}' if request.product_name else ''}

📤 <b>Откуда:</b> {source_name}
📥 <b>Куда:</b> {target_name}
📊 <b>Количество:</b> {request.quantity} шт
"""

        if coefficient is not None:
            if coefficient == 0:
                coeff_text = "🆓 Бесплатно"
            elif coefficient <= 1:
                coeff_text = f"✅ {coefficient}"
            else:
                coeff_text = f"💰 {coefficient}"
            text += f"\n💰 <b>Коэффициент:</b> {coeff_text}"

        return text.strip()
