"""
WB Redistribution Bot - Бот для перераспределения остатков между складами Wildberries.

Функции:
- Авторизация через SMS (номер телефона)
- Перераспределение остатков между складами
- Оплата через YooKassa
"""

import asyncio
import logging
import sys
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties

from config import Config
from db_factory import get_database
from handlers import redistribution_router, browser_auth_router
# from handlers import payment_router  # Временно отключено до реализации YooKassa
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


async def cmd_start(message: Message, state: FSMContext):
    """Команда /start - начало работы с ботом через SMS авторизацию"""
    user_id = message.from_user.id
    logger.info(f"[START] User {user_id} pressed /start")

    # Сначала очищаем любые предыдущие состояния FSM
    await state.clear()
    logger.info(f"[START] User {user_id} FSM state cleared")

    try:
        # Регистрируем пользователя
        db.add_user(
            telegram_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name
        )
        logger.info(f"[START] User {user_id} registered in DB")

        # Проверяем, есть ли активная browser session
        session = db.get_browser_session(user_id)
        logger.info(f"[START] User {user_id} session: {bool(session)}")

        if session:
            # Если сессия есть - показываем кнопку Mini App
            webapp_url = Config.WEBAPP_URL
            logger.info(f"[START] WEBAPP_URL: {webapp_url}")

            if webapp_url and webapp_url.startswith("https://"):
                full_url = f"{webapp_url.rstrip('/')}/webapp/index.html"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📦 Открыть Перераспределение",
                        web_app=WebAppInfo(url=full_url)
                    )],
                    [InlineKeyboardButton(
                        text="🔄 Войти заново",
                        callback_data="reauth"
                    )]
                ])

                supplier_info = session.get('supplier_name', 'Ваш магазин')
                phone = session.get('phone', 'не указан')

                await message.answer(
                    f"👋 <b>Добро пожаловать в WB Redistribution Bot!</b>\n\n"
                    f"✅ Вы уже авторизованы!\n\n"
                    f"📛 Магазин: <b>{supplier_info}</b>\n"
                    f"📱 Телефон: <code>{phone}</code>\n\n"
                    f"Нажмите кнопку ниже, чтобы открыть панель перераспределения:\n\n"
                    f"<b>Команды:</b>\n"
                    f"/balance - проверить баланс\n"
                    f"/help - справка",
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"[START] User {user_id} - sent authorized message with Mini App")
            else:
                await message.answer(
                    f"✅ Вы авторизованы, но WEBAPP_URL не настроен.\n\n"
                    f"Магазин: {session.get('supplier_name', 'N/A')}\n"
                    f"Телефон: {session.get('phone', 'N/A')}"
                )
                logger.info(f"[START] User {user_id} - sent authorized message (no HTTPS)")
        else:
            # Если сессии нет - запускаем SMS авторизацию
            from handlers.browser_auth import AuthStates

            await message.answer(
                f"👋 <b>Добро пожаловать в WB Redistribution Bot!</b>\n\n"
                f"📦 <b>Автоматическое перераспределение остатков между складами Wildberries</b>\n\n"
                f"Для работы бота нужен доступ к вашему личному кабинету WB.\n\n"
                f"🔐 <b>Авторизация через SMS</b>\n\n"
                f"📱 Отправьте номер телефона в формате:\n"
                f"<code>+79991234567</code> или <code>89991234567</code>\n\n"
                f"⚠️ На этот номер придет SMS код от Wildberries.",
                parse_mode=ParseMode.HTML
            )
            logger.info(f"[START] User {user_id} - sent welcome message, waiting for phone")

            # Устанавливаем состояние ожидания телефона
            await state.set_state(AuthStates.waiting_phone)

    except Exception as e:
        logger.error(f"[START] Error for user {user_id}: {e}", exc_info=True)
        try:
            await message.answer(
                f"⚠️ Произошла ошибка при запуске.\n\n"
                f"Попробуйте позже или обратитесь к администратору.\n"
                f"Код: {type(e).__name__}"
            )
        except Exception as send_error:
            logger.error(f"[START] Failed to send error message: {send_error}")


