"""
API роутер для платежей.

Endpoints:
- POST /payments/webhook - Webhook от YooKassa
- GET /payments/status/{payment_id} - Статус платежа
"""

import logging
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from payments.yookassa_client import YooKassaClient, PaymentStatus, get_yookassa_client
from payments.balance import get_balance_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


class WebhookResponse(BaseModel):
    """Ответ на webhook"""
    status: str
    message: str


@router.post("/webhook", response_model=WebhookResponse)
async def yookassa_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Webhook для уведомлений от YooKassa.

    YooKassa отправляет POST запросы при изменении статуса платежа:
    - payment.succeeded - платёж успешен
    - payment.canceled - платёж отменён
    - payment.waiting_for_capture - ожидает подтверждения

    Returns:
        200 OK если обработано успешно
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Received YooKassa webhook: {body.get('event')}")

    # Парсим webhook
    payment_info = YooKassaClient.parse_webhook(body)

    if not payment_info:
        # Неизвестный event - игнорируем, но возвращаем 200
        return WebhookResponse(status="ok", message="Event ignored")

    # Обрабатываем успешный платёж
    if payment_info.status == PaymentStatus.SUCCEEDED:
        balance_service = get_balance_service()

        # Обрабатываем в фоне чтобы быстрее ответить YooKassa
        background_tasks.add_task(
            balance_service.process_payment_webhook,
            payment_info.id
        )

        logger.info(f"Payment {payment_info.id} succeeded, processing...")
        return WebhookResponse(status="ok", message="Payment processed")

    elif payment_info.status == PaymentStatus.CANCELED:
        logger.info(f"Payment {payment_info.id} canceled")
        return WebhookResponse(status="ok", message="Payment canceled")

    else:
        logger.debug(f"Payment {payment_info.id} status: {payment_info.status}")
        return WebhookResponse(status="ok", message="Status noted")


@router.get("/status/{payment_id}")
async def get_payment_status(payment_id: str):
    """
    Проверить статус платежа.

    Args:
        payment_id: ID платежа в YooKassa

    Returns:
        Информация о платеже
    """
    yookassa = get_yookassa_client()

    if not yookassa.is_configured:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    payment = yookassa.get_payment(payment_id)

    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    return {
        "id": payment.id,
        "status": payment.status.value,
        "amount": payment.amount,
        "currency": payment.currency,
        "paid": payment.paid,
        "description": payment.description
    }


@router.post("/check/{payment_id}")
async def check_and_process_payment(payment_id: str):
    """
    Проверить и обработать платёж вручную.

    Используется если webhook не пришёл.

    Args:
        payment_id: ID платежа в YooKassa

    Returns:
        Результат обработки
    """
    balance_service = get_balance_service()

    result = balance_service.process_payment_webhook(payment_id)

    if result:
        return {"status": "ok", "message": "Payment processed successfully"}
    else:
        return {"status": "pending", "message": "Payment not ready or already processed"}
