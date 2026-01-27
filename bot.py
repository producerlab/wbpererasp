"""
WB Redistribution Bot - Бот для перераспределения остатков между складами Wildberries.

Функции:
- Мониторинг коэффициентов приёмки в реальном времени
- Автобронирование слотов при появлении выгодных коэффициентов
- Уведомления в Telegram об изменениях
- Рекомендации "куда везти" на основе географии заказов
"""

import asyncio
import logging
import sys
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties

from config import Config
from db_factory import get_database
from handlers import token_router, supplier_router, monitoring_router, booking_router, redistribution_router
from services.coefficient_monitor import CoefficientMonitor, MonitoringEvent
from services.notification_service import NotificationService
from services.slot_booking import SlotBookingService
from wb_api.client import WBApiClient
from utils.encryption import encrypt_token

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Глобальные объекты
db = None  # Database instance (SQLite or PostgreSQL)
bot: Optional[Bot] = None
monitor: Optional[CoefficientMonitor] = None
notification_service: Optional[NotificationService] = None
booking_service: Optional[SlotBookingService] = None


async def on_coefficient_change(event: MonitoringEvent):
    """Обработчик изменения коэффициентов"""
    change = event.change
    subscriptions = event.subscriptions

    logger.info(
        f"Coefficient change: {change.warehouse_name} "
        f"{change.old_coefficient} -> {change.new_coefficient}"
    )

    # Отправляем уведомления всем подписчикам
    await notification_service.broadcast_to_subscribers(subscriptions, change)

    # Автобронирование для тех, кто включил
    auto_book_subs = [s for s in subscriptions if s.get('auto_book')]
    if auto_book_subs:
        from wb_api.coefficients import Coefficient
        coeff = Coefficient(
            warehouse_id=change.warehouse_id,
            warehouse_name=change.warehouse_name,
            date=change.date,
            coefficient=change.new_coefficient
        )
        results = await booking_service.auto_book_for_subscriptions(
            auto_book_subs, coeff
        )
        for result in results:
            if result and result.success:
                # Уведомляем об автобронировании
                if result.user_id:
                    await notification_service.notify_auto_booking(
                        user_id=result.user_id,
                        result=result,
                        warehouse_name=change.warehouse_name,
                        coefficient=change.new_coefficient
                    )


