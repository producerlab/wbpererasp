"""
Клавиатуры для бота перераспределения остатков WB.
"""

from typing import List, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


def get_main_menu() -> ReplyKeyboardMarkup:
    """Главное меню бота"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📊 Коэффициенты"),
                KeyboardButton(text="⚙️ Мониторинг")
            ],
            [
                KeyboardButton(text="🔑 Токены"),
                KeyboardButton(text="📋 История")
            ],
            [
                KeyboardButton(text="❓ Помощь")
            ]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_token_menu(has_tokens: bool = False) -> InlineKeyboardMarkup:
    """Меню управления токенами"""
    buttons = []

    if has_tokens:
        buttons.append([
            InlineKeyboardButton(
                text="📋 Мои токены",
                callback_data="list_tokens"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="➕ Добавить токен",
            callback_data="add_token"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_warehouses_keyboard(
    warehouses: List[Tuple[int, str]],
    selected: List[int] = None,
    page: int = 0,
    per_page: int = 8
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора складов.

    Args:
        warehouses: Список (id, name) складов
        selected: Список выбранных ID
        page: Текущая страница
        per_page: Складов на странице
    """
    if selected is None:
        selected = []

    buttons = []

    # Пагинация
    start = page * per_page
    end = start + per_page
    page_warehouses = warehouses[start:end]

    for wh_id, wh_name in page_warehouses:
        emoji = "✅" if wh_id in selected else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {wh_name}",
                callback_data=f"toggle_wh:{wh_id}"
            )
        ])

    # Навигация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="◀️", callback_data=f"wh_page:{page-1}")
        )

    if end < len(warehouses):
        nav_buttons.append(
            InlineKeyboardButton(text="▶️", callback_data=f"wh_page:{page+1}")
        )

    if nav_buttons:
        buttons.append(nav_buttons)

    # Кнопка готово
    buttons.append([
        InlineKeyboardButton(
            text=f"✅ Готово ({len(selected)} выбрано)",
            callback_data="warehouses_done"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_coefficients_keyboard(
    coefficients: List[float],
    selected: List[float] = None
) -> InlineKeyboardMarkup:
    """Клавиатура выбора коэффициентов"""
    if selected is None:
        selected = [0, 0.5, 1]

    buttons = []

    for coeff in coefficients:
        emoji = "✅" if coeff in selected else "⬜"
        label = "бесплатно" if coeff == 0 else str(coeff)

        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji} {label}",
                callback_data=f"toggle_coeff:{coeff}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="✅ Готово",
            callback_data="coefficients_done"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_booking_confirmation_keyboard(
    warehouse_id: int,
    date_str: str,
    coefficient: float
) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения бронирования"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=f"confirm_book:{warehouse_id}:{date_str}:{coefficient}"
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel_book"
            )
        ]
    ])


def get_booking_result_keyboard(
    success: bool,
    supply_id: str = None,
    warehouse_id: int = None
) -> InlineKeyboardMarkup:
    """Клавиатура после результата бронирования"""
    buttons = []

    if success and supply_id:
        buttons.append([
            InlineKeyboardButton(
                text="📋 Открыть ЛК WB",
                url="https://seller.wildberries.ru/supplies-management/all-supplies"
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                text="❌ Отменить бронь",
                callback_data=f"cancel_booking:{supply_id}"
            )
        ])
    else:
        if warehouse_id:
            buttons.append([
                InlineKeyboardButton(
                    text="🔄 Попробовать снова",
                    callback_data=f"retry_book:{warehouse_id}"
                )
            ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_notification_keyboard(
    warehouse_id: int,
    date_str: str,
    coefficient: float
) -> InlineKeyboardMarkup:
    """Клавиатура для уведомления о коэффициенте"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 Забронировать",
                callback_data=f"book:{warehouse_id}:{date_str}:{coefficient}"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Все коэффициенты",
                callback_data=f"coefficients:{warehouse_id}"
            ),
            InlineKeyboardButton(
                text="🔕 Скрыть",
                callback_data="dismiss"
            )
        ]
    ])
