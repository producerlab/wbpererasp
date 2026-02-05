"""
WB Redistribution Bot - Бот для перераспределения остатков между складами Wildberries.

Функции:
- Авторизация через SMS (номер телефона)
- Перераспределение остатков между складами
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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

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

        # Проверяем админа
        is_admin = user_id in Config.ADMIN_IDS
        logger.info(f"[START] Admin check: user_id={user_id}, is_admin={is_admin}, ADMIN_IDS={Config.ADMIN_IDS}")
        if is_admin:
            logger.info(f"[START] ADMIN user detected: {user_id}")

        # Для админов сразу показываем Mini App (без проверки browser_session)
        if is_admin:
            webapp_url = Config.WEBAPP_URL
            logger.info(f"[START] ADMIN mode - showing Mini App. WEBAPP_URL: {webapp_url}")

            if webapp_url and webapp_url.startswith("https://"):
                full_url = f"{webapp_url.rstrip('/')}/webapp/index.html"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📦 Открыть Перераспределение",
                        web_app=WebAppInfo(url=full_url)
                    )],
                    [InlineKeyboardButton(
                        text="📥 Импорт cookies",
                        callback_data="import_cookies"
                    )],
                    [InlineKeyboardButton(
                        text="🔄 Войти заново",
                        callback_data="reauth"
                    )]
                ])

                await message.answer(
                    f"👋 <b>Добро пожаловать, Администратор!</b>\n\n"
                    f"✅ Вы работаете в <b>режиме администратора</b>\n\n"
                    f"🎭 <b>DEMO режим:</b>\n"
                    f"• Поставщики создаются автоматически (тестовые данные)\n"
                    f"• SMS авторизация не требуется\n"
                    f"• Доступны все функции панели\n\n"
                    f"Нажмите кнопку ниже, чтобы открыть панель перераспределения:\n\n"
                    f"<b>Команды:</b>\n"
                    f"/help - справка",
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"[START] ADMIN {user_id} - sent admin message with Mini App")
                return  # Выходим, не проверяем browser_session
            else:
                await message.answer(
                    f"✅ Вы администратор, но WEBAPP_URL не настроен.\n\n"
                    f"Проверьте конфигурацию WEBAPP_URL (должен начинаться с https://)"
                )
                logger.info(f"[START] ADMIN {user_id} - WEBAPP_URL not configured")
                return

        # Проверяем, есть ли активная browser session (только для обычных пользователей)
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
                        text="🔃 Обновить профили",
                        callback_data="refresh_profiles"
                    )],
                    [InlineKeyboardButton(
                        text="📥 Импорт cookies",
                        callback_data="import_cookies"
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
            # Если сессии нет - показываем варианты авторизации
            from handlers.browser_auth import AuthStates

            # Кнопка для импорта cookies
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📧 Импорт cookies из браузера",
                    callback_data="import_cookies"
                )]
            ])

            await message.answer(
                f"👋 <b>Добро пожаловать в WB Redistribution Bot!</b>\n\n"
                f"📦 <b>Автоматическое перераспределение остатков между складами Wildberries</b>\n\n"
                f"<b>Почему нужна авторизация?</b>\n"
                f"Wildberries не предоставляет API для работы с остатками. "
                f"Поэтому бот работает через ваш личный кабинет — так же, как вы делали бы это вручную.\n\n"
                f"🔐 <b>Безопасность</b>\n"
                f"• Мы НЕ храним ваш пароль — только одноразовый SMS-код\n"
                f"• Сессия привязана только к вашему Telegram\n"
                f"• Вы можете выйти в любой момент командой /logout\n\n"
                f"<b>Способы авторизации:</b>\n"
                f"1️⃣ <b>SMS авторизация</b> - отправьте номер телефона в формате:\n"
                f"   <code>+79991234567</code> или <code>89991234567</code>\n\n"
                f"2️⃣ <b>Импорт cookies</b> - авторизуйтесь в браузере и импортируйте cookies\n"
                f"   (используйте кнопку ниже)\n\n"
                f"💡 <b>Совет:</b> Можете использовать номер менеджера или отдельную SIM-карту — "
                f"так ваш основной номер останется в стороне.\n\n"
                f"⚠️ SMS придёт от <b>Wildberries</b>",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            logger.info(f"[START] User {user_id} - sent welcome message with import_cookies button")

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
        "<b>Как это работает:</b>\n"
        "1. Авторизуйтесь через /start (номер телефона + SMS)\n"
        "2. Создайте заявку на перемещение через /redistribute\n"
        "3. Бот автоматически выполнит перемещение при появлении квот",
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
        f"<b>Перераспределение:</b> Активно",
        parse_mode=ParseMode.HTML
    )


class CookieImportStates(StatesGroup):
    """Состояния для импорта cookies"""
    waiting_cookies = State()


async def callback_reauth(callback: CallbackQuery, state: FSMContext):
    """Callback для кнопки 'Войти заново'"""
    user_id = callback.from_user.id

    # Деактивируем текущую сессию
    db.invalidate_browser_session(user_id)

    from handlers.browser_auth import AuthStates

    await callback.message.edit_text(
        "🔄 Сессия сброшена.\n\n"
        "📱 <b>Отправьте номер телефона</b> в формате:\n"
        "<code>+79991234567</code> или <code>89991234567</code>\n\n"
        "💡 Можете использовать номер менеджера, если не хотите указывать свой.\n\n"
        "⚠️ SMS придёт от <b>Wildberries</b>",
        parse_mode=ParseMode.HTML
    )

    # Устанавливаем состояние ожидания телефона
    await state.set_state(AuthStates.waiting_phone)
    await callback.answer()


async def callback_import_cookies(callback: CallbackQuery, state: FSMContext):
    """Callback для кнопки 'Импорт cookies'"""
    await callback.message.edit_text(
        "📥 <b>Импорт cookies из браузера</b>\n\n"
        "1. Зайдите на <code>seller.wildberries.ru</code> в браузере\n"
        "2. Убедитесь что вы залогинены\n"
        "3. Установите расширение Cookie-Editor:\n"
        "   • Chrome: https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm\n"
        "   • Firefox: https://addons.mozilla.org/firefox/addon/cookie-editor/\n"
        "4. Кликните на иконку расширения\n"
        "5. Нажмите кнопку Export (📋)\n"
        "6. Скопируйте JSON\n"
        "7. Отправьте JSON мне в этот чат\n\n"
        "⚠️ <b>Важно:</b> JSON должен начинаться с <code>[</code> и заканчиваться <code>]</code>\n\n"
        "Пример формата:\n"
        "<code>[{\"name\":\"cookie1\",...}]</code>",
        parse_mode=ParseMode.HTML
    )

    # Устанавливаем состояние ожидания cookies
    await state.set_state(CookieImportStates.waiting_cookies)
    await callback.answer()


async def handle_cookies_json(message: Message, state: FSMContext):
    """Обработка JSON с cookies"""
    user_id = message.from_user.id

    try:
        import json
        from api.routes.sessions import CookieItem

        # Получаем JSON из текста или файла
        json_text = None

        if message.document:
            # Проверяем расширение файла
            file_name = message.document.file_name or ""
            allowed_extensions = ['.json', '.txt', '.md']

            if not any(file_name.endswith(ext) for ext in allowed_extensions):
                await message.answer(
                    f"❌ Неподдерживаемый формат файла: <code>{file_name}</code>\n\n"
                    f"Поддерживаются: .json, .txt, .md",
                    parse_mode=ParseMode.HTML
                )
                return

            # Скачиваем и читаем файл
            try:
                from aiogram import Bot
                bot = message.bot
                file = await bot.get_file(message.document.file_id)
                file_content = await bot.download_file(file.file_path)
                json_text = file_content.read().decode('utf-8')
                logger.info(f"Downloaded cookies file: {file_name} ({len(json_text)} bytes)")
            except Exception as e:
                logger.error(f"Failed to download file: {e}")
                await message.answer(
                    f"❌ Ошибка при чтении файла: {str(e)}\n\n"
                    f"Попробуйте отправить cookies как текст.",
                    parse_mode=ParseMode.HTML
                )
                return
        else:
            # Читаем из текста сообщения
            json_text = message.text

        if not json_text:
            await message.answer(
                "❌ Не удалось получить данные. Отправьте JSON как текст или файл (.json, .txt, .md)"
            )
            return

        # Парсим JSON
        cookies_data = json.loads(json_text)

        if not isinstance(cookies_data, list):
            await message.answer(
                "❌ Неверный формат! JSON должен быть массивом cookies.\n\n"
                "Ожидается: <code>[{...}, {...}]</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # Преобразуем в наш формат
        cookies = []
        for cookie in cookies_data:
            try:
                cookies.append(CookieItem(
                    name=cookie.get('name', ''),
                    value=cookie.get('value', ''),
                    domain=cookie.get('domain', ''),
                    path=cookie.get('path', '/'),
                    expires=cookie.get('expirationDate'),
                    httpOnly=cookie.get('httpOnly', False),
                    secure=cookie.get('secure', False),
                    sameSite=cookie.get('sameSite')
                ))
            except Exception as e:
                logger.warning(f"Failed to parse cookie: {e}")
                continue

        if not cookies:
            await message.answer("❌ Не удалось распарсить cookies из JSON")
            return

        # Фильтруем только wildberries cookies
        wb_cookies = [c for c in cookies if 'wildberries' in c.domain.lower()]

        if not wb_cookies:
            await message.answer(
                "❌ В предоставленных cookies нет Wildberries cookies!\n\n"
                "Убедитесь что вы экспортировали cookies со страницы <code>seller.wildberries.ru</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # Импортируем cookies
        from utils.encryption import encrypt_token
        cookies_json = json.dumps([c.dict() for c in wb_cookies])

        # Преобразуем в формат Playwright
        playwright_cookies = []
        for c in wb_cookies:
            # Валидируем sameSite - Playwright требует строго Strict|Lax|None
            same_site = c.sameSite
            if same_site not in ['Strict', 'Lax', 'None']:
                same_site = 'Lax'  # По умолчанию

            playwright_cookies.append({
                'name': c.name,
                'value': c.value,
                'domain': c.domain,
                'path': c.path,
                'expires': c.expires if c.expires else -1,
                'httpOnly': c.httpOnly if c.httpOnly is not None else False,
                'secure': c.secure if c.secure is not None else False,
                'sameSite': same_site
            })

        cookies_encrypted = encrypt_token(json.dumps(playwright_cookies))

        # Сохраняем в БД
        # Сначала деактивируем старые сессии
        db.invalidate_browser_session(user_id)
        # Затем создаём новую сессию
        db.add_browser_session(
            user_id=user_id,
            phone="",  # Телефон не требуется при импорте cookies
            cookies_encrypted=cookies_encrypted,
            supplier_name=None,
            expires_days=7
        )

        # Пробуем загрузить профили с импортированными cookies
        try:
            from browser.auth import WBAuthService

            # Уведомляем пользователя что начинается загрузка профилей
            status_msg = await message.answer(
                "🔄 Cookies сохранены! Загружаю профили поставщиков...",
                parse_mode=ParseMode.HTML
            )

            # Обновляем сессию с профилями (открывает браузер, применяет cookies, получает профили)
            auth_service = WBAuthService()
            # refresh_profiles_with_cookies принимает расшифрованные cookies
            profiles = await auth_service.refresh_profiles_with_cookies(playwright_cookies)

            if profiles:
                # Обновляем supplier_name в БД
                supplier_info = f"{profiles[0]['name']}"
                if profiles[0].get('company'):
                    supplier_info += f" ({profiles[0]['company']})"

                db.invalidate_browser_session(user_id)
                db.add_browser_session(
                    user_id=user_id,
                    phone="",
                    cookies_encrypted=cookies_encrypted,
                    supplier_name=supplier_info,
                    expires_days=7
                )

                await status_msg.edit_text(
                    f"✅ <b>Cookies импортированы успешно!</b>\n\n"
                    f"📊 Импортировано: {len(wb_cookies)} cookies\n"
                    f"👤 Профиль: {supplier_info}\n"
                    f"📋 Доступно профилей: {len(profiles)}\n"
                    f"⏰ Срок действия: 7 дней\n\n"
                    f"Теперь можете использовать бота!",
                    parse_mode=ParseMode.HTML
                )
            else:
                await status_msg.edit_text(
                    f"⚠️ <b>Cookies импортированы, но не удалось загрузить профили</b>\n\n"
                    f"📊 Импортировано: {len(wb_cookies)} cookies\n"
                    f"⏰ Срок действия: 7 дней\n\n"
                    f"Cookies могли истечь или требуется повторная авторизация в браузере.",
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.warning(f"Failed to load profiles after cookie import: {e}")
            # Даже если не удалось загрузить профили, cookies сохранены
            await message.answer(
                f"✅ <b>Cookies импортированы!</b>\n\n"
                f"📊 Импортировано: {len(wb_cookies)} cookies\n"
                f"⏰ Срок действия: 7 дней\n\n"
                f"⚠️ Не удалось автоматически загрузить профили, но вы можете попробовать использовать бота.",
                parse_mode=ParseMode.HTML
            )

        # Очищаем состояние
        await state.clear()

    except json.JSONDecodeError:
        await message.answer(
            "❌ Неверный JSON формат!\n\n"
            "Убедитесь что вы скопировали весь текст из Cookie-Editor.\n"
            "JSON должен начинаться с <code>[</code> и заканчиваться <code>]</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error importing cookies: {e}", exc_info=True)
        await message.answer(
            f"❌ Ошибка при импорте cookies:\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )
        await state.clear()


async def main():
    """Главная функция запуска бота"""
    global db, bot

    # Валидация конфигурации
    Config.validate()
    logger.info("Configuration validated")
    logger.info(Config.get_summary())

    # ОТЛАДКА: Показываем ADMIN_IDS
    logger.info(f"🔐 ADMIN_IDS: {Config.ADMIN_IDS}")
    logger.info(f"🔐 ADMIN_IDS type: {type(Config.ADMIN_IDS)}")
    logger.info(f"🔐 ADMIN_IDS count: {len(Config.ADMIN_IDS)}")

    # Инициализация БД
    db = get_database()
    logger.info("Database initialized")

    # Инициализация бота
    bot = Bot(
        token=Config.get_bot_token(),
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
    dp.callback_query.register(callback_import_cookies, F.data == "import_cookies")

    # Регистрация обработчика cookies JSON (только в состоянии waiting_cookies)
    dp.message.register(handle_cookies_json, CookieImportStates.waiting_cookies)

    # Подключение роутеров
    dp.include_router(redistribution_router)
    dp.include_router(browser_auth_router)

    logger.info("Handlers registered")

    # Запуск бота
    print("\n✅ Бот успешно запущен!")
    print(f"🤖 Бот: @mpbizai_bot")
    print(f"👤 Админ: {Config.ADMIN_IDS}")
    print("\n📝 Команды бота:")
    print("   /start - начало работы (авторизация через SMS)")
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
                        token=Config.get_bot_token(),
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