async def cmd_help(message: Message):
    """Команда /help"""
    await message.answer(
        "<b>📚 Справка по командам</b>\n\n"
        "<b>Авторизация:</b>\n"
        "/start - начать работу / авторизация\n"
        "/logout - выйти из сессии\n\n"
        "<b>Перераспределение:</b>\n"
        "/redistribute - перераспределить остатки между складами\n\n"
        "<b>Оплата:</b>\n"
        "/balance - проверить баланс\n"
        "/pay - пополнить баланс\n"
        "/history - история операций\n\n"
        "<b>Как это работает:</b>\n"
        "1. Авторизуйтесь через /start (номер телефона + SMS)\n"
        "2. Пополните баланс через /pay\n"
        "3. Создайте заявку на перемещение через /redistribute\n"
        "4. Бот автоматически выполнит перемещение при появлении квот",
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


async def callback_reauth(callback: CallbackQuery, state: FSMContext):
    """Callback для кнопки 'Войти заново'"""
    user_id = callback.from_user.id

    # Деактивируем текущую сессию
    db.invalidate_browser_session(user_id)

    from handlers.browser_auth import AuthStates

    await callback.message.edit_text(
        "🔄 Сессия сброшена.\n\n"
        "📱 Отправьте номер телефона в формате:\n"
        "<code>+79991234567</code> или <code>89991234567</code>\n\n"
        "⚠️ На этот номер придет SMS код от Wildberries.",
        parse_mode=ParseMode.HTML
    )

    # Устанавливаем состояние ожидания телефона
    await state.set_state(AuthStates.waiting_phone)
    await callback.answer()


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

    # Диспетчер
    dp = Dispatcher()

    # Регистрация handlers
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_stats, Command("stats"))

    # Регистрация callback handlers
    dp.callback_query.register(callback_reauth, F.data == "reauth")

    # Подключение роутеров
    dp.include_router(redistribution_router)
    dp.include_router(browser_auth_router)
    # dp.include_router(payment_router)  # Временно отключено до реализации YooKassa

    logger.info("Handlers registered")

    # Запуск бота
    print("\n✅ Бот успешно запущен!")
    print(f"🤖 Бот: @mpbizai_bot")
    print(f"👤 Админ: {Config.ADMIN_IDS}")
    print("\n📝 Команды бота:")
    print("   /start - начало работы (авторизация через SMS)")
    print("   /redistribute - перераспределить остатки")
    print("   /balance - проверить баланс")
    print("\n⏳ Ожидание сообщений... (Ctrl+C для остановки)\n")
    print("=" * 50)

    logger.info("Starting bot...")

    # Retry логика для борьбы с TelegramConflictError
    max_retries = 5
    retry_delay = 10  # секунд

    for attempt in range(max_retries):
        try:
            logger.info(f"Attempt {attempt + 1}/{max_retries} to start polling...")
            await dp.start_polling(bot)
            break  # Если успешно - выходим из цикла
        except Exception as e:
            error_msg = str(e).lower()
            if 'conflict' in error_msg or 'terminated by other getupdates' in error_msg:
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️  TelegramConflictError on attempt {attempt + 1}")
                    logger.warning(f"Old bot instance still running. Waiting {retry_delay}s before retry...")
                    await bot.session.close()
                    await asyncio.sleep(retry_delay)
                    # Пересоздаем bot для нового соединения
                    bot = Bot(
                        token=Config.BOT_TOKEN,
                        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
                    )
                    continue
                else:
                    logger.error("❌ TelegramConflictError: Failed after all retries!")
                    logger.error("Old bot deployment is stuck. Manual intervention needed.")
                    await bot.session.close()
                    sys.exit(1)
            else:
                # Другая ошибка - пробрасываем дальше
                raise

    # Cleanup
    try:
        await bot.session.close()
    except:
        pass


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