async def cmd_start(message: Message):
    """Команда /start"""
    user_id = message.from_user.id

    # Регистрируем пользователя
    db.add_user(
        telegram_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    # Проверяем, есть ли у пользователя WB токен
    tokens = db.get_wb_tokens(user_id)
    has_token = len(tokens) > 0

    if has_token:
        # Если токен есть - показываем кнопку Mini App
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть Перераспределение",
                    web_app=WebAppInfo(url=f"{Config.WEBAPP_URL}/webapp/index.html")
                )
            ]
        ])

        await message.answer(
            f"👋 <b>Добро пожаловать в WB Redistribution Bot!</b>\n\n"
            f"Я помогу вам:\n"
            f"📦 Перераспределять остатки между складами\n"
            f"📊 Мониторить коэффициенты приёмки в реальном времени\n"
            f"🚀 Автоматически бронировать выгодные слоты\n"
            f"📍 Получать рекомендации куда отправлять товары\n\n"
            f"<b>Перераспределение:</b>\n"
            f"Нажмите кнопку ниже для открытия Mini App 👇\n\n"
            f"<b>Мониторинг:</b>\n"
            f"/coefficients - текущие коэффициенты\n"
            f"/monitor - настроить мониторинг\n"
            f"/recommend - куда везти товар\n\n"
            f"<b>Бронирование:</b>\n"
            f"/book - забронировать слот\n"
            f"/history - история бронирований\n\n"
            f"/token - управление токенами\n"
            f"/help - справка",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    else:
        # Если токена нет - показываем инструкцию без кнопки
        await message.answer(
            f"👋 <b>Добро пожаловать в WB Redistribution Bot!</b>\n\n"
            f"📦 Перераспределение остатков между складами\n"
            f"📊 Мониторинг коэффициентов приёмки\n"
            f"🚀 Автобронирование выгодных слотов\n\n"
            f"⚠️ <b>Для начала работы необходимо добавить WB API токен:</b>\n\n"
            f"Откройте <a href='https://seller.wildberries.ru/supplier-settings/access-to-api'>ЛК Wildberries</a> → Настройки → Доступ к API\n\n"
            f"Создайте токен с правами:\n"
            f"• <b>Маркетплейс</b>\n"
            f"• <b>Поставки</b>\n"
            f"• <b>Контент</b>\n\n"
            f"Уровень доступа: <b>Чтение и запись</b>\n\n"
            f"<b>Скопируйте токен и отправьте его мне 👇</b>\n"
            f"Я проверю и подключу Mini App автоматически 🚀",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )


async def cmd_help(message: Message):
    """Команда /help"""
    await message.answer(
        "<b>📚 Справка по командам</b>\n\n"
        "<b>Токены:</b>\n"
        "/token - добавить/удалить WB API токен\n\n"
        "<b>Перераспределение:</b>\n"
        "/redistribute - перераспределить остатки между складами\n\n"
        "<b>Мониторинг:</b>\n"
        "/monitor - настроить мониторинг складов\n"
        "/coefficients - текущие коэффициенты\n"
        "/recommend - куда везти товар (рекомендации)\n\n"
        "<b>Бронирование:</b>\n"
        "/book - забронировать слот вручную\n"
        "/history - история бронирований\n\n"
        "<b>Как получить WB API токен:</b>\n"
        "1. ЛК WB → Настройки → Доступ к API\n"
        "2. Создайте токен с правами: <b>Маркетплейс</b>, <b>Поставки</b>, <b>Контент</b>\n"
        "3. Уровень доступа: <b>Чтение и запись</b>\n"
        "4. Отправьте токен боту через /token\n\n"
        "<i>Мониторинг проверяет коэффициенты каждые "
        f"{Config.COEFFICIENT_POLL_INTERVAL} секунд</i>",
        parse_mode=ParseMode.HTML
    )


async def cmd_stats(message: Message):
    """Статистика бота (для админов)"""
    if message.from_user.id not in Config.ADMIN_IDS:
        return

    total_stats = db.get_total_stats()
    monitor_stats = monitor.get_stats() if monitor else {}

    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: {total_stats.get('total_users', 0)}\n"
        f"📝 Запросов: {total_stats.get('total_requests', 0)}\n\n"
        f"<b>Мониторинг:</b>\n"
        f"🔄 Статус: {'Работает' if monitor_stats.get('running') else 'Остановлен'}\n"
        f"📊 Опросов: {monitor_stats.get('polls_count', 0)}\n"
        f"🔔 Изменений: {monitor_stats.get('changes_detected', 0)}\n"
        f"📦 Кэш коэфф.: {monitor_stats.get('cached_coefficients', 0)}",
        parse_mode=ParseMode.HTML
    )


