"""
Сервис бронирования слотов на складах WB.

Реализует:
- Ручное бронирование слотов
- Автобронирование при появлении выгодных коэффициентов
- Отмену бронирований
"""

import asyncio
import logging
from datetime import date
from typing import Optional, Dict, List
from dataclasses import dataclass

from config import Config
from database import Database
from wb_api.client import WBApiClient
from wb_api.supplies import SuppliesAPI, BookingResult, CargoType
from wb_api.coefficients import Coefficient

logger = logging.getLogger(__name__)


@dataclass
class AutoBookingConfig:
    """Конфигурация автобронирования для пользователя"""
    user_id: int
    token_id: int
    api_token: str
    max_coefficient: float = 1.0
    warehouse_ids: List[int] = None
    daily_limit: int = 5
    bookings_today: int = 0


class SlotBookingService:
    """
    Сервис бронирования слотов.

    Использование:
        service = SlotBookingService(db)

        # Ручное бронирование
        result = await service.book_slot(
            user_id=123,
            token_id=1,
            warehouse_id=117501,
            date=date(2026, 1, 25),
            coefficient=0.5
        )

        # Автобронирование
        result = await service.auto_book(
            config=AutoBookingConfig(...),
            coefficient=Coefficient(...)
        )
    """

    def __init__(self, db: Database):
        self.db = db

    async def book_slot(
        self,
        user_id: int,
        token_id: int,
        warehouse_id: int,
        slot_date: date,
        coefficient: float,
        cargo_type: CargoType = CargoType.BOX,
        name: str = None
    ) -> BookingResult:
        """
        Бронирует слот на склад.

        Args:
            user_id: ID пользователя
            token_id: ID WB токена
            warehouse_id: ID склада
            slot_date: Дата поставки
            coefficient: Текущий коэффициент
            cargo_type: Тип груза
            name: Название поставки

        Returns:
            BookingResult с результатом
        """
        # Получаем токен пользователя
        token_data = self.db.get_wb_token(user_id, token_id)
        if not token_data:
            return BookingResult(
                success=False,
                error_message="WB API токен не найден"
            )

        api_token = token_data['token']

        # Получаем название склада
        warehouse_name = self.db.get_warehouse_name(warehouse_id) or str(warehouse_id)

        # Создаём название поставки если не указано
        if name is None:
            name = f"Supply_{slot_date.isoformat()}_{warehouse_name}"

        # Создаём запись о бронировании
        booking_id = self.db.add_booking(
            user_id=user_id,
            token_id=token_id,
            warehouse_id=warehouse_id,
            warehouse_name=warehouse_name,
            coefficient=coefficient,
            slot_date=slot_date,
            booking_type='manual',
            status='pending'
        )

        try:
            # Бронируем через WB API
            async with WBApiClient(api_token) as client:
                api = SuppliesAPI(client)
                result = await api.book_slot(
                    warehouse_id=warehouse_id,
                    date=slot_date,
                    name=name,
                    cargo_type=cargo_type
                )

            # Обновляем статус
            if result.success:
                self.db.update_booking_status(
                    booking_id=booking_id,
                    status='confirmed',
                    supply_id=result.supply_id
                )
                logger.info(
                    f"Booked slot for user {user_id}: "
                    f"{warehouse_name} on {slot_date} (coeff: {coefficient})"
                )
            else:
                self.db.update_booking_status(
                    booking_id=booking_id,
                    status='failed',
                    error_message=result.error_message
                )

            # Обновляем время использования токена
            self.db.update_token_last_used(token_id)

            return result

        except Exception as e:
            logger.error(f"Booking failed: {e}")
            self.db.update_booking_status(
                booking_id=booking_id,
                status='failed',
                error_message=str(e)
            )
            return BookingResult(
                success=False,
                error_message=str(e)
            )

    async def auto_book(
        self,
        config: AutoBookingConfig,
        coefficient: Coefficient
    ) -> Optional[BookingResult]:
        """
        Автобронирование при появлении выгодного слота.

        Args:
            config: Конфигурация автобронирования
            coefficient: Коэффициент для бронирования

        Returns:
            BookingResult или None если бронирование не требуется
        """
        # Проверяем лимит
        if config.bookings_today >= config.daily_limit:
            logger.info(
                f"Auto-booking skipped for user {config.user_id}: "
                f"daily limit reached ({config.daily_limit})"
            )
            return None

        # Проверяем коэффициент
        if coefficient.coefficient > config.max_coefficient:
            logger.debug(
                f"Auto-booking skipped: coefficient {coefficient.coefficient} > "
                f"max {config.max_coefficient}"
            )
            return None

        # Проверяем склад
        if config.warehouse_ids and coefficient.warehouse_id not in config.warehouse_ids:
            logger.debug(
                f"Auto-booking skipped: warehouse {coefficient.warehouse_id} "
                f"not in config"
            )
            return None

        logger.info(
            f"Auto-booking for user {config.user_id}: "
            f"{coefficient.warehouse_name} on {coefficient.date} "
            f"(coeff: {coefficient.coefficient})"
        )

        # Бронируем
        result = await self.book_slot(
            user_id=config.user_id,
            token_id=config.token_id,
            warehouse_id=coefficient.warehouse_id,
            slot_date=coefficient.date,
            coefficient=coefficient.coefficient
        )

        # Добавляем user_id в результат для уведомлений
        result.user_id = config.user_id

        if result.success:
            # Обновляем тип бронирования на 'auto'
            self.db.update_booking_type(result.supply_id, 'auto')

        return result

    async def auto_book_for_subscriptions(
        self,
        subscriptions: List[Dict],
        coefficient: Coefficient
    ) -> List[BookingResult]:
        """
        Автобронирование для списка подписок.

        Args:
            subscriptions: Список подписок из БД
            coefficient: Коэффициент для бронирования

        Returns:
            Список результатов бронирования
        """
        results = []

        tasks = []
        for sub in subscriptions:
            if not sub.get('auto_book'):
                continue

            config = AutoBookingConfig(
                user_id=sub['user_id'],
                token_id=sub['token_id'],
                api_token=sub['api_token'],
                max_coefficient=max(sub.get('target_coefficients', [1.0])),
                warehouse_ids=sub.get('warehouse_ids'),
                daily_limit=Config.AUTO_BOOK_DAILY_LIMIT
            )

            tasks.append(self.auto_book(config, coefficient))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Фильтруем None и исключения
            results = [
                r for r in results
                if r is not None and not isinstance(r, Exception)
            ]

        return results

    async def cancel_booking(
        self,
        user_id: int,
        booking_id: int
    ) -> bool:
        """
        Отменяет бронирование.

        Args:
            user_id: ID пользователя
            booking_id: ID бронирования

        Returns:
            True если успешно
        """
        # Получаем бронирование
        bookings = self.db.get_user_bookings(user_id, limit=100)
        booking = next((b for b in bookings if b['id'] == booking_id), None)

        if not booking:
            logger.warning(f"Booking {booking_id} not found for user {user_id}")
            return False

        if not booking.get('supply_id'):
            # Нет supply_id - просто помечаем как отменённое
            self.db.update_booking_status(booking_id, 'cancelled')
            return True

        # Получаем токен
        token_data = self.db.get_wb_token(user_id)
        if not token_data:
            return False

        try:
            async with WBApiClient(token_data['token']) as client:
                api = SuppliesAPI(client)
                success = await api.cancel_supply(booking['supply_id'])

            if success:
                self.db.update_booking_status(booking_id, 'cancelled')

            return success

        except Exception as e:
            logger.error(f"Failed to cancel booking {booking_id}: {e}")
            return False

    def get_user_bookings(
        self,
        user_id: int,
        limit: int = 20,
        status: str = None
    ) -> List[Dict]:
        """Получает историю бронирований пользователя"""
        return self.db.get_user_bookings(user_id, limit, status)
