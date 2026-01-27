"""
Сервис уведомлений для Telegram.

ОТКЛЮЧЕН! Мониторинг коэффициентов отключен.
"""

raise RuntimeError(
    "❌ NOTIFICATION SERVICE ОТКЛЮЧЕН!\n"
    "Мониторинг коэффициентов отключен, уведомления не используются.\n"
    "Если вы видите эту ошибку - обновите код"
)

import asyncio
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from wb_api.coefficients import CoefficientChange, Coefficient
from wb_api.supplies import BookingResult

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Сервис отправки уведомлений в Telegram.

    Использование:
        service = NotificationService(bot)
        await service.notify_coefficient_change(user_id, change)
    """

    def __init__(self, bot: Bot, cooldown_minutes: int = 10):
        """
        Args:
            bot: Экземпляр Telegram бота
            cooldown_minutes: Минимальный интервал между уведомлениями для одного склада (минуты)
        """
        self.bot = bot
        self.cooldown_minutes = cooldown_minutes

        # Кэш последних уведомлений: {user_id: {warehouse_id: datetime}}
        self._last_notifications: Dict[int, Dict[int, datetime]] = {}

    def _can_notify(self, user_id: int, warehouse_id: int) -> bool:
        """
        Проверяет можно ли отправить уведомление (cooldown).

        Args:
            user_id: ID пользователя
            warehouse_id: ID склада

        Returns:
            True если можно отправить, False если нужно подождать
        """
        now = datetime.now()

        # Проверяем есть ли запись о предыдущем уведомлении
        if user_id not in self._last_notifications:
            self._last_notifications[user_id] = {}

        user_notifications = self._last_notifications[user_id]

        if warehouse_id in user_notifications:
            last_time = user_notifications[warehouse_id]
            time_passed = (now - last_time).total_seconds() / 60  # в минутах

            if time_passed < self.cooldown_minutes:
                logger.debug(
                    f"Cooldown active for user {user_id}, warehouse {warehouse_id}. "
                    f"Time passed: {time_passed:.1f} min, need: {self.cooldown_minutes} min"
                )
                return False

        # Обновляем время последнего уведомления
        user_notifications[warehouse_id] = now
        return True

    async def notify_coefficient_change(
        self,
        user_id: int,
        change: CoefficientChange,
        can_auto_book: bool = False
    ):
        """
        Уведомляет пользователя об изменении коэффициента.

        Args:
            user_id: Telegram ID пользователя
            change: Информация об изменении
            can_auto_book: Показывать ли кнопку автобронирования
        """
        # Проверяем cooldown
        if not self._can_notify(user_id, change.warehouse_id):
            logger.debug(f"Skipping notification for user {user_id} due to cooldown")
            return
        # Определяем эмодзи по типу изменения
        if change.new_coefficient == 0:
            emoji = "🆓"
            alert = "БЕСПЛАТНАЯ ПРИЁМКА!"
        elif change.new_coefficient < change.old_coefficient:
            emoji = "📉"
            alert = "Снижение коэффициента"
        elif change.old_coefficient < 0 and change.new_coefficient >= 0:
            emoji = "✅"
            alert = "Приёмка доступна"
        else:
            emoji = "📈"
            alert = "Изменение коэффициента"

        # Форматируем сообщение
        message = f"""
{emoji} <b>{alert}</b>

📍 <b>Склад:</b> {change.warehouse_name}
📅 <b>Дата:</b> {change.date.strftime('%d.%m.%Y')}

💰 <b>Коэффициент:</b> {change.old_coefficient} → <b>{change.new_coefficient}</b>

⏰ <i>Обнаружено: {change.detected_at.strftime('%H:%M:%S')}</i>
"""

        # Создаём клавиатуру
        buttons = []

        # Кнопка бронирования
        buttons.append([
            InlineKeyboardButton(
                text="🚀 Забронировать",
                callback_data=f"book:{change.warehouse_id}:{change.date.isoformat()}:{change.new_coefficient}"
            )
        ])

        # Кнопка "Подробнее" и "Игнорировать"
        buttons.append([
            InlineKeyboardButton(
                text="📋 Все коэффициенты",
                callback_data=f"coefficients:{change.warehouse_id}"
            ),
            InlineKeyboardButton(
                text="🔕 Скрыть",
                callback_data="dismiss"
            )
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML',
                reply_markup=keyboard
            )
            logger.debug(f"Sent coefficient change notification to user {user_id}")

        except Exception as e:
            logger.error(f"Failed to send notification to user {user_id}: {e}")

    async def notify_booking_result(
        self,
        user_id: int,
        result: BookingResult,
        warehouse_name: str = None
    ):
        """
        Уведомляет о результате бронирования.

        Args:
            user_id: Telegram ID пользователя
            result: Результат бронирования
            warehouse_name: Название склада
        """
        if result.success:
            message = f"""
✅ <b>Бронирование успешно!</b>

📍 <b>Склад:</b> {warehouse_name or result.warehouse_id}
📅 <b>Дата:</b> {result.date.strftime('%d.%m.%Y') if result.date else 'N/A'}
💰 <b>Коэффициент:</b> {result.coefficient or 'N/A'}
🆔 <b>ID поставки:</b> <code>{result.supply_id}</code>

