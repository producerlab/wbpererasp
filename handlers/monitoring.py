"""
Handlers для настройки мониторинга коэффициентов.

Команды:
- /monitor - настройка мониторинга
- /coefficients - текущие коэффициенты
- /autobook - настройка автобронирования
"""

import logging
from typing import List
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import Database
from config import Config
from wb_api.warehouses import WarehousesAPI

logger = logging.getLogger(__name__)

router = Router(name="monitoring")


class MonitoringStates(StatesGroup):
    """Состояния для настройки мониторинга"""
    selecting_warehouses = State()
    selecting_coefficients = State()
    confirming = State()


def get_db() -> Database:
    """Получает экземпляр БД"""
    return Database(Config.DATABASE_PATH)


# Популярные склады для быстрого выбора
POPULAR_WAREHOUSES = [
    (117501, "Коледино"),
    (117986, "Электросталь"),
    (507, "Подольск"),
    (130744, "СПб (Шушары)"),
    (1733, "Казань"),
    (218210, "Екатеринбург"),
    (686, "Новосибирск"),
    (2737, "Краснодар"),
]


@router.message(Command("monitor"))
async def cmd_monitor(message: Message):
    """Команда /monitor - настройка мониторинга"""
    db = get_db()
    user_id = message.from_user.id

    # Проверяем наличие токена
    token = db.get_wb_token(user_id)
    if not token:
        await message.answer(
            "⚠️ Для настройки мониторинга необходим WB API токен.\n\n"
            "Добавьте токен командой /token"
        )
        return

    # Получаем текущие подписки
    subscriptions = db.get_user_subscriptions(user_id)

    if subscriptions:
        # Показываем текущие подписки
        text = "📊 <b>Ваши подписки на мониторинг</b>\n\n"

        for sub in subscriptions:
            status = "✅ Активна" if sub['is_active'] else "❌ Отключена"
            auto = "🤖 Автобронь" if sub['auto_book'] else "📢 Только уведомления"
            warehouses = sub['warehouse_ids']
            coeffs = sub['target_coefficients']

            text += f"""
{status} | {auto}
   Складов: {len(warehouses)}
   Коэффициенты: {', '.join(map(str, coeffs))}
"""

        buttons = [
            [InlineKeyboardButton(
                text="➕ Добавить подписку",
                callback_data="new_subscription"
            )],
            [InlineKeyboardButton(
                text="⚙️ Изменить подписки",
                callback_data="edit_subscriptions"
            )]
        ]
    else:
        text = """
📊 <b>Мониторинг коэффициентов</b>

У вас пока нет активных подписок.

Мониторинг позволяет:
• Получать уведомления при изменении коэффициентов
• Автоматически бронировать выгодные слоты
• Отслеживать конкретные склады

Нажмите «Настроить мониторинг» чтобы начать.
"""
        buttons = [[
            InlineKeyboardButton(
                text="⚙️ Настроить мониторинг",
                callback_data="new_subscription"
            )
        ]]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, parse_mode='HTML', reply_markup=keyboard)


