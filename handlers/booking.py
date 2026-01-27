"""
Handlers для бронирования слотов.

Команды:
- /book - ручное бронирование
- /history - история бронирований
- Callback handlers для кнопок бронирования
"""

import logging
from datetime import date, datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import Database
from config import Config
from services.slot_booking import SlotBookingService

logger = logging.getLogger(__name__)

router = Router(name="booking")


def get_db() -> Database:
    """Получает экземпляр БД"""
    return Database(Config.DATABASE_PATH)


@router.message(Command("book"))
async def cmd_book(message: Message):
    """Бронирование: /book или /book <warehouse_id> <date>"""
    # Проверяем есть ли аргументы
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []

    # Если нет аргументов - показываем инструкцию
    if not args:
        text = """
🚀 <b>Бронирование слота</b>

Для бронирования слота:

1. Используйте команду /coefficients чтобы увидеть доступные слоты
2. Нажмите кнопку «Забронировать» у нужного слота

Или настройте автобронирование через /monitor,
чтобы бот сам бронировал выгодные слоты.

<b>Альтернативный способ:</b>
Отправьте ID склада и дату в формате:
<code>/book 117501 2026-01-25</code>
"""
        await message.answer(text, parse_mode='HTML')
        return
    db = get_db()
    user_id = message.from_user.id

    # Получаем поставщиков пользователя
    suppliers = db.get_user_suppliers(user_id)

    if not suppliers:
        await message.answer(
            "⚠️ Для бронирования необходим WB API токен.\n\n"
            "Добавьте токен командой /token"
        )
        return

    # Если один поставщик - используем его автоматически
    if len(suppliers) == 1:
        supplier = suppliers[0]
        # Продолжаем с текущей логикой, используя токен поставщика
        token = db.get_wb_token_by_id(supplier['token_id'])
        if not token or not token['is_active']:
            await message.answer(
                f"⚠️ Токен поставщика <b>{supplier['name']}</b> недоступен.\n\n"
                "Проверьте токен командой /token",
                parse_mode='HTML'
            )
            return
    else:
        # Несколько поставщиков - показываем выбор
        # Для простоты используем первый токен по умолчанию
        # В полной реализации можно добавить выбор через callback
        default_supplier = next((s for s in suppliers if s['is_default']), suppliers[0])
        token = db.get_wb_token_by_id(default_supplier['token_id'])
        if not token or not token['is_active']:
            await message.answer(
                "⚠️ Токен недоступен.\n\n"
                "Проверьте токен командой /token"
            )
            return

    # Парсим аргументы
    args = message.text.split()[1:]  # Убираем /book

    if len(args) < 2:
        await message.answer(
            "❌ Неверный формат.\n\n"
            "Используйте: <code>/book &lt;ID_склада&gt; &lt;дата&gt;</code>\n"
            "Пример: <code>/book 117501 2026-01-25</code>",
            parse_mode='HTML'
        )
        return

    try:
        warehouse_id = int(args[0])
        slot_date = date.fromisoformat(args[1])
    except (ValueError, IndexError):
        await message.answer(
            "❌ Неверный формат.\n\n"
            "ID склада должен быть числом.\n"
            "Дата в формате YYYY-MM-DD (например: 2026-01-25)",
            parse_mode='HTML'
        )
        return

    # Получаем название склада
    warehouse_name = db.get_warehouse_name(warehouse_id) or str(warehouse_id)

    # Подтверждение
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=f"confirm_book:{warehouse_id}:{slot_date.isoformat()}:manual"
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel_book"
            )
        ]
    ])

    await message.answer(
        f"🚀 <b>Подтверждение бронирования</b>\n\n"
        f"📍 Склад: {warehouse_name} (ID: {warehouse_id})\n"
        f"📅 Дата: {slot_date.strftime('%d.%m.%Y')}\n\n"
        f"Подтвердите бронирование:",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("book:"))
async def callback_book(callback: CallbackQuery):
    """Бронирование из уведомления о коэффициенте"""
    await callback.answer()

    # Парсим данные: book:warehouse_id:date:coefficient
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.message.answer("❌ Ошибка: неверный формат данных")
        return

    warehouse_id = int(parts[1])
    slot_date = date.fromisoformat(parts[2])
    coefficient = float(parts[3])

    db = get_db()
    user_id = callback.from_user.id

    # Проверяем наличие поставщиков
    suppliers = db.get_user_suppliers(user_id)
    if not suppliers:
        await callback.message.answer(
            "❌ Токен не найден. Добавьте токен командой /token"
        )
        return

    warehouse_name = db.get_warehouse_name(warehouse_id) or str(warehouse_id)

    # Подтверждение
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Забронировать",
                callback_data=f"confirm_book:{warehouse_id}:{slot_date.isoformat()}:{coefficient}"
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel_book"
            )
        ]
    ])

    await callback.message.edit_text(
        f"🚀 <b>Подтверждение бронирования</b>\n\n"
        f"📍 Склад: {warehouse_name}\n"
        f"📅 Дата: {slot_date.strftime('%d.%m.%Y')}\n"
        f"💰 Коэффициент: {coefficient}\n\n"
        f"Подтвердите бронирование:",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("confirm_book:"))
