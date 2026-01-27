"""
Handlers для управления поставщиками.

Команды:
- /suppliers - список всех поставщиков и управление ими
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import Database
from config import Config

logger = logging.getLogger(__name__)

router = Router(name="supplier_management")


class SupplierStates(StatesGroup):
    """Состояния для работы с поставщиками"""
    waiting_for_supplier_name = State()
    renaming_supplier = State()


def get_db() -> Database:
    """Получает экземпляр БД"""
    return Database(Config.DATABASE_PATH)


@router.message(Command("suppliers"))
async def cmd_suppliers(message: Message):
    """Команда /suppliers - список всех поставщиков пользователя"""
    db = get_db()
    user_id = message.from_user.id

    # Регистрируем пользователя если его еще нет
    db.add_user(user_id, message.from_user.username, message.from_user.first_name)

    suppliers = db.get_user_suppliers(user_id)

    if not suppliers:
        text = (
            "📦 <b>Управление поставщиками</b>\n\n"
            "У вас пока нет добавленных поставщиков.\n\n"
            "Для начала работы добавьте WB API токен через /token\n"
            "Токен автоматически создаст поставщика."
        )
        buttons = [[
            InlineKeyboardButton(text="➕ Добавить токен", callback_data="add_token")
        ]]
    else:
        text = f"📦 <b>Ваши поставщики ({len(suppliers)})</b>\n\n"
        buttons = []

        for supplier in suppliers:
            # Статистика по поставщику
            stats = db.get_supplier_stats(supplier['id'])

            status_emoji = "⭐" if supplier['is_default'] else "📦"
            token_status = "✅" if supplier.get('token_active') else "❌"

            text += (
                f"{status_emoji} <b>{supplier['name']}</b> {token_status}\n"
                f"   Токен: {supplier['token_name']}\n"
                f"   Операций: {stats['operations_count']}\n"
                f"   Последняя активность: {stats['last_used']}\n\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    text=f"⚙️ {supplier['name']}",
                    callback_data=f"supplier_settings:{supplier['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="➕ Добавить токен",
                callback_data="add_token"
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        text,
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("supplier_settings:"))
async def callback_supplier_settings(callback: CallbackQuery):
    """Настройки конкретного поставщика"""
    await callback.answer()

    supplier_id = int(callback.data.split(":")[1])
    db = get_db()

    supplier = db.get_supplier(supplier_id)
    if not supplier:
        await callback.message.edit_text("❌ Поставщик не найден")
        return

    stats = db.get_supplier_stats(supplier_id)

    text = f"""
⚙️ <b>Настройки: {supplier['name']}</b>

<b>Статистика:</b>
• Всего операций: {stats['operations_count']}
• Перераспределений: {stats['redistributions_count']}
• Последняя активность: {stats['last_used']}

<b>Действия:</b>
"""

    buttons = [
        [InlineKeyboardButton(
            text="⭐ Сделать основным" if not supplier['is_default'] else "✅ Уже основной",
            callback_data=f"supplier_set_default:{supplier_id}" if not supplier['is_default'] else "noop"
        )],
        [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"supplier_rename:{supplier_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"supplier_delete_confirm:{supplier_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_suppliers")]
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=keyboard)


@router.callback_query(F.data.startswith("supplier_set_default:"))
async def callback_set_default_supplier(callback: CallbackQuery):
    """Устанавливает поставщика по умолчанию"""
    await callback.answer()

    supplier_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    db = get_db()

    success = db.set_default_supplier(user_id, supplier_id)

    if success:
        await callback.answer("✅ Поставщик по умолчанию установлен", show_alert=True)
        # Обновить display
        await callback_supplier_settings(callback)
    else:
        await callback.answer("❌ Ошибка при установке", show_alert=True)


@router.callback_query(F.data.startswith("supplier_rename:"))
async def callback_supplier_rename(callback: CallbackQuery, state: FSMContext):
    """Начало переименования поставщика"""
    await callback.answer()

    supplier_id = int(callback.data.split(":")[1])
    db = get_db()

    supplier = db.get_supplier(supplier_id)
    if not supplier:
        await callback.message.edit_text("❌ Поставщик не найден")
        return

    await state.update_data(renaming_supplier_id=supplier_id)
    await state.set_state(SupplierStates.renaming_supplier)

    await callback.message.edit_text(
        f"✏️ <b>Переименование поставщика</b>\n\n"
        f"Текущее название: <b>{supplier['name']}</b>\n\n"
        f"Введите новое название или /cancel для отмены:",
        parse_mode='HTML'
    )


@router.message(SupplierStates.renaming_supplier)
async def process_supplier_rename(message: Message, state: FSMContext):
    """Обработка нового названия поставщика"""
    new_name = message.text.strip()

    if new_name.lower() == '/cancel':
        await state.clear()
        await message.answer("❌ Переименование отменено. Используйте /suppliers")
        return

    data = await state.get_data()
    supplier_id = data.get('renaming_supplier_id')

    if not supplier_id:
        await state.clear()
        await message.answer("❌ Ошибка: поставщик не найден. Используйте /suppliers")
        return

    db = get_db()
    success = db.update_supplier_name(supplier_id, new_name)

    if success:
        await message.answer(
            f"✅ Поставщик переименован в <b>{new_name}</b>\n\n"
            f"Используйте /suppliers для просмотра",
            parse_mode='HTML'
        )
    else:
        await message.answer("❌ Ошибка при переименовании")

    await state.clear()


@router.callback_query(F.data.startswith("supplier_delete_confirm:"))
async def callback_supplier_delete_confirm(callback: CallbackQuery):
    """Подтверждение удаления поставщика"""
    await callback.answer()

    supplier_id = int(callback.data.split(":")[1])
    db = get_db()

    supplier = db.get_supplier(supplier_id)
    if not supplier:
        await callback.message.edit_text("❌ Поставщик не найден")
        return

    text = f"""
⚠️ <b>Удаление поставщика</b>

Вы уверены, что хотите удалить поставщика <b>{supplier['name']}</b>?

<b>ВНИМАНИЕ:</b>
• Связанные заявки на перераспределение останутся, но станут недоступны для управления
• Сам токен останется и будет доступен через /token
• Это действие нельзя отменить

Вы уверены?
"""

    buttons = [
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"supplier_delete:{supplier_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"supplier_settings:{supplier_id}")]
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=keyboard)


@router.callback_query(F.data.startswith("supplier_delete:"))
async def callback_supplier_delete(callback: CallbackQuery):
    """Удаление поставщика"""
    await callback.answer()

    supplier_id = int(callback.data.split(":")[1])
    db = get_db()

    success = db.delete_supplier(supplier_id)

    if success:
        await callback.message.edit_text(
            "✅ Поставщик удален.\n\n"
            "Используйте /suppliers для просмотра оставшихся."
        )
    else:
        await callback.message.edit_text("❌ Ошибка при удалении поставщика")


@router.callback_query(F.data == "back_to_suppliers")
async def callback_back_to_suppliers(callback: CallbackQuery):
    """Возврат к списку поставщиков"""
    await callback.answer()

    # Создать фейковое message для повторного использования cmd_suppliers
    await cmd_suppliers(callback.message)


@router.callback_query(F.data == "noop")
async def callback_noop(callback: CallbackQuery):
    """Пустой callback для неактивных кнопок"""
    await callback.answer()