@router.callback_query(F.data == "new_subscription")
async def callback_new_subscription(callback: CallbackQuery, state: FSMContext):
    """Начало создания подписки"""
    await callback.answer()

    # Показываем выбор складов
    buttons = []

    for wh_id, wh_name in POPULAR_WAREHOUSES:
        buttons.append([
            InlineKeyboardButton(
                text=f"⬜ {wh_name}",
                callback_data=f"toggle_wh:{wh_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="✅ Готово",
            callback_data="warehouses_done"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "📍 <b>Выберите склады для мониторинга</b>\n\n"
        "Нажимайте на склады чтобы добавить/убрать их из списка.\n"
        "Когда закончите, нажмите «Готово».",
        parse_mode='HTML',
        reply_markup=keyboard
    )

    # Инициализируем выбранные склады
    await state.set_state(MonitoringStates.selecting_warehouses)
    await state.update_data(selected_warehouses=[])


@router.callback_query(
    MonitoringStates.selecting_warehouses,
    F.data.startswith("toggle_wh:")
)
async def callback_toggle_warehouse(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора склада"""
    await callback.answer()

    wh_id = int(callback.data.split(":")[1])

    # Получаем текущий список
    data = await state.get_data()
    selected = data.get('selected_warehouses', [])

    # Переключаем
    if wh_id in selected:
        selected.remove(wh_id)
    else:
        selected.append(wh_id)

    await state.update_data(selected_warehouses=selected)

    # Обновляем клавиатуру
    buttons = []
    for wh_id_opt, wh_name in POPULAR_WAREHOUSES:
        if wh_id_opt in selected:
            emoji = "✅"
        else:
            emoji = "⬜"

        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {wh_name}",
                callback_data=f"toggle_wh:{wh_id_opt}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text=f"✅ Готово ({len(selected)} выбрано)",
            callback_data="warehouses_done"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(
    MonitoringStates.selecting_warehouses,
    F.data == "warehouses_done"
)
async def callback_warehouses_done(callback: CallbackQuery, state: FSMContext):
    """Завершение выбора складов"""
    data = await state.get_data()
    selected = data.get('selected_warehouses', [])

    if not selected:
        await callback.answer("Выберите хотя бы один склад!", show_alert=True)
        return

    await callback.answer()

    # Переходим к выбору коэффициентов
    buttons = [
        [InlineKeyboardButton(
            text="✅ 0 (бесплатно)",
            callback_data="toggle_coeff:0"
        )],
        [InlineKeyboardButton(
            text="✅ 0.5",
            callback_data="toggle_coeff:0.5"
        )],
        [InlineKeyboardButton(
            text="✅ 1.0",
            callback_data="toggle_coeff:1"
        )],
        [InlineKeyboardButton(
            text="⬜ 1.5",
            callback_data="toggle_coeff:1.5"
        )],
        [InlineKeyboardButton(
            text="⬜ 2.0",
            callback_data="toggle_coeff:2"
        )],
        [InlineKeyboardButton(
            text="✅ Готово",
            callback_data="coefficients_done"
        )]
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "🎯 <b>Выберите коэффициенты для отслеживания</b>\n\n"
        "Вы будете получать уведомления когда коэффициент\n"
        "станет равен или меньше выбранных значений.\n\n"
        f"Выбрано складов: {len(selected)}",
        parse_mode='HTML',
        reply_markup=keyboard
    )

    await state.set_state(MonitoringStates.selecting_coefficients)
    await state.update_data(selected_coefficients=[0, 0.5, 1])  # По умолчанию


@router.callback_query(
    MonitoringStates.selecting_coefficients,
    F.data.startswith("toggle_coeff:")
)
async def callback_toggle_coefficient(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора коэффициента"""
    await callback.answer()

    coeff = float(callback.data.split(":")[1])

    data = await state.get_data()
    selected = data.get('selected_coefficients', [])

    if coeff in selected:
        selected.remove(coeff)
    else:
        selected.append(coeff)
        selected.sort()

    await state.update_data(selected_coefficients=selected)

    # Обновляем клавиатуру
    all_coeffs = [0, 0.5, 1, 1.5, 2]
    buttons = []

    for c in all_coeffs:
        emoji = "✅" if c in selected else "⬜"
        label = "бесплатно" if c == 0 else str(c)
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {label}",
                callback_data=f"toggle_coeff:{c}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text=f"✅ Готово ({len(selected)} выбрано)",
            callback_data="coefficients_done"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(
    MonitoringStates.selecting_coefficients,
    F.data == "coefficients_done"
)
async def callback_coefficients_done(callback: CallbackQuery, state: FSMContext):
    """Завершение выбора коэффициентов"""
    data = await state.get_data()
    selected_coeffs = data.get('selected_coefficients', [])

    if not selected_coeffs:
        await callback.answer("Выберите хотя бы один коэффициент!", show_alert=True)
        return

    await callback.answer()

    selected_warehouses = data.get('selected_warehouses', [])

    # Спрашиваем про автобронирование
    buttons = [
        [InlineKeyboardButton(
            text="🤖 Включить автобронирование",
            callback_data="autobook:on"
        )],
        [InlineKeyboardButton(
            text="📢 Только уведомления",
            callback_data="autobook:off"
        )]
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    warehouse_names = [
        name for wh_id, name in POPULAR_WAREHOUSES
        if wh_id in selected_warehouses
    ]

    await callback.message.edit_text(
        f"🤖 <b>Автобронирование</b>\n\n"
        f"Склады: {', '.join(warehouse_names)}\n"
        f"Коэффициенты: {', '.join(map(str, selected_coeffs))}\n\n"
        f"Хотите включить автоматическое бронирование?\n\n"
        f"При автобронировании бот автоматически забронирует слот\n"
        f"когда появится выгодный коэффициент.",
        parse_mode='HTML',
        reply_markup=keyboard
    )

    await state.set_state(MonitoringStates.confirming)


@router.callback_query(
    MonitoringStates.confirming,
    F.data.startswith("autobook:")
)
async def callback_autobook_choice(callback: CallbackQuery, state: FSMContext):
    """Выбор автобронирования и сохранение подписки"""
    await callback.answer()

    auto_book = callback.data == "autobook:on"

    data = await state.get_data()
    selected_warehouses = data.get('selected_warehouses', [])
    selected_coeffs = data.get('selected_coefficients', [])

    # Сохраняем подписку
    db = get_db()
    user_id = callback.from_user.id

    token = db.get_wb_token(user_id)
    if not token:
        await callback.message.edit_text(
            "❌ Ошибка: токен не найден. Добавьте токен командой /token"
        )
        await state.clear()
        return

    sub_id = db.add_monitoring_subscription(
        user_id=user_id,
        token_id=token['id'],
        warehouse_ids=selected_warehouses,
        target_coefficients=selected_coeffs,
        auto_book=auto_book
    )

    warehouse_names = [
        name for wh_id, name in POPULAR_WAREHOUSES
        if wh_id in selected_warehouses
    ]

    auto_status = "включено" if auto_book else "отключено"

    await callback.message.edit_text(
        f"✅ <b>Мониторинг настроен!</b>\n\n"
        f"📍 Склады: {', '.join(warehouse_names)}\n"
        f"🎯 Коэффициенты: {', '.join(map(str, selected_coeffs))}\n"
        f"🤖 Автобронирование: {auto_status}\n\n"
        f"Я буду проверять коэффициенты каждые {Config.COEFFICIENT_POLL_INTERVAL} секунд\n"
        f"и сразу уведомлять вас об изменениях.\n\n"
        f"Команды:\n"
        f"/coefficients - текущие коэффициенты\n"
        f"/monitor - управление подписками\n"
        f"/history - история бронирований",
        parse_mode='HTML'
    )

    await state.clear()


@router.message(Command("coefficients"))
async def cmd_coefficients(message: Message):
    """Команда /coefficients - текущие коэффициенты"""
    db = get_db()
    user_id = message.from_user.id

    # Проверяем наличие токена
    token = db.get_wb_token(user_id)
    if not token:
        await message.answer(
            "⚠️ Для просмотра коэффициентов необходим WB API токен.\n\n"
            "Добавьте токен командой /token"
        )
        return

    status_msg = await message.answer("🔄 Загружаю коэффициенты...")

    try:
        # Импортируем здесь чтобы избежать циклических импортов
        from services.coefficient_monitor import CoefficientMonitor

        # Получаем системный токен для запроса коэффициентов
        # (или используем токен пользователя)
        monitor = CoefficientMonitor(db, token['token'])
        coefficients = await monitor.get_profitable_slots(max_coefficient=2.0)

        if not coefficients:
            await status_msg.edit_text(
                "📊 Нет доступных слотов с коэффициентом ≤ 2.0\n\n"
                "Попробуйте позже или /monitor для настройки уведомлений."
            )
            return

        # Группируем по складам
        by_warehouse = {}
        for c in coefficients:
            name = c.warehouse_name or str(c.warehouse_id)
            if name not in by_warehouse:
                by_warehouse[name] = []
            by_warehouse[name].append(c)

        # Формируем сообщение
        lines = ["📊 <b>Выгодные коэффициенты</b>\n"]

        for wh_name in sorted(by_warehouse.keys())[:10]:  # Топ 10 складов
            wh_coeffs = sorted(by_warehouse[wh_name], key=lambda x: x.date)[:3]

            lines.append(f"\n📍 <b>{wh_name}</b>")

            for c in wh_coeffs:
                if c.coefficient == 0:
                    emoji = "🆓"
                elif c.coefficient <= 0.5:
                    emoji = "🔥"
                elif c.coefficient <= 1:
                    emoji = "✅"
                else:
                    emoji = "💰"

                lines.append(
                    f"  {emoji} {c.date.strftime('%d.%m')}: <b>{c.coefficient}</b>"
                )

        lines.append("\n<i>Показаны слоты с коэффициентом ≤ 2.0</i>")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_coefficients")],
            [InlineKeyboardButton(text="⚙️ Настроить мониторинг", callback_data="new_subscription")]
        ])

        await status_msg.edit_text(
            "\n".join(lines),
            parse_mode='HTML',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Failed to get coefficients: {e}")
        await status_msg.edit_text(
            f"❌ Ошибка при загрузке коэффициентов.\n\n"
            f"Попробуйте позже или проверьте токен командой /token"
        )


@router.callback_query(F.data == "refresh_coefficients")
async def callback_refresh_coefficients(callback: CallbackQuery):
    """Обновление коэффициентов"""
    await callback.answer("Обновляю...")
    # Вызываем ту же логику что и /coefficients
    await cmd_coefficients(callback.message)


@router.message(Command("recommend"))
async def cmd_recommend(message: Message):
    """Команда /recommend - рекомендации куда везти товар"""
    db = get_db()
    user_id = message.from_user.id

    # Проверяем наличие токена
    token = db.get_wb_token(user_id)
    if not token:
        await message.answer(
            "⚠️ Для получения рекомендаций необходим WB API токен.\n\n"
            "Добавьте токен командой /token"
        )
        return

    status_msg = await message.answer("🔄 Анализирую коэффициенты...")

    try:
        from services.recommendation_service import RecommendationService

        service = RecommendationService(db)

        # Получаем рекомендации по регионам
        by_region = await service.get_recommendations_by_region(
            api_token=token['token'],
            limit_per_region=3
        )

        if not by_region:
            await status_msg.edit_text(
                "📍 <b>Куда везти товар</b>\n\n"
                "К сожалению, сейчас нет доступных слотов.\n"
                "Попробуйте позже или настройте /monitor для уведомлений.",
                parse_mode='HTML'
            )
            return

        # Форматируем
        text = "📍 <b>Куда везти товар — рекомендации</b>\n"
        text += service.format_by_region(by_region)

        # Добавляем лучший слот
        best = await service.get_best_slot(api_token=token['token'])
        if best:
            text += f"\n\n🏆 <b>Лучший слот:</b> {best.warehouse_name}\n"
            text += f"   📅 {best.date.strftime('%d.%m.%Y')} | 💰 {best.coefficient}"

        text += "\n\n<i>Рекомендации основаны на текущих коэффициентах</i>"

        buttons = []
        if best:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🚀 Забронировать {best.warehouse_name}",
                    callback_data=f"book:{best.warehouse_id}:{best.date.isoformat()}:{best.coefficient}"
                )
            ])
        buttons.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_recommend"),
            InlineKeyboardButton(text="📊 Все коэффициенты", callback_data="show_all_coefficients")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await status_msg.edit_text(
            text,
            parse_mode='HTML',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Failed to get recommendations: {e}")
        await status_msg.edit_text(
            "❌ Ошибка при получении рекомендаций.\n\n"
            "Попробуйте позже или проверьте токен командой /token"
        )


@router.callback_query(F.data == "refresh_recommend")
async def callback_refresh_recommend(callback: CallbackQuery):
    """Обновление рекомендаций"""
    await callback.answer("Обновляю...")
    await cmd_recommend(callback.message)


@router.callback_query(F.data == "show_all_coefficients")
async def callback_show_all_coefficients(callback: CallbackQuery):
    """Показать все коэффициенты"""
    await callback.answer()
    await cmd_coefficients(callback.message)


@router.callback_query(F.data == "dismiss")
async def callback_dismiss(callback: CallbackQuery):
    """Скрытие уведомления"""
    await callback.answer("Скрыто")
    try:
        await callback.message.delete()
    except Exception:
        pass