async def handle_text_message(message: Message):
    """
    Обработчик обычных текстовых сообщений.
    Если у пользователя нет токена - пытается распознать WB токен автоматически.
    """
    user_id = message.from_user.id
    text = message.text.strip()

    # Проверяем, есть ли у пользователя токен
    tokens = db.get_wb_tokens(user_id)

    # Если токен уже есть - игнорируем (пусть другие handlers обрабатывают)
    if len(tokens) > 0:
        return

    # Если нет токена и текст похож на WB токен (длинный base64)
    if len(text) < 50:
        return  # Слишком короткий для токена

    # Удаляем сообщение с токеном из чата (безопасность)
    try:
        await message.delete()
    except Exception as e:
        logger.error(f"Failed to delete token message: {e}")
        await message.answer(
            "⚠️ <b>ВНИМАНИЕ!</b>\n\n"
            "Не удалось удалить ваше сообщение с токеном.\n"
            "Пожалуйста, удалите его вручную для безопасности!",
            parse_mode=ParseMode.HTML
        )

    # Проверяем токен
    status_msg = await message.answer("🔄 Проверяю токен...")

    try:
        async with WBApiClient(text) as client:
            is_valid = await client.check_token()
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        is_valid = False

    if not is_valid:
        await status_msg.edit_text(
            "❌ <b>Токен невалиден</b>\n\n"
            "Убедитесь, что:\n"
            "• Токен скопирован полностью\n"
            "• Не истёк срок действия\n"
            "• Есть права: <b>Маркетплейс, Поставки, Контент</b>\n"
            "• Уровень доступа: <b>Чтение и запись</b>\n\n"
            "Попробуйте создать новый токен в ЛК WB и отправьте снова.",
            parse_mode=ParseMode.HTML
        )
        return

    # Токен валидный - добавляем
    encrypted = encrypt_token(text)
    name = "Основной"
    token_id = db.add_wb_token(user_id, encrypted, name)

    if not token_id:
        await status_msg.edit_text(
            "❌ Этот токен уже добавлен.\n\n"
            "Используйте /token для управления токенами."
        )
        return

    # Создаём поставщика автоматически
    supplier_id = db.add_supplier(
        user_id=user_id,
        name=name,
        token_id=token_id
    )

    # Показываем кнопку Mini App
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📦 Открыть Перераспределение",
                web_app=WebAppInfo(url=f"{Config.WEBAPP_URL}/webapp/index.html")
            )
        ]
    ])

    await status_msg.edit_text(
        f"✅ <b>Токен успешно добавлен!</b>\n\n"
        f"🎉 Mini App готов к работе!\n\n"
        f"Теперь вы можете:\n"
        f"📦 Открыть Mini App (кнопка ниже)\n"
        f"📊 /monitor - мониторинг коэффициентов\n"
        f"📈 /coefficients - текущие коэффициенты\n"
        f"🎯 /book - забронировать слот\n\n"
        f"<i>Нажмите кнопку ниже для открытия перераспределения 👇</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )


async def start_monitoring():
    """Запускает фоновый мониторинг коэффициентов"""
    global monitor

    # Для мониторинга нужен системный токен
    # В реальном приложении можно использовать токен первого админа
    # или отдельный системный токен
    system_token = Config.WB_SYSTEM_TOKEN if hasattr(Config, 'WB_SYSTEM_TOKEN') else None

    if not system_token:
        logger.warning(
            "WB_SYSTEM_TOKEN not configured. "
            "Monitoring will use user tokens on demand."
        )
        return

    monitor = CoefficientMonitor(db, system_token)
    monitor.on_change(on_coefficient_change)
    await monitor.start()


async def main():
    """Главная функция запуска бота"""
    global db, bot, notification_service, booking_service

    # Валидация конфигурации
    Config.validate()
    logger.info("Configuration validated")
    logger.info(Config.get_summary())

    # Инициализация БД
    db = get_database()
    logger.info("Database initialized")

    # Инициализация бота
    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # Инициализация сервисов
    notification_service = NotificationService(bot)
    booking_service = SlotBookingService(db)

    # Диспетчер
    dp = Dispatcher()

    # Регистрация handlers
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_stats, Command("stats"))

    # Подключение роутеров
    dp.include_router(token_router)
    dp.include_router(supplier_router)
    dp.include_router(monitoring_router)
    dp.include_router(booking_router)
    dp.include_router(redistribution_router)

    # Обработчик обычных текстовых сообщений (регистрируем последним как catch-all)
    dp.message.register(handle_text_message)

    logger.info("Handlers registered")

    # Запуск мониторинга (опционально)
    # await start_monitoring()

    # Запуск бота
    print("\n✅ Бот успешно запущен!")
    print(f"🤖 Бот: @mpbizai_bot")
    print(f"👤 Админ: {Config.ADMIN_IDS}")
    print("\n📝 Команды бота:")
    print("   /start - начало работы")
    print("   /token - добавить WB API токен")
    print("   /redistribute - перераспределить остатки")
    print("   /coefficients - текущие коэффициенты")
    print("   /monitor - настроить мониторинг")
    print("   /book - забронировать слот")
    print("\n⏳ Ожидание сообщений... (Ctrl+C для остановки)\n")
    print("=" * 50)

    logger.info("Starting bot...")
    try:
        await dp.start_polling(bot)
    finally:
        if monitor:
            await monitor.stop()
        await bot.session.close()


if __name__ == "__main__":
    print("=" * 50)
    print("🚀 WB Redistribution Bot запускается...")
    print("=" * 50)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Бот остановлен пользователем")
        logger.info("Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        logger.error(f"Bot crashed: {e}", exc_info=True)
        sys.exit(1)
