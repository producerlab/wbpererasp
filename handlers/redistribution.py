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

from database import Database
from config import Config

logger = logging.getLogger(__name__)

router = Router(name="redistribution")


def get_db() -> Database:
    """Получает экземпляр БД"""
    return Database(Config.DATABASE_PATH)


# ==================== КОМАНДА /redistribute ====================

@router.message(Command("redistribute"))
async def cmd_redistribute(message: Message, state: FSMContext):
    """Команда /redistribute - открыть Mini App для перераспределения"""
    # Очищаем состояние на всякий случай
    await state.clear()

    db = get_db()
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
    webapp_url = Config.WEBAPP_URL

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


@router.callback_query(F.data.startswith("redist_select_supplier:"))
async def callback_select_supplier(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора поставщика"""
    await callback.answer()

    supplier_id = int(callback.data.split(":")[1])
    db = get_db()
    user_id = callback.from_user.id

    # Получаем информацию о поставщике
    suppliers = db.get_user_suppliers(user_id)
    supplier = next((s for s in suppliers if s['id'] == supplier_id), None)

    if not supplier:
        await callback.message.edit_text("❌ Поставщик не найден")
        await state.clear()
        return

    await state.update_data(supplier_id=supplier_id)
    await start_redistribution_for_supplier(callback.message, state, supplier)


async def start_redistribution_for_supplier(message: Message, state: FSMContext, supplier: dict):
    """Начинает процесс перераспределения для выбранного поставщика"""
    db = get_db()

    # Получаем токен поставщика
    token = db.get_wb_token_by_id(supplier['token_id'])
    if not token or not token['is_active']:
        await message.answer(
            f"⚠️ Токен поставщика <b>{supplier['name']}</b> недоступен.\n\n"
            "Проверьте токен командой /token",
            parse_mode='HTML'
        )
        await state.clear()
        return

    status_msg = await message.answer(
        f"🔄 Загружаю остатки для <b>{supplier['name']}</b>...",
        parse_mode='HTML'
    )

    try:
        from utils.encryption import decrypt_token
        decrypted_token = decrypt_token(token['encrypted_token'])

        service = RedistributionService(db)
        stocks = await service.get_stocks_for_redistribution(decrypted_token)

        if not stocks:
            await status_msg.edit_text(
                f"📦 <b>Перераспределение остатков</b>\n"
                f"Поставщик: <b>{supplier['name']}</b>\n\n"
                "У вас нет товаров на складах WB.\n\n"
                "Проверьте что у токена есть права на раздел <b>Контент</b>.",
                parse_mode='HTML'
            )
            await state.clear()
            return

        # Сохраняем остатки в состояние
        await state.set_state(RedistributionStates.selecting_sku)
        await state.update_data(
            stocks_by_sku=stocks,
            page=0,
            api_token=decrypted_token,
            supplier_name=supplier['name']
        )

        keyboard = build_sku_selection_keyboard(stocks, page=0)

        await status_msg.edit_text(
            f"📦 <b>Перераспределение остатков</b>\n"
            f"Поставщик: <b>{supplier['name']}</b>\n\n"
            f"Найдено товаров: {len(stocks)}\n\n"
            "Выберите артикул для перемещения:",
            parse_mode='HTML',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Failed to load stocks for supplier {supplier['id']}: {e}")
        await status_msg.edit_text(
            "❌ Ошибка загрузки остатков.\n\n"
            "Попробуйте позже или проверьте токен командой /token"
        )
        await state.clear()


# ==================== ВЫБОР АРТИКУЛА ====================

@router.callback_query(F.data.startswith("redist_page:"))
async def callback_sku_page(callback: CallbackQuery, state: FSMContext):
    """Переключение страницы артикулов"""
    await callback.answer()

    page = int(callback.data.split(":")[1])
    data = await state.get_data()
    stocks = data.get('stocks_by_sku', {})

    await state.update_data(page=page)

    keyboard = build_sku_selection_keyboard(stocks, page=page)

    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("redist_sku:"))
async def callback_select_sku(callback: CallbackQuery, state: FSMContext):
    """Выбор артикула"""
    await callback.answer()

    sku = callback.data.split(":")[1]
    data = await state.get_data()
    api_token = data.get('api_token')
    stocks_by_sku = data.get('stocks_by_sku', {})

    # Получаем информацию о товаре
    stock_data = stocks_by_sku.get(sku)
    if not stock_data:
        await callback.message.answer("❌ Артикул не найден")
        return

    # Получаем остатки для этого SKU
    service = RedistributionService(get_db())

    try:
        stocks = await service.get_stocks_for_sku(api_token, sku)
    except Exception as e:
        logger.error(f"Failed to get stocks for SKU: {e}")
        await callback.message.answer("❌ Ошибка загрузки остатков")
        return

    if not stocks:
        await callback.message.answer(
            f"📦 Артикул <code>{sku}</code>\n\n"
            "Нет остатков на складах.",
            parse_mode='HTML'
        )
        return

    # Сохраняем и переходим к выбору склада-источника
    await state.set_state(RedistributionStates.selecting_source)
    await state.update_data(
        selected_sku=sku,
        sku_stocks=stocks,
        product_name=stock_data.product_name
    )

    keyboard = build_source_warehouse_keyboard(stocks, sku)

    await callback.message.edit_text(
        f"📦 <b>Артикул:</b> <code>{sku}</code>\n"
        f"📝 {stock_data.product_name[:50] if stock_data.product_name else ''}\n\n"
        f"<b>Выберите склад-источник</b> (откуда забрать):",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data == "redist_search")
async def callback_search_sku(callback: CallbackQuery, state: FSMContext):
    """Поиск артикула"""
    await callback.answer()

    await state.set_state(RedistributionStates.waiting_quantity_input)
    await state.update_data(search_mode=True)

    await callback.message.edit_text(
        "🔍 <b>Поиск артикула</b>\n\n"
        "Введите артикул или часть названия товара:\n\n"
        "Для отмены отправьте /cancel",
        parse_mode='HTML'
    )


# ==================== ВЫБОР СКЛАДА-ИСТОЧНИКА ====================

@router.callback_query(F.data.startswith("redist_src:"))
async def callback_select_source(callback: CallbackQuery, state: FSMContext):
    """Выбор склада-источника"""
    await callback.answer()

    parts = callback.data.split(":")
    source_id = int(parts[1])
    sku = parts[2]

    data = await state.get_data()
    sku_stocks = data.get('sku_stocks', [])

    # Находим остаток на выбранном складе
    source_stock = next(
        (s for s in sku_stocks if s.warehouse_id == source_id),
        None
    )

    if not source_stock or source_stock.quantity <= 0:
        await callback.message.answer("❌ Нет доступных остатков на этом складе")
        return

    # Сохраняем и переходим к выбору склада-назначения
    await state.set_state(RedistributionStates.selecting_target)
    await state.update_data(
        source_warehouse_id=source_id,
        source_warehouse_name=source_stock.warehouse_name,
        available_quantity=source_stock.available
    )

    keyboard = build_target_warehouse_keyboard(source_id, sku)
    service = RedistributionService(get_db())
    source_name = service.get_warehouse_name(source_id)

    await callback.message.edit_text(
        f"📦 <b>Артикул:</b> <code>{sku}</code>\n"
        f"📤 <b>Откуда:</b> {source_name} ({source_stock.quantity} шт)\n\n"
        f"<b>Выберите склад-назначения</b> (куда везти):",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data == "redist_back_sku")
async def callback_back_to_sku(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору артикула"""
    await callback.answer()

    data = await state.get_data()
    stocks = data.get('stocks_by_sku', {})
    page = data.get('page', 0)

    await state.set_state(RedistributionStates.selecting_sku)

    keyboard = build_sku_selection_keyboard(stocks, page=page)

    await callback.message.edit_text(
        "📦 <b>Перераспределение остатков</b>\n\n"
        f"Найдено товаров: {len(stocks)}\n\n"
        "Выберите артикул для перемещения:",
        parse_mode='HTML',
        reply_markup=keyboard
    )


# ==================== ВЫБОР СКЛАДА-НАЗНАЧЕНИЯ ====================

@router.callback_query(F.data.startswith("redist_dst:"))
async def callback_select_target(callback: CallbackQuery, state: FSMContext):
    """Выбор склада-назначения"""
    await callback.answer()

    parts = callback.data.split(":")
    target_id = int(parts[1])
    source_id = int(parts[2])
    sku = parts[3]

    data = await state.get_data()
    available = data.get('available_quantity', 0)

    # Сохраняем и переходим к вводу количества
    await state.set_state(RedistributionStates.entering_quantity)
    await state.update_data(
        target_warehouse_id=target_id,
        selected_quantity=0
    )

    keyboard = build_quantity_keyboard(
        available=available,
        current=0,
        sku=sku,
        source_id=source_id,
        target_id=target_id
    )

    service = RedistributionService(get_db())
    source_name = service.get_warehouse_name(source_id)
    target_name = service.get_warehouse_name(target_id)

    await callback.message.edit_text(
        f"📦 <b>Артикул:</b> <code>{sku}</code>\n"
        f"📤 <b>Откуда:</b> {source_name}\n"
        f"📥 <b>Куда:</b> {target_name}\n\n"
        f"<b>Укажите количество</b> (доступно {available} шт):",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("redist_back_src:"))
async def callback_back_to_source(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору склада-источника"""
    await callback.answer()

    sku = callback.data.split(":")[1]
    data = await state.get_data()
    sku_stocks = data.get('sku_stocks', [])

    await state.set_state(RedistributionStates.selecting_source)

    keyboard = build_source_warehouse_keyboard(sku_stocks, sku)

    await callback.message.edit_text(
        f"📦 <b>Артикул:</b> <code>{sku}</code>\n\n"
        f"<b>Выберите склад-источник</b> (откуда забрать):",
        parse_mode='HTML',
        reply_markup=keyboard
    )


# ==================== ВВОД КОЛИЧЕСТВА ====================

@router.callback_query(F.data.startswith("redist_qty_add:"))
async def callback_qty_add(callback: CallbackQuery, state: FSMContext):
    """Добавить количество"""
    await callback.answer()

    parts = callback.data.split(":")
    delta = int(parts[1])
    sku = parts[2]
    source_id = int(parts[3])
    target_id = int(parts[4])

    data = await state.get_data()
    current = data.get('selected_quantity', 0)
    available = data.get('available_quantity', 0)

    new_qty = min(current + delta, available)
    await state.update_data(selected_quantity=new_qty)

    keyboard = build_quantity_keyboard(
        available=available,
        current=new_qty,
        sku=sku,
        source_id=source_id,
        target_id=target_id
    )

    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("redist_qty_sub:"))
async def callback_qty_sub(callback: CallbackQuery, state: FSMContext):
    """Уменьшить количество"""
    await callback.answer()

    parts = callback.data.split(":")
    delta = int(parts[1])
    sku = parts[2]
    source_id = int(parts[3])
    target_id = int(parts[4])

    data = await state.get_data()
    current = data.get('selected_quantity', 0)
    available = data.get('available_quantity', 0)

    new_qty = max(current - delta, 0)
    await state.update_data(selected_quantity=new_qty)

    keyboard = build_quantity_keyboard(
        available=available,
        current=new_qty,
        sku=sku,
        source_id=source_id,
        target_id=target_id
    )

    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("redist_qty_max:"))
async def callback_qty_max(callback: CallbackQuery, state: FSMContext):
    """Максимальное количество"""
    await callback.answer()

    parts = callback.data.split(":")
    sku = parts[1]
    source_id = int(parts[2])
    target_id = int(parts[3])

    data = await state.get_data()
    available = data.get('available_quantity', 0)

    await state.update_data(selected_quantity=available)

    keyboard = build_quantity_keyboard(
        available=available,
        current=available,
        sku=sku,
        source_id=source_id,
        target_id=target_id
    )

    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("redist_qty_min:"))
async def callback_qty_min(callback: CallbackQuery, state: FSMContext):
    """Минимальное количество (0)"""
    await callback.answer()

    parts = callback.data.split(":")
    sku = parts[1]
    source_id = int(parts[2])
    target_id = int(parts[3])

    data = await state.get_data()
    available = data.get('available_quantity', 0)

    await state.update_data(selected_quantity=0)

    keyboard = build_quantity_keyboard(
        available=available,
        current=0,
        sku=sku,
        source_id=source_id,
        target_id=target_id
    )

    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("redist_qty_input:"))
async def callback_qty_input(callback: CallbackQuery, state: FSMContext):
    """Ввод количества вручную"""
    await callback.answer()

    await state.set_state(RedistributionStates.waiting_quantity_input)
    await state.update_data(search_mode=False)

    data = await state.get_data()
    available = data.get('available_quantity', 0)

    await callback.message.answer(
        f"✏️ <b>Введите количество</b>\n\n"
        f"Доступно: {available} шт\n\n"
        f"Введите число от 1 до {available}:",
        parse_mode='HTML'
    )


@router.message(RedistributionStates.waiting_quantity_input)
async def process_quantity_input(message: Message, state: FSMContext):
    """Обработка введённого количества"""
    data = await state.get_data()

    # Проверяем режим поиска
    if data.get('search_mode'):
        # Поиск артикула
        search_query = message.text.strip()
        stocks = data.get('stocks_by_sku', {})

        # Фильтруем
        filtered = {
            sku: stock for sku, stock in stocks.items()
            if search_query.lower() in sku.lower() or
               search_query.lower() in (stock.product_name or '').lower()
        }

        if not filtered:
            await message.answer(
                f"🔍 По запросу «{search_query}» ничего не найдено.\n\n"
                "Попробуйте другой запрос или /cancel для отмены."
            )
            return

        await state.set_state(RedistributionStates.selecting_sku)
        await state.update_data(stocks_by_sku=filtered, page=0)

        keyboard = build_sku_selection_keyboard(filtered, page=0)

        await message.answer(
            f"🔍 Найдено: {len(filtered)}\n\n"
            "Выберите артикул:",
            reply_markup=keyboard
        )
        return

    # Ввод количества
    available = data.get('available_quantity', 0)

    try:
        quantity = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число")
        return

    if quantity <= 0:
        await message.answer("❌ Количество должно быть больше 0")
        return

    if quantity > available:
        await message.answer(f"❌ Максимум доступно: {available} шт")
        return

    # Сохраняем и переходим к подтверждению
    await state.update_data(selected_quantity=quantity)

    sku = data.get('selected_sku')
    source_id = data.get('source_warehouse_id')
    target_id = data.get('target_warehouse_id')

    # Переходим к подтверждению
    await state.set_state(RedistributionStates.confirming)

    service = RedistributionService(get_db())

    request = RedistributionRequest(
        sku=sku,
        source_warehouse_id=source_id,
        target_warehouse_id=target_id,
        quantity=quantity,
        product_name=data.get('product_name', '')
    )

    summary = service.format_redistribution_summary(request)
    keyboard = build_confirmation_keyboard(sku, source_id, target_id, quantity)

    await message.answer(
        f"{summary}\n\n<b>Подтвердите перераспределение:</b>",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("redist_qty_confirm:"))
async def callback_qty_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение количества и переход к финальному подтверждению"""
    await callback.answer()

    parts = callback.data.split(":")
    quantity = int(parts[1])
    sku = parts[2]
    source_id = int(parts[3])
    target_id = int(parts[4])

    data = await state.get_data()

    await state.set_state(RedistributionStates.confirming)

    service = RedistributionService(get_db())

    request = RedistributionRequest(
        sku=sku,
        source_warehouse_id=source_id,
        target_warehouse_id=target_id,
        quantity=quantity,
        product_name=data.get('product_name', '')
    )

    summary = service.format_redistribution_summary(request)
    keyboard = build_confirmation_keyboard(sku, source_id, target_id, quantity)

    await callback.message.edit_text(
        f"{summary}\n\n<b>Подтвердите перераспределение:</b>",
        parse_mode='HTML',
        reply_markup=keyboard
    )


# ==================== ПОДТВЕРЖДЕНИЕ И ВЫПОЛНЕНИЕ ====================

@router.callback_query(F.data.startswith("redist_confirm:"))
async def callback_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и выполнение перераспределения"""
    await callback.answer("Создаю заявку...")

    parts = callback.data.split(":")
    sku = parts[1]
    source_id = int(parts[2])
    target_id = int(parts[3])
    quantity = int(parts[4])

    data = await state.get_data()
    api_token = data.get('api_token')
    user_id = callback.from_user.id

    # Показываем статус
    await callback.message.edit_text(
        "🔄 <b>Создаю заявку на перераспределение...</b>",
        parse_mode='HTML'
    )

    db = get_db()
    service = RedistributionService(db)

    request = RedistributionRequest(
        sku=sku,
        source_warehouse_id=source_id,
        target_warehouse_id=target_id,
        quantity=quantity,
        product_name=data.get('product_name', '')
    )

    result = await service.create_redistribution(api_token, user_id, request)

    keyboard = build_result_keyboard(result.success, result.supply_id)

    if result.success:
        summary = service.format_redistribution_summary(request, result.coefficient)
        await callback.message.edit_text(
            f"✅ <b>Заявка создана!</b>\n\n"
            f"{summary}\n\n"
            f"🆔 ID поставки: <code>{result.supply_id}</code>\n\n"
            f"⚠️ <i>Подтвердите поставку в ЛК Wildberries в течение 24 часов!</i>",
            parse_mode='HTML',
            reply_markup=keyboard
        )
    else:
        await callback.message.edit_text(
            f"❌ <b>Ошибка создания заявки</b>\n\n"
            f"<b>Причина:</b> {result.error_message}\n\n"
            f"<i>Попробуйте позже или создайте заявку вручную в ЛК WB</i>",
            parse_mode='HTML',
            reply_markup=keyboard
        )

    await state.clear()


# ==================== ОТМЕНА И НАВИГАЦИЯ ====================

@router.callback_query(F.data == "redist_cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена перераспределения"""
    await callback.answer("Отменено")
    await state.clear()

    await callback.message.edit_text(
        "❌ Перераспределение отменено.\n\n"
        "Используйте /redistribute чтобы начать заново."
    )


@router.callback_query(F.data == "redist_restart")
async def callback_restart(callback: CallbackQuery, state: FSMContext):
    """Начать заново"""
    await callback.answer()
    await state.clear()

    # Имитируем команду /redistribute
    await cmd_redistribute(callback.message, state)


@router.callback_query(F.data.startswith("redist_back_dst:"))
async def callback_back_to_target(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору склада-назначения"""
    await callback.answer()

    parts = callback.data.split(":")
    source_id = int(parts[1])
    sku = parts[2]

    await state.set_state(RedistributionStates.selecting_target)

    keyboard = build_target_warehouse_keyboard(source_id, sku)
    service = RedistributionService(get_db())
    source_name = service.get_warehouse_name(source_id)

    data = await state.get_data()
    sku_stocks = data.get('sku_stocks', [])
    source_stock = next(
        (s for s in sku_stocks if s.warehouse_id == source_id),
        None
    )
    qty = source_stock.quantity if source_stock else 0

    await callback.message.edit_text(
        f"📦 <b>Артикул:</b> <code>{sku}</code>\n"
        f"📤 <b>Откуда:</b> {source_name} ({qty} шт)\n\n"
        f"<b>Выберите склад-назначения</b> (куда везти):",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("redist_back_qty:"))
async def callback_back_to_quantity(callback: CallbackQuery, state: FSMContext):
    """Назад к вводу количества"""
    await callback.answer()

    parts = callback.data.split(":")
    sku = parts[1]
    source_id = int(parts[2])
    target_id = int(parts[3])

    data = await state.get_data()
    available = data.get('available_quantity', 0)
    current = data.get('selected_quantity', 0)

    await state.set_state(RedistributionStates.entering_quantity)

    keyboard = build_quantity_keyboard(
        available=available,
        current=current,
        sku=sku,
        source_id=source_id,
        target_id=target_id
    )

    service = RedistributionService(get_db())
    source_name = service.get_warehouse_name(source_id)
    target_name = service.get_warehouse_name(target_id)

    await callback.message.edit_text(
        f"📦 <b>Артикул:</b> <code>{sku}</code>\n"
        f"📤 <b>Откуда:</b> {source_name}\n"
        f"📥 <b>Куда:</b> {target_name}\n\n"
        f"<b>Укажите количество</b> (доступно {available} шт):",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data == "noop")
async def callback_noop(callback: CallbackQuery):
    """Пустой callback для информационных кнопок"""
    await callback.answer()


@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await callback.answer()
    await state.clear()

    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\n"
        "Доступные команды:\n"
        "/redistribute - перераспределить остатки\n"
        "/coefficients - текущие коэффициенты\n"
        "/monitor - настроить мониторинг\n"
        "/book - забронировать слот\n"
        "/help - справка",
        parse_mode='HTML'
    )
