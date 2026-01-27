"""
Сервис управления балансом пользователей.

Функционал:
- Пополнение баланса
- Списание за перемещения
- История операций
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from db_factory import get_database
from .yookassa_client import YooKassaClient, PaymentStatus, get_yookassa_client

logger = logging.getLogger(__name__)


# Цена за одно перемещение (в рублях)
REDISTRIBUTION_PRICE = 50.0


@dataclass
class BalanceInfo:
    """Информация о балансе"""
    user_id: int
    balance: float
    total_spent: float
    can_redistribute: bool  # Хватает ли на одно перемещение


class BalanceService:
    """Сервис управления балансом"""

    def __init__(self, redistribution_price: float = REDISTRIBUTION_PRICE):
        """
        Инициализация сервиса.

        Args:
            redistribution_price: Цена за одно перемещение
        """
        self.redistribution_price = redistribution_price
        self._yookassa = get_yookassa_client()

    def get_balance(self, user_id: int) -> BalanceInfo:
        """
        Получить информацию о балансе.

        Args:
            user_id: Telegram user ID

        Returns:
            BalanceInfo
        """
        db = get_database()
        info = db.get_balance_info(user_id)

        return BalanceInfo(
            user_id=user_id,
            balance=info.get('balance', 0),
            total_spent=info.get('total_spent', 0),
            can_redistribute=info.get('balance', 0) >= self.redistribution_price
        )

    def create_top_up_payment(
        self,
        user_id: int,
        amount: float
    ) -> Optional[str]:
        """
        Создать платёж для пополнения баланса.

        Args:
            user_id: Telegram user ID
            amount: Сумма пополнения

        Returns:
            URL для оплаты или None
        """
        if not self._yookassa.is_configured:
            logger.error("YooKassa not configured")
            return None

        if amount < 50:
            logger.warning(f"Amount too small: {amount}")
            return None

        # Создаём платёж в YooKassa
        payment = self._yookassa.create_payment(
            amount=amount,
            user_id=user_id,
            description=f"Пополнение баланса WB Bot на {amount:.0f}₽"
        )

        if not payment:
            return None

        # Сохраняем информацию о платеже в БД
        db = get_database()
        db.add_payment(
            user_id=user_id,
            amount=amount,
            payment_type='top_up',
            payment_id=payment.id,
            description=payment.description
        )

        logger.info(f"Payment created for user {user_id}: {payment.id}, amount: {amount}")
        return payment.confirmation_url

    def process_payment_webhook(self, payment_id: str) -> bool:
        """
        Обработать webhook от YooKassa.

        Args:
            payment_id: ID платежа

        Returns:
            True если баланс пополнен
        """
        # Проверяем статус платежа
        payment = self._yookassa.get_payment(payment_id)
        if not payment:
            logger.error(f"Payment {payment_id} not found")
            return False

        if payment.status != PaymentStatus.SUCCEEDED:
            logger.debug(f"Payment {payment_id} not succeeded: {payment.status}")
            return False

        # Находим платёж в БД
        db = get_database()
        db_payment = db.get_payment_by_external_id(payment_id)

        if not db_payment:
            logger.error(f"Payment {payment_id} not found in DB")
            return False

        if db_payment.get('status') == 'completed':
            logger.debug(f"Payment {payment_id} already processed")
            return True

        # Пополняем баланс
        user_id = db_payment.get('user_id')
        amount = db_payment.get('amount')

        new_balance = db.update_user_balance(user_id, amount, 'add')

        # Обновляем статус платежа
        db.update_payment_status(
            db_payment.get('id'),
            status='completed',
            completed_at=datetime.now()
        )

        logger.info(f"Balance topped up for user {user_id}: +{amount}₽, new balance: {new_balance}₽")
        return True

    def charge_for_redistribution(self, user_id: int) -> bool:
        """
        Списать деньги за перемещение.

        Args:
            user_id: Telegram user ID

        Returns:
            True если успешно списано
        """
        db = get_database()
        current_balance = db.get_user_balance(user_id)

        if current_balance < self.redistribution_price:
            logger.warning(f"Insufficient balance for user {user_id}: {current_balance}₽")
            return False

        # Списываем
        new_balance = db.update_user_balance(user_id, self.redistribution_price, 'subtract')

        # Записываем в историю
        db.add_payment(
            user_id=user_id,
            amount=-self.redistribution_price,
            payment_type='redistribution',
            description='Списание за перемещение',
        )
        db.update_payment_status(
            db.get_payments(user_id, limit=1)[0]['id'],
            status='completed',
            completed_at=datetime.now()
        )

        logger.info(f"Charged {self.redistribution_price}₽ from user {user_id}, new balance: {new_balance}₽")
        return True

    def refund_redistribution(self, user_id: int) -> float:
        """
        Вернуть деньги за неудачное перемещение.

        Args:
            user_id: Telegram user ID

        Returns:
            Новый баланс
        """
        db = get_database()
        new_balance = db.update_user_balance(user_id, self.redistribution_price, 'add')

        # Записываем в историю
        db.add_payment(
            user_id=user_id,
            amount=self.redistribution_price,
            payment_type='refund',
            description='Возврат за неудачное перемещение',
        )
        db.update_payment_status(
            db.get_payments(user_id, limit=1)[0]['id'],
            status='completed',
            completed_at=datetime.now()
        )

        logger.info(f"Refunded {self.redistribution_price}₽ to user {user_id}, new balance: {new_balance}₽")
        return new_balance

    def get_history(self, user_id: int, limit: int = 20) -> list:
        """
        Получить историю операций.

        Args:
            user_id: Telegram user ID
            limit: Максимум записей

        Returns:
            Список операций
        """
        db = get_database()
        return db.get_payments(user_id, limit=limit)


# Singleton instance
_balance_service: Optional[BalanceService] = None


def get_balance_service() -> BalanceService:
    """Получить singleton instance BalanceService"""
    global _balance_service
    if _balance_service is None:
        _balance_service = BalanceService()
    return _balance_service
