"""
Интеграция с YooKassa для приёма платежей.

Функционал:
- Создание платежей
- Проверка статуса
- Webhook для уведомлений
"""

import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from yookassa import Configuration, Payment
from yookassa.domain.response import PaymentResponse

from config import Config

logger = logging.getLogger(__name__)


class PaymentStatus(Enum):
    """Статусы платежа"""
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


@dataclass
class PaymentInfo:
    """Информация о платеже"""
    id: str
    status: PaymentStatus
    amount: float
    currency: str
    description: str
    confirmation_url: Optional[str] = None
    paid: bool = False


class YooKassaClient:
    """Клиент для работы с YooKassa"""

    def __init__(
        self,
        shop_id: str = None,
        secret_key: str = None,
        return_url: str = None
    ):
        """
        Инициализация клиента.

        Args:
            shop_id: ID магазина в YooKassa
            secret_key: Секретный ключ API
            return_url: URL для возврата после оплаты
        """
        self.shop_id = shop_id or Config.YOOKASSA_SHOP_ID if hasattr(Config, 'YOOKASSA_SHOP_ID') else None
        self.secret_key = secret_key or Config.YOOKASSA_SECRET_KEY if hasattr(Config, 'YOOKASSA_SECRET_KEY') else None
        self.return_url = return_url or Config.WEBAPP_URL if hasattr(Config, 'WEBAPP_URL') else "https://t.me"

        if self.shop_id and self.secret_key:
            Configuration.account_id = self.shop_id
            Configuration.secret_key = self.secret_key
            self._configured = True
            logger.info("YooKassa configured")
        else:
            self._configured = False
            logger.warning("YooKassa not configured - payments disabled")

    @property
    def is_configured(self) -> bool:
        """Проверка конфигурации"""
        return self._configured

    def create_payment(
        self,
        amount: float,
        user_id: int,
        description: str = None
    ) -> Optional[PaymentInfo]:
        """
        Создать платёж.

        Args:
            amount: Сумма в рублях
            user_id: Telegram user ID
            description: Описание платежа

        Returns:
            PaymentInfo или None если ошибка
        """
        if not self.is_configured:
            logger.error("YooKassa not configured")
            return None

        try:
            idempotence_key = str(uuid.uuid4())

            payment = Payment.create({
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": self.return_url
                },
                "capture": True,  # Автоматическое подтверждение
                "description": description or f"Пополнение баланса (user_id: {user_id})",
                "metadata": {
                    "user_id": str(user_id)
                }
            }, idempotence_key)

            logger.info(f"Payment created: {payment.id}")

            return PaymentInfo(
                id=payment.id,
                status=PaymentStatus(payment.status),
                amount=float(payment.amount.value),
                currency=payment.amount.currency,
                description=payment.description,
                confirmation_url=payment.confirmation.confirmation_url if payment.confirmation else None,
                paid=payment.paid
            )

        except Exception as e:
            logger.error(f"Failed to create payment: {e}")
            return None

    def get_payment(self, payment_id: str) -> Optional[PaymentInfo]:
        """
        Получить информацию о платеже.

        Args:
            payment_id: ID платежа в YooKassa

        Returns:
            PaymentInfo или None
        """
        if not self.is_configured:
            return None

        try:
            payment = Payment.find_one(payment_id)

            return PaymentInfo(
                id=payment.id,
                status=PaymentStatus(payment.status),
                amount=float(payment.amount.value),
                currency=payment.amount.currency,
                description=payment.description,
                paid=payment.paid
            )

        except Exception as e:
            logger.error(f"Failed to get payment {payment_id}: {e}")
            return None

    def check_payment_status(self, payment_id: str) -> Optional[PaymentStatus]:
        """
        Проверить статус платежа.

        Args:
            payment_id: ID платежа

        Returns:
            PaymentStatus или None
        """
        payment = self.get_payment(payment_id)
        if payment:
            return payment.status
        return None

    def cancel_payment(self, payment_id: str) -> bool:
        """
        Отменить платёж.

        Args:
            payment_id: ID платежа

        Returns:
            True если успешно
        """
        if not self.is_configured:
            return False

        try:
            Payment.cancel(payment_id)
            logger.info(f"Payment {payment_id} canceled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel payment {payment_id}: {e}")
            return False

    @staticmethod
    def parse_webhook(request_body: dict) -> Optional[PaymentInfo]:
        """
        Распарсить webhook от YooKassa.

        Args:
            request_body: Тело запроса (JSON)

        Returns:
            PaymentInfo или None
        """
        try:
            event = request_body.get('event')
            obj = request_body.get('object', {})

            if event not in ['payment.succeeded', 'payment.canceled', 'payment.waiting_for_capture']:
                logger.debug(f"Ignoring webhook event: {event}")
                return None

            return PaymentInfo(
                id=obj.get('id'),
                status=PaymentStatus(obj.get('status')),
                amount=float(obj.get('amount', {}).get('value', 0)),
                currency=obj.get('amount', {}).get('currency', 'RUB'),
                description=obj.get('description', ''),
                paid=obj.get('paid', False)
            )

        except Exception as e:
            logger.error(f"Failed to parse webhook: {e}")
            return None


# Singleton instance
_yookassa_client: Optional[YooKassaClient] = None


def get_yookassa_client() -> YooKassaClient:
    """Получить singleton instance YooKassaClient"""
    global _yookassa_client
    if _yookassa_client is None:
        _yookassa_client = YooKassaClient()
    return _yookassa_client
