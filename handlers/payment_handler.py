"""
Handlers для оплаты и управления балансом.

Команды:
- /balance - проверить баланс
- /pay - пополнить баланс
- /history - история операций
"""

import logging

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from db_factory import get_database
from payments.balance import get_balance_service, REDISTRIBUTION_PRICE
from payments.yookassa_client import get_yookassa_client
from config import Config

logger = logging.getLogger(__name__)
router = Router(name="payment")


def get_db():
    """Получает экземпляр БД"""
    return get_database()


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    """Показать баланс пользователя"""
    user_id = message.from_user.id
    balance_service = get_balance_service()
    info = balance_service.get_balance(user_id)

    price = getattr(Config, 'REDISTRIBUTION_PRICE', REDISTRIBUTION_PRICE)
    possible_redistributions = int(info.balance // price)

    text = (
        f"<b>Ваш баланс</b>\n\n"
        f"💰 Баланс: <b>{info.balance:.0f}₽</b>\n"
        f"📊 Потрачено всего: {info.total_spent:.0f}₽\n\n"
        f"💸 Цена за перемещение: {price:.0f}₽\n"
        f"📦 Доступно перемещений: <b>{possible_redistributions}</b>\n\n"
    )

    if info.can_redistribute:
        text += "✅ Вы можете создавать заявки на перемещение"
    else:
        text += f"⚠️ Недостаточно средств. Пополните баланс командой /pay"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Пополнить 500₽", callback_data="pay:500"),
            InlineKeyboardButton(text="💳 Пополнить 1000₽", callback_data="pay:1000"),
        ],
        [
            InlineKeyboardButton(text="💳 Пополнить 2000₽", callback_data="pay:2000"),
            InlineKeyboardButton(text="💳 Пополнить 5000₽", callback_data="pay:5000"),
        ]
    ])

    await message.answer(text, reply_markup=keyboard)


@router.message(Command("pay"))
async def cmd_pay(message: Message):
    """Пополнить баланс"""
    user_id = message.from_user.id

    # Проверяем аргумент
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "<b>Пополнение баланса</b>\n\n"
            "Укажите сумму:\n"
            "/pay 500 - пополнить на 500₽\n"
            "/pay 1000 - пополнить на 1000₽\n\n"
            "Минимальная сумма: 50₽",
        )
        return

    try:
        amount = float(args[1])
    except ValueError:
        await message.answer("Укажите сумму числом: /pay 500")
        return

    if amount < 50:
        await message.answer("Минимальная сумма пополнения: 50₽")
        return

    if amount > 100000:
        await message.answer("Максимальная сумма пополнения: 100 000₽")
        return

    # Проверяем, настроена ли YooKassa
    yookassa = get_yookassa_client()
    if not yookassa.is_configured:
        await message.answer(
            "⚠️ Платёжная система временно недоступна.\n\n"
            "Обратитесь к администратору."
        )
        return

    await message.answer(f"⏳ Создаю платёж на {amount:.0f}₽...")

    # Создаём платёж
    balance_service = get_balance_service()
    payment_url = balance_service.create_top_up_payment(user_id, amount)

    if payment_url:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=payment_url)]
        ])
        await message.answer(
            f"<b>Платёж создан</b>\n\n"
            f"💰 Сумма: {amount:.0f}₽\n\n"
            f"Нажмите кнопку для оплаты.\n"
            f"После оплаты баланс пополнится автоматически.",
            reply_markup=keyboard
        )
    else:
        await message.answer(
            "❌ Не удалось создать платёж.\n\n"
            "Попробуйте позже или обратитесь к администратору."
        )


@router.message(Command("history"))
async def cmd_history(message: Message):
    """История операций"""
    user_id = message.from_user.id
    balance_service = get_balance_service()
    history = balance_service.get_history(user_id, limit=10)

    if not history:
        await message.answer(
            "У вас пока нет операций.\n\n"
            "Пополните баланс: /pay"
        )
        return

    text = "<b>История операций</b>\n\n"

    for item in history:
        amount = item.get('amount', 0)
        ptype = item.get('payment_type', '')
        status = item.get('status', '')
        created = item.get('created_at', '')[:16] if item.get('created_at') else 'N/A'

        # Определяем тип операции
        if ptype == 'top_up':
            emoji = "➕" if status == 'completed' else "⏳"
            label = "Пополнение"
        elif ptype == 'redistribution':
            emoji = "➖"
            label = "Перемещение"
        elif ptype == 'refund':
            emoji = "↩️"
            label = "Возврат"
        else:
            emoji = "•"
            label = ptype

        amount_str = f"+{amount:.0f}₽" if amount > 0 else f"{amount:.0f}₽"
        text += f"{emoji} {label}: <b>{amount_str}</b> ({created})\n"

    text += f"\nПоказаны последние {len(history)} операций"

    await message.answer(text)


# Callback для быстрых кнопок пополнения
from aiogram import F
from aiogram.types import CallbackQuery


@router.callback_query(F.data.startswith("pay:"))
async def callback_pay(callback: CallbackQuery):
    """Обработка кнопок быстрого пополнения"""
    user_id = callback.from_user.id

    try:
        amount = float(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка", show_alert=True)
        return

    # Проверяем YooKassa
    yookassa = get_yookassa_client()
    if not yookassa.is_configured:
        await callback.answer("Платёжная система недоступна", show_alert=True)
        return

    await callback.answer("Создаю платёж...")

    # Создаём платёж
    balance_service = get_balance_service()
    payment_url = balance_service.create_top_up_payment(user_id, amount)

    if payment_url:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=payment_url)]
        ])
        await callback.message.edit_text(
            f"<b>Платёж создан</b>\n\n"
            f"💰 Сумма: {amount:.0f}₽\n\n"
            f"Нажмите кнопку для оплаты.",
            reply_markup=keyboard
        )
    else:
        await callback.message.edit_text(
            "❌ Не удалось создать платёж.\n\n"
            "Попробуйте позже."
        )
