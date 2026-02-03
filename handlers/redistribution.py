"""
Handlers для перераспределения остатков между складами.

Команды:
- /redistribute - открыть Mini App для создания заявки

Перераспределение теперь доступно только через Mini App для единого UX.
"""

import logging

from aiogram import Router
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from db_factory import get_database
from config import Config

logger = logging.getLogger(__name__)

router = Router(name="redistribution")


# ==================== КОМАНДА /redistribute ====================

@router.message(Command("redistribute"))
async def cmd_redistribute(message: Message, state: FSMContext):
    """Команда /redistribute - открыть Mini App для перераспределения"""
    # Очищаем состояние на всякий случай
    await state.clear()

    db = get_database()
    user_id = message.from_user.id

    # Проверяем наличие токена
    suppliers = db.get_user_suppliers(user_id)

    if not suppliers:
        await message.answer(
            "⚠️ Для перераспределения необходим WB API токен.\n\n"
            "Добавьте токен командой /token"
        )
        return

    # Создаем кнопку для открытия Mini App
    webapp_url = Config.WEBAPP_URL.rstrip('/') + '/webapp/index.html'

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть форму перераспределения",
                    web_app=WebAppInfo(url=webapp_url)
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Просмотреть текущие заявки",
                    web_app=WebAppInfo(url=webapp_url)
                )
            ]
        ]
    )

    await message.answer(
        "📦 <b>Перераспределение остатков</b>\n\n"
        "Для создания заявки на перемещение товаров между складами "
        "используйте удобную визуальную форму в Mini App.\n\n"
        "✅ Пошаговый мастер создания\n"
        "✅ Автоматическая проверка остатков\n"
        "✅ История всех заявок\n"
        "✅ Отслеживание статуса\n\n"
        "Нажмите кнопку ниже, чтобы открыть форму:",
        parse_mode='HTML',
        reply_markup=keyboard
    )