⚠️ <i>Подтвердите поставку в ЛК Wildberries в течение 24 часов!</i>
"""
            buttons = [[
                InlineKeyboardButton(
                    text="📋 Открыть ЛК WB",
                    url="https://seller.wildberries.ru/supplies-management/all-supplies"
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"cancel_booking:{result.supply_id}"
                )
            ]]
        else:
            message = f"""
❌ <b>Ошибка бронирования</b>

📍 <b>Склад:</b> {warehouse_name or result.warehouse_id}

<b>Причина:</b> {result.error_message or 'Неизвестная ошибка'}

<i>Попробуйте забронировать вручную через ЛК WB</i>
"""
            buttons = [[
                InlineKeyboardButton(
                    text="🔄 Попробовать снова",
                    callback_data=f"retry_book:{result.warehouse_id}"
                )
            ]]

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML',
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Failed to send booking result to user {user_id}: {e}")

    async def notify_auto_booking(
        self,
        user_id: int,
        result: BookingResult,
        warehouse_name: str,
        coefficient: float
    ):
        """
        Уведомляет об автобронировании.

        Args:
            user_id: Telegram ID пользователя
            result: Результат бронирования
            warehouse_name: Название склада
            coefficient: Коэффициент
        """
        if result.success:
            message = f"""
🤖 <b>АВТОБРОНИРОВАНИЕ ВЫПОЛНЕНО!</b>

📍 <b>Склад:</b> {warehouse_name}
📅 <b>Дата:</b> {result.date.strftime('%d.%m.%Y') if result.date else 'N/A'}
💰 <b>Коэффициент:</b> {coefficient}
🆔 <b>ID поставки:</b> <code>{result.supply_id}</code>

⚠️ <b>Подтвердите поставку в ЛК Wildberries в течение 24 часов!</b>
"""
            buttons = [[
                InlineKeyboardButton(
                    text="📋 Открыть ЛК WB",
                    url="https://seller.wildberries.ru/supplies-management/all-supplies"
                ),
                InlineKeyboardButton(
                    text="❌ Отменить бронь",
                    callback_data=f"cancel_booking:{result.supply_id}"
                )
            ]]
        else:
            message = f"""
🤖 <b>Автобронирование не удалось</b>

📍 <b>Склад:</b> {warehouse_name}
💰 <b>Коэффициент:</b> {coefficient}

<b>Причина:</b> {result.error_message or 'Неизвестная ошибка'}

<i>Попробуйте забронировать вручную</i>
"""
            buttons = [[
                InlineKeyboardButton(
                    text="🚀 Забронировать вручную",
                    callback_data=f"book:{result.warehouse_id}:{result.date}:{coefficient}"
                )
            ]]

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML',
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Failed to send auto-booking notification to user {user_id}: {e}")

    async def send_coefficients_summary(
        self,
        user_id: int,
        coefficients: List[Coefficient],
        warehouse_filter: int = None
    ):
        """
        Отправляет сводку по коэффициентам.

        Args:
            user_id: Telegram ID пользователя
            coefficients: Список коэффициентов
            warehouse_filter: Фильтр по складу
        """
        if warehouse_filter:
            coefficients = [c for c in coefficients if c.warehouse_id == warehouse_filter]

        if not coefficients:
            await self.bot.send_message(
                chat_id=user_id,
                text="📊 Нет данных о коэффициентах для выбранных складов."
            )
            return

        # Группируем по складам
        by_warehouse: Dict[str, List[Coefficient]] = {}
        for c in coefficients:
            name = c.warehouse_name or str(c.warehouse_id)
            if name not in by_warehouse:
                by_warehouse[name] = []
            by_warehouse[name].append(c)

        # Формируем сообщение
        lines = ["📊 <b>Текущие коэффициенты приёмки</b>\n"]

        for wh_name, wh_coeffs in sorted(by_warehouse.items()):
            lines.append(f"\n📍 <b>{wh_name}</b>")

            # Сортируем по дате и берём первые 5
            sorted_coeffs = sorted(wh_coeffs, key=lambda x: x.date)[:5]

            for c in sorted_coeffs:
                # Эмодзи по коэффициенту
                if c.coefficient == 0:
                    emoji = "🆓"
                elif c.coefficient <= 1:
                    emoji = "✅"
                elif c.coefficient < 0:
                    emoji = "❌"
                else:
                    emoji = "💰"

                lines.append(
                    f"  {emoji} {c.date.strftime('%d.%m')}: <b>{c.coefficient}</b>"
                )

        lines.append(f"\n<i>Обновлено: {datetime.now().strftime('%H:%M:%S')}</i>")

        message = "\n".join(lines)

        # Кнопка обновления
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data="refresh_coefficients"
            )
        ]])

        try:
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML',
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Failed to send coefficients summary to user {user_id}: {e}")

    async def broadcast_to_subscribers(
        self,
        subscriptions: List[Dict],
        change: CoefficientChange
    ):
        """
        Рассылает уведомление всем подписчикам.

        Args:
            subscriptions: Список подписок
            change: Изменение коэффициента
        """
        tasks = []
        for sub in subscriptions:
            tasks.append(
                self.notify_coefficient_change(
                    user_id=sub['user_id'],
                    change=change,
                    can_auto_book=sub.get('auto_book', False)
                )
            )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success = sum(1 for r in results if not isinstance(r, Exception))
            logger.info(
                f"Broadcast sent: {success}/{len(tasks)} successful "
                f"for warehouse {change.warehouse_name}"
            )
