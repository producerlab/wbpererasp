"""
Инлайн-клавиатуры для перераспределения остатков.

Клавиатуры:
- Выбор артикула (с пагинацией)
- Выбор склада-источника
- Выбор склада-назначения
- Ввод количества
- Подтверждение
"""

from typing import List, Dict, Optional, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from wb_api.stocks import StockItem, StocksByWarehouse
from wb_api.warehouses import WarehousesAPI


def build_sku_selection_keyboard(
    stocks_by_sku: Dict[str, StocksByWarehouse],
    page: int = 0,
    items_per_page: int = 5
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора артикула с пагинацией.

    Args:
        stocks_by_sku: Остатки сгруппированные по SKU
        page: Текущая страница
        items_per_page: Элементов на странице

    Returns:
        InlineKeyboardMarkup
    """
    buttons = []

    # Сортируем по количеству (больше = выше)
    sorted_skus = sorted(
        stocks_by_sku.items(),
        key=lambda x: x[1].total_quantity,
        reverse=True
    )

    # Пагинация
    total_pages = max(1, (len(sorted_skus) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_items = sorted_skus[start_idx:end_idx]

    # Кнопки артикулов
    for sku, stock_data in page_items:
        name = stock_data.product_name[:25] if stock_data.product_name else sku[:25]
        total = stock_data.total_quantity
        warehouses_count = len(stock_data.warehouses)

        label = f"{name} ({total} шт, {warehouses_count} скл.)"

        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"redist_sku:{sku}"
            )
        ])

    # Навигация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="◀️", callback_data=f"redist_page:{page - 1}")
        )

    nav_buttons.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop")
    )

    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="▶️", callback_data=f"redist_page:{page + 1}")
        )

    if nav_buttons:
        buttons.append(nav_buttons)

    # Поиск и отмена
    buttons.append([
        InlineKeyboardButton(text="🔍 Поиск", callback_data="redist_search"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="redist_cancel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_source_warehouse_keyboard(
    stocks: List[StockItem],
    sku: str
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора склада-источника.

    Args:
        stocks: Остатки товара на разных складах
        sku: Артикул

    Returns:
        InlineKeyboardMarkup
    """
    buttons = []

    # Сортируем по количеству
    sorted_stocks = sorted(stocks, key=lambda x: x.quantity, reverse=True)

    # Получаем названия складов
    warehouse_names = WarehousesAPI.POPULAR_WAREHOUSES

    for stock in sorted_stocks:
        if stock.quantity <= 0:
            continue

        wh_name = warehouse_names.get(stock.warehouse_id, {}).get('name')
        if not wh_name:
            wh_name = stock.warehouse_name or f"Склад {stock.warehouse_id}"

        label = f"📦 {wh_name} ({stock.quantity} шт)"

        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"redist_src:{stock.warehouse_id}:{sku}"
            )
        ])

    # Назад и отмена
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="redist_back_sku"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="redist_cancel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_target_warehouse_keyboard(
    source_warehouse_id: int,
    sku: str
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора склада-назначения.

    Args:
        source_warehouse_id: ID склада-источника
        sku: Артикул

    Returns:
        InlineKeyboardMarkup
    """
    buttons = []

    # Популярные склады (исключаем источник)
    warehouse_names = WarehousesAPI.POPULAR_WAREHOUSES

    for wh_id, wh_info in warehouse_names.items():
        if wh_id == source_warehouse_id:
            continue

        region = wh_info.get('region', '')
        name = wh_info.get('name', f'Склад {wh_id}')
        label = f"📍 {name}"
        if region:
            label += f" ({region[:3]})"

        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"redist_dst:{wh_id}:{source_warehouse_id}:{sku}"
            )
        ])

    # Назад и отмена
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"redist_back_src:{sku}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="redist_cancel")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_quantity_keyboard(
    available: int,
    current: int,
    sku: str,
    source_id: int,
    target_id: int
) -> InlineKeyboardMarkup:
    """
    Клавиатура ввода количества.

    Args:
        available: Доступное количество
        current: Текущее выбранное количество
        sku: Артикул
        source_id: ID склада-источника
        target_id: ID склада-назначения

    Returns:
        InlineKeyboardMarkup
    """
    buttons = []
    base_data = f"{sku}:{source_id}:{target_id}"

    # Заголовок с текущим количеством (info кнопка)
    buttons.append([
        InlineKeyboardButton(
            text=f"📦 Доступно: {available} | Выбрано: {current}",
            callback_data="noop"
        )
    ])

    # Кнопки +
    plus_buttons = []
    for delta in [1, 10, 50, 100]:
        if current + delta <= available:
            plus_buttons.append(
                InlineKeyboardButton(
                    text=f"+{delta}",
                    callback_data=f"redist_qty_add:{delta}:{base_data}"
                )
            )

    if current < available:
        plus_buttons.append(
            InlineKeyboardButton(
                text="MAX",
                callback_data=f"redist_qty_max:{base_data}"
            )
        )

    if plus_buttons:
        buttons.append(plus_buttons)

    # Кнопки -
    minus_buttons = []
    for delta in [1, 10, 50, 100]:
        if current - delta >= 0:
            minus_buttons.append(
                InlineKeyboardButton(
                    text=f"-{delta}",
                    callback_data=f"redist_qty_sub:{delta}:{base_data}"
                )
            )

    if current > 0:
        minus_buttons.append(
            InlineKeyboardButton(
                text="MIN",
                callback_data=f"redist_qty_min:{base_data}"
            )
        )

    if minus_buttons:
        buttons.append(minus_buttons)

    # Ввод вручную
    buttons.append([
        InlineKeyboardButton(
            text="✏️ Ввести число",
            callback_data=f"redist_qty_input:{base_data}"
        )
    ])

    # Далее и отмена
    action_buttons = []
    if current > 0:
        action_buttons.append(
            InlineKeyboardButton(
                text="✅ Далее",
                callback_data=f"redist_qty_confirm:{current}:{base_data}"
            )
        )

    action_buttons.append(
        InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=f"redist_back_dst:{source_id}:{sku}"
        )
    )
    action_buttons.append(
        InlineKeyboardButton(text="❌ Отмена", callback_data="redist_cancel")
    )

    buttons.append(action_buttons)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_confirmation_keyboard(
    sku: str,
    source_id: int,
    target_id: int,
    quantity: int
) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения перераспределения.

    Args:
        sku: Артикул
        source_id: ID склада-источника
        target_id: ID склада-назначения
        quantity: Количество

    Returns:
        InlineKeyboardMarkup
    """
    data = f"{sku}:{source_id}:{target_id}:{quantity}"

    buttons = [
        [
            InlineKeyboardButton(
                text="✅ Подтвердить",
                callback_data=f"redist_confirm:{data}"
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Изменить кол-во",
                callback_data=f"redist_back_qty:{sku}:{source_id}:{target_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="◀️ Начать заново",
                callback_data="redist_restart"
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="redist_cancel"
            )
        ]
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_result_keyboard(
    success: bool,
    supply_id: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    Клавиатура результата операции.

    Args:
        success: Успешно ли
        supply_id: ID созданной поставки

    Returns:
        InlineKeyboardMarkup
    """
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
            text="🔄 Новое перераспределение",
            callback_data="redist_restart"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="main_menu"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
