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
from handlers import token_router, supplier_router, redistribution_router
# Мониторинг и бронирование отключены
# from handlers import monitoring_router, booking_router
from handlers.token_management import TokenStates
# Сервисы мониторинга отключены
# from services.coefficient_monitor import CoefficientMonitor, MonitoringEvent
# from services.notification_service import NotificationService
# from services.slot_booking import SlotBookingService
from wb_api.client import WBApiClient
from aiogram.fsm.context import FSMContext

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
# Сервисы мониторинга отключены
# monitor: Optional[CoefficientMonitor] = None
# notification_service: Optional[NotificationService] = None
# booking_service: Optional[SlotBookingService] = None


# МОНИТОРИНГ ОТКЛЮЧЕН
# async def on_coefficient_change(event: MonitoringEvent):
#     """
#     Обработчик изменения коэффициентов.
#
#     Фильтрует незначимые изменения и применяет cooldown.
#     """
#     pass


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
            f"📦 Перераспределять остатки между складами\n\n"
            f"<b>Команды:</b>\n"
            f"📦 /redistribute - открыть форму перераспределения\n"
            f"🏪 /suppliers - управление поставщиками\n"
            f"🔑 /token - управление токенами\n"
            f"❓ /help - справка",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    else:
        # Если токена нет - показываем инструкцию без кнопки
        await message.answer(
            f"👋 <b>Добро пожаловать в WB Redistribution Bot!</b>\n\n"
            f"📦 <b>Перераспределение остатков между складами</b>\n\n"
            f"⚠️ <b>Для начала работы необходимо добавить WB API токен:</b>\n\n"
            f"Откройте <a href='https://seller.wildberries.ru/supplier-settings/access-to-api'>ЛК Wildberries</a> → Настройки → Доступ к API\n\n"
            f"Создайте токен с правами:\n"
            f"• <b>Маркетплейс</b>\n"
            f"• <b>Поставки</b>\n"
            f"• <b>Контент</b>\n\n"
            f"Уровень доступа: <b>Чтение и запись</b>\n\n"
            f"<b>Скопируйте токен и отправьте его мне 👇</b>\n"
            f"Я проверю и подключу автоматически 🚀",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )


async def cmd_help(message: Message):
    """Команда /help"""
    await message.answer(
        "<b>📚 Справка по командам</b>\n\n"
        "<b>Токены:</b>\n"
        "/token - добавить/удалить WB API токен\n\n"
        "<b>Поставщики:</b>\n"
        "/suppliers - управление поставщиками (переименование, удаление)\n\n"
        "<b>Перераспределение:</b>\n"
        "/redistribute - перераспределить остатки между складами\n\n"
        "<b>Как получить WB API токен:</b>\n"
        "1. ЛК WB → Настройки → Доступ к API\n"
        "2. Создайте токен с правами: <b>Маркетплейс</b>, <b>Поставки</b>, <b>Контент</b>\n"
        "3. Уровень доступа: <b>Чтение и запись</b>\n"
        "4. Отправьте токен боту через /token",
        parse_mode=ParseMode.HTML
    )


async def cmd_stats(message: Message):
    """Статистика бота (для админов)"""
    if message.from_user.id not in Config.ADMIN_IDS:
        return

    total_stats = db.get_total_stats()

    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: {total_stats.get('total_users', 0)}\n"
        f"📝 Запросов: {total_stats.get('total_requests', 0)}\n\n"
        f"<b>Мониторинг:</b> Отключен",
        parse_mode=ParseMode.HTML
    )


async def handle_text_message(message: Message, state: FSMContext):
    """
    Обработчик обычных текстовых сообщений.
    Автоматически распознаёт и проверяет WB API токен.
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

    # Сохраняем токен во временное хранилище
    await state.update_data(token=text)

    await status_msg.edit_text(
        "✅ Токен валиден!\n\n"
        "Введите название компании/ИП для этого токена:\n"
        "(например: <b>ИП Хоснуллин</b> или <b>ООО Мегаторг</b>)\n\n"
        "Или отправьте /skip для имени по умолчанию.",
        parse_mode=ParseMode.HTML
    )
    await state.set_state(TokenStates.waiting_for_name)


# МОНИТОРИНГ ОТКЛЮЧЕН
# async def start_monitoring():
#     """
#     Запускает фоновый мониторинг коэффициентов - ОТКЛЮЧЕНО
#     """
#     pass


async def main():
    """Главная функция запуска бота"""
    global db, bot

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

    # Инициализация сервисов - мониторинг отключен
    # notification_service = NotificationService(
    #     bot,
    #     cooldown_minutes=Config.NOTIFICATION_COOLDOWN_MINUTES
    # )
    # booking_service = SlotBookingService(db)

    # Диспетчер
    dp = Dispatcher()

    # Регистрация handlers
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_stats, Command("stats"))

    # Подключение роутеров
    dp.include_router(token_router)
    dp.include_router(supplier_router)
    # dp.include_router(monitoring_router)  # ОТКЛЮЧЕН
    # dp.include_router(booking_router)     # ОТКЛЮЧЕН
    dp.include_router(redistribution_router)

    # Обработчик обычных текстовых сообщений (регистрируем последним как catch-all)
    dp.message.register(handle_text_message)

    logger.info("Handlers registered")

    # Запуск мониторинга коэффициентов - ОТКЛЮЧЕН
    # await start_monitoring()
    logger.info("Coefficient monitoring is DISABLED (commented out)")

    # Запуск бота
    print("\n✅ Бот успешно запущен!")
    print(f"🤖 Бот: @mpbizai_bot")
    print(f"👤 Админ: {Config.ADMIN_IDS}")
    print("\n📝 Команды бота:")
    print("   /start - начало работы")
    print("   /token - добавить WB API токен")
    print("   /suppliers - управление поставщиками")
    print("   /redistribute - перераспределить остатки")
    print("\n⏳ Ожидание сообщений... (Ctrl+C для остановки)\n")
    print("=" * 50)

    logger.info("Starting bot...")
    try:
        await dp.start_polling(bot)
    finally:
        # if monitor:
        #     await monitor.stop()
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
