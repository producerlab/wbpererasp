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
from handlers.token_management import TokenStates
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
        webapp_url = Config.WEBAPP_URL.rstrip('/')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Открыть Перераспределение",
                    web_app=WebAppInfo(url=f"{webapp_url}/webapp/index.html")
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


async def handle_token_auto(message: Message, state: FSMContext):
    """
    Автоматический обработчик WB API токена.
    Срабатывает когда пользователь отправляет длинную строку (>50 символов).
    """
    text = message.text.strip()
    user_id = message.from_user.id

    # Проверяем, что это похоже на токен (длинная строка)
    if len(text) < 50:
        return  # Слишком короткое - игнорируем

    # Проверяем, есть ли уже токен у пользователя
    tokens = db.get_wb_tokens(user_id)
    if len(tokens) > 0:
        # Токен уже есть - игнорируем (пусть другие handlers обрабатывают)
        return

    # Удаляем сообщение с токеном (безопасность)
    try:
        await message.delete()
    except Exception as e:
        logger.error(f"Failed to delete token message: {e}")

    # Проверяем и сохраняем токен
    status_msg = await message.answer("🔄 Проверяю токен и получаю информацию о магазине...")
    logger.info(f"Auto-processing token for user {user_id}, length: {len(text)}")

    supplier_name = "Мой магазин"  # Дефолт

    try:
        async with WBApiClient(text) as client:
            is_valid = await client.check_token()

            if not is_valid:
                await status_msg.edit_text(
                    "❌ Токен невалиден.\n\n"
                    "Убедитесь, что токен:\n"
                    "• Скопирован полностью\n"
                    "• Не истёк срок действия\n"
                    "• Есть права: Маркетплейс, Поставки\n\n"
                    "Попробуйте ещё раз или /token для помощи.",
                    parse_mode=ParseMode.HTML
                )
                return

            # Получаем название автоматически
            supplier_info = await client.get_supplier_info()
            if supplier_info and supplier_info.get("name"):
                supplier_name = supplier_info["name"]
                logger.info(f"Got supplier name: {supplier_name}")

    except Exception as e:
        logger.error(f"Token validation failed: {e}", exc_info=True)

    # Сохраняем токен
    try:
        encrypted = encrypt_token(text)
        token_id = db.add_wb_token(user_id, encrypted, supplier_name)

        if not token_id:
            await status_msg.edit_text("❌ Этот токен уже добавлен.")
            return

        # Добавляем поставщика
        supplier_id = db.add_supplier(user_id=user_id, name=supplier_name, token_id=token_id)
        logger.info(f"Token {token_id} and supplier {supplier_id} added successfully")

        # Показываем кнопку Mini App
        webapp_url = Config.WEBAPP_URL
        if webapp_url and webapp_url.startswith("https://"):
            full_url = f"{webapp_url.rstrip('/')}/webapp/index.html"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📦 Открыть Перераспределение",
                    web_app=WebAppInfo(url=full_url)
                )]
            ])
            await status_msg.edit_text(
                f"✅ <b>Токен успешно добавлен!</b>\n\n"
                f"📛 Магазин: {supplier_name}\n"
                f"🆔 ID: {token_id}\n\n"
                f"Теперь откройте Mini App для перераспределения остатков:",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            await status_msg.edit_text(
                f"✅ <b>Токен добавлен!</b>\n\n"
                f"📛 Магазин: {supplier_name}\n"
                f"🆔 ID: {token_id}\n\n"
                f"Используйте /redistribute",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Failed to save token: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ Ошибка сохранения токена.\n\n"
            f"Попробуйте ещё раз или /token для помощи."
        )


async def main():
    """Главная функция запуска бота"""
    global db, bot

    # 🚨 RAILWAY DEPLOYMENT CHECK
    try:
        import os
        test_file = os.path.join(os.path.dirname(__file__), 'RAILWAY_TEST.txt')
        if os.path.exists(test_file):
            with open(test_file, 'r') as f:
                content = f.read()
                logger.warning("=" * 60)
                logger.warning("🚨 RAILWAY DEPLOYMENT CHECK:")
                logger.warning(content)
                logger.warning("=" * 60)
    except Exception as e:
        logger.error(f"Failed to read RAILWAY_TEST.txt: {e}")

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

    # Подключение роутеров
    dp.include_router(token_router)
    dp.include_router(supplier_router)
    dp.include_router(redistribution_router)

    # Автоматический обработчик токенов (регистрируем ПОСЛЕДНИМ как catch-all)
    dp.message.register(handle_token_auto)

    logger.info("Handlers registered")

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