async def callback_confirm_book(callback: CallbackQuery):
    """Подтверждение и выполнение бронирования"""
    await callback.answer("Бронирую...")

    # Парсим данные
    parts = callback.data.split(":")
    warehouse_id = int(parts[1])
    slot_date = date.fromisoformat(parts[2])
    coefficient = float(parts[3]) if len(parts) > 3 and parts[3] != 'manual' else 0

    db = get_db()
    user_id = callback.from_user.id

    # Получаем поставщиков и токен
    suppliers = db.get_user_suppliers(user_id)
    if not suppliers:
        await callback.message.edit_text(
            "❌ Токен не найден. Добавьте токен командой /token"
        )
        return

    # Используем токен первого поставщика (или дефолтного)
    default_supplier = next((s for s in suppliers if s['is_default']), suppliers[0])
    token = db.get_wb_token_by_id(default_supplier['token_id'])
    if not token or not token['is_active']:
        await callback.message.edit_text(
            "❌ Токен недоступен. Проверьте токен командой /token"
        )
        return

    warehouse_name = db.get_warehouse_name(warehouse_id) or str(warehouse_id)

    # Показываем статус
    await callback.message.edit_text(
        f"🔄 <b>Бронирую слот...</b>\n\n"
        f"📍 Склад: {warehouse_name}\n"
        f"📅 Дата: {slot_date.strftime('%d.%m.%Y')}",
        parse_mode='HTML'
    )

    # Выполняем бронирование
    service = SlotBookingService(db)
    result = await service.book_slot(
        user_id=user_id,
        token_id=token['id'],
        warehouse_id=warehouse_id,
        slot_date=slot_date,
        coefficient=coefficient
    )

    if result.success:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="📋 Открыть ЛК WB",
                url="https://seller.wildberries.ru/supplies-management/all-supplies"
            )],
            [InlineKeyboardButton(
                text="❌ Отменить бронь",
                callback_data=f"cancel_booking:{result.supply_id}"
            )]
        ])

        await callback.message.edit_text(
            f"✅ <b>Бронирование успешно!</b>\n\n"
            f"📍 Склад: {warehouse_name}\n"
            f"📅 Дата: {slot_date.strftime('%d.%m.%Y')}\n"
            f"🆔 ID поставки: <code>{result.supply_id}</code>\n\n"
            f"⚠️ <i>Подтвердите поставку в ЛК WB в течение 24 часов!</i>",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    else:
        await callback.message.edit_text(
            f"❌ <b>Ошибка бронирования</b>\n\n"
            f"📍 Склад: {warehouse_name}\n"
            f"📅 Дата: {slot_date.strftime('%d.%m.%Y')}\n\n"
            f"<b>Причина:</b> {result.error_message}\n\n"
            f"<i>Попробуйте забронировать вручную в ЛК WB</i>",
            parse_mode='HTML'
        )


@router.callback_query(F.data == "cancel_book")
async def callback_cancel_book(callback: CallbackQuery):
    """Отмена бронирования (до выполнения)"""
    await callback.answer("Отменено")
    await callback.message.edit_text(
        "❌ Бронирование отменено.\n\n"
        "Используйте /coefficients чтобы увидеть доступные слоты."
    )


@router.callback_query(F.data.startswith("cancel_booking:"))
async def callback_cancel_existing_booking(callback: CallbackQuery):
    """Отмена существующего бронирования"""
    await callback.answer("Отменяю...")

    supply_id = callback.data.split(":")[1]

    db = get_db()
    user_id = callback.from_user.id

    # Получаем бронирования пользователя и ищем по supply_id
    bookings = db.get_user_bookings(user_id, limit=100)
    booking = next((b for b in bookings if b.get('supply_id') == supply_id), None)

    if not booking:
        await callback.message.answer(
            "❌ Бронирование не найдено или уже отменено."
        )
        return

    service = SlotBookingService(db)
    success = await service.cancel_booking(user_id, booking['id'])

    if success:
        await callback.message.edit_text(
            f"✅ Бронирование отменено.\n\n"
            f"ID поставки: {supply_id}"
        )
    else:
        await callback.message.edit_text(
            f"❌ Не удалось отменить бронирование.\n\n"
            f"Попробуйте отменить вручную в ЛК WB."
        )


@router.message(Command("history"))
async def cmd_history(message: Message):
    """Команда /history - история бронирований"""
    db = get_db()
    user_id = message.from_user.id

    bookings = db.get_user_bookings(user_id, limit=10)

    if not bookings:
        await message.answer(
            "📋 <b>История бронирований</b>\n\n"
            "У вас пока нет бронирований.\n\n"
            "Используйте /coefficients чтобы найти выгодные слоты.",
            parse_mode='HTML'
        )
        return

    lines = ["📋 <b>История бронирований</b>\n"]

    for b in bookings:
        # Определяем статус
        status_emoji = {
            'pending': '⏳',
            'confirmed': '✅',
            'cancelled': '❌',
            'failed': '❌',
            'expired': '⌛'
        }.get(b['status'], '❓')

        booking_type = '🤖' if b['booking_type'] == 'auto' else '👤'

        lines.append(
            f"\n{status_emoji} {booking_type} <b>{b['warehouse_name']}</b>\n"
            f"   📅 {b['slot_date']} | 💰 {b['coefficient']}\n"
            f"   {b['status']} | {b['created_at'][:16]}"
        )

        if b.get('supply_id'):
            lines.append(f"   🆔 <code>{b['supply_id']}</code>")

        if b.get('error_message'):
            lines.append(f"   ⚠️ {b['error_message'][:50]}")

    await message.answer(
        "\n".join(lines),
        parse_mode='HTML'
    )
