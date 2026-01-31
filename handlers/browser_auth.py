"""
Handlers для авторизации через SMS в ЛК Wildberries.

Команды:
- /auth - начать авторизацию
- /sessions - список активных сессий
- /logout - выйти из сессии
"""

import asyncio
import logging
from io import BytesIO
from typing import TYPE_CHECKING

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from browser.auth import WBAuthService, AuthStatus, get_auth_service
from config import Config
from db_factory import get_database
from utils.encryption import encrypt_token, decrypt_token

logger = logging.getLogger(__name__)
router = Router(name="browser_auth")


def get_db():
    """Получает экземпляр БД"""
    return get_database()


class AuthStates(StatesGroup):
    """Состояния FSM для авторизации"""
    waiting_phone = State()
    waiting_code = State()


# ==================== /auth ====================

@router.message(Command("auth"))
async def cmd_auth(message: Message, state: FSMContext):
    """Начать авторизацию через SMS"""
    user_id = message.from_user.id
    db = get_db()

    # Проверяем текущие сессии
    sessions = db.get_browser_sessions(user_id, active_only=True)

    text = (
        "🔐 <b>Авторизация в ЛК Wildberries</b>\n\n"
        "Wildberries не даёт API для перемещения остатков, "
        "поэтому бот работает через личный кабинет.\n\n"
        "<b>Безопасность:</b>\n"
        "• Мы НЕ храним пароль — только одноразовый SMS-код\n"
        "• Выйти можно в любой момент: /logout\n\n"
    )

    if sessions:
        text += f"У вас уже есть {len(sessions)} активных сессий.\n"
        text += "Отправьте номер телефона для новой авторизации или /sessions для списка.\n\n"

    text += (
        "📱 <b>Нажмите кнопку ниже</b> или введите номер вручную:\n"
        "<code>+79001234567</code> или <code>89001234567</code>"
    )

    # Кнопка "Поделиться номером телефона"
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться номером", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await state.set_state(AuthStates.waiting_phone)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.message(AuthStates.waiting_phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext):
    """Обработка номера телефона через кнопку 'Поделиться'"""
    user_id = message.from_user.id
    phone = message.contact.phone_number
    await _process_phone_auth(message, state, phone)


@router.message(AuthStates.waiting_phone)
async def process_phone_text(message: Message, state: FSMContext):
    """Обработка введённого номера телефона вручную"""
    if not message.text:
        await message.answer("Отправьте номер телефона или нажмите кнопку ниже.")
        return
    phone = message.text.strip()
    await _process_phone_auth(message, state, phone)


async def _process_phone_auth(message: Message, state: FSMContext, phone: str):
    """Общая логика авторизации по номеру телефона"""
    user_id = message.from_user.id
    db = get_db()

    # Валидация номера
    auth_service = get_auth_service()
    try:
        normalized_phone = auth_service.normalize_phone(phone)
    except ValueError as e:
        await message.answer(
            f"Некорректный номер телефона.\n\n"
            f"Отправьте номер в формате:\n"
            f"+79001234567 или 89001234567",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # ВАЖНО: Устанавливаем состояние waiting_code СРАЗУ, до начала авторизации!
    # Это нужно, чтобы если пользователь отправит код пока браузер работает,
    # сообщение попало в правильный handler (process_code), а не в process_phone_text
    await state.update_data(phone=normalized_phone)
    await state.set_state(AuthStates.waiting_code)

    # Убираем клавиатуру с кнопкой и показываем прогресс
    progress_msg = await message.answer(
        f"📱 Номер: <code>{normalized_phone}</code>\n\n"
        f"⏳ <b>Шаг 1/4:</b> Открываю страницу Wildberries...",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )

    try:
        # Небольшая пауза чтобы пользователь увидел первый шаг
        await asyncio.sleep(0.5)

        # Обновляем прогресс
        try:
            await progress_msg.edit_text(
                f"📱 Номер: <code>{normalized_phone}</code>\n\n"
                f"✅ <b>Шаг 1/4:</b> Страница открыта\n"
                f"⏳ <b>Шаг 2/4:</b> Ввожу номер телефона...",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.debug(f"Ошибка при редактировании сообщения (шаг 1→2): {e}")
            pass  # Игнорируем ошибки редактирования

        # Начинаем авторизацию (занимает время - browser automation)
        # Используем asyncio.create_task чтобы можно было обновлять прогресс
        auth_task = asyncio.create_task(auth_service.start_auth(user_id, normalized_phone))

        # Ждём 3 секунды и обновляем прогресс (увеличено для Telegram API)
        await asyncio.sleep(3)
        try:
            await progress_msg.edit_text(
                f"📱 Номер: <code>{normalized_phone}</code>\n\n"
                f"✅ <b>Шаг 1/4:</b> Страница открыта\n"
                f"✅ <b>Шаг 2/4:</b> Номер введён\n"
                f"⏳ <b>Шаг 3/4:</b> Отправляю запрос на SMS...",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.debug(f"Ошибка при редактировании сообщения (шаг 2→3): {e}")
            pass  # Игнорируем ошибки редактирования

        # Ждём завершения авторизации
        session = await auth_task

        if session.status == AuthStatus.PENDING_CODE:
            # SMS отправлено, состояние уже установлено выше
            # Обновляем прогресс (финальное сообщение)
            try:
                # Небольшая пауза перед финальным редактированием
                await asyncio.sleep(1)
                await progress_msg.edit_text(
                    f"📱 Номер: <code>{normalized_phone}</code>\n\n"
                    f"✅ <b>Шаг 1/4:</b> Страница открыта\n"
                    f"✅ <b>Шаг 2/4:</b> Номер введён\n"
                    f"✅ <b>Шаг 3/4:</b> Запрос отправлен\n"
                    f"✅ <b>Шаг 4/4:</b> SMS отправлен!\n\n"
                    f"📩 Код придёт от <b>Wildberries</b> на ваш телефон.\n"
                    f"🔒 Код одноразовый — после ввода он больше не действует.\n\n"
                    f"Напишите 6-значный код:",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.debug(f"Ошибка при редактировании финального сообщения: {e}")
                # Если не удалось отредактировать - отправляем новое сообщение
                await message.answer(
                    f"✅ SMS отправлен!\n\n"
                    f"📩 Код придёт от <b>Wildberries</b> на ваш телефон.\n"
                    f"Напишите 6-значный код:",
                    parse_mode="HTML"
                )

            # Проверяем, не отправил ли пользователь код пока мы ждали
            data = await state.get_data()
            pending_code = data.get('pending_code')

            if pending_code and pending_code.isdigit() and len(pending_code) == 6:
                # Код уже был отправлен — обрабатываем его автоматически
                logger.info(f"Найден pending_code для user {user_id}, обрабатываем автоматически")
                await message.answer(
                    f"🔍 Проверяю ваш код...",
                    parse_mode="HTML"
                )
                # Очищаем pending_code
                await state.update_data(pending_code=None)
                # Вызываем submit_code
                try:
                    code_session = await auth_service.submit_code(user_id, pending_code)
                    # Обрабатываем результат (копируем логику из process_code)
                    await _handle_code_result(message, state, code_session, data.get('phone'))
                except Exception as e:
                    logger.error(f"Ошибка при автоматическом вводе кода: {e}")
                    await message.answer(
                        f"Код получен, но произошла ошибка.\n"
                        f"Попробуйте ввести код ещё раз:"
                    )
                return
        elif session.status == AuthStatus.CAPTCHA_REQUIRED:
            # WB показал captcha - отправляем скриншот пользователю
            try:
                await progress_msg.delete()
            except Exception:
                pass

            await state.clear()
            await auth_service.close_session(user_id)

            if session.captcha_screenshot:
                photo = BufferedInputFile(session.captcha_screenshot, filename="captcha.png")
                await message.answer_photo(
                    photo,
                    caption=(
                        "⚠️ <b>Wildberries показал капчу</b>\n\n"
                        "К сожалению, WB заблокировал автоматическую авторизацию.\n\n"
                        "<b>Что делать:</b>\n"
                        "• Подождите 10-15 минут и попробуйте снова\n"
                        "• Попробуйте с другого номера\n"
                        "• Если ошибка повторяется — свяжитесь с поддержкой\n\n"
                        "Попробуйте снова: /auth"
                    ),
                    parse_mode="HTML"
                )
            else:
                await message.answer(
                    "⚠️ <b>Wildberries показал капчу</b>\n\n"
                    "К сожалению, WB заблокировал автоматическую авторизацию.\n\n"
                    "Подождите 10-15 минут и попробуйте снова: /auth",
                    parse_mode="HTML"
                )
        elif session.status == AuthStatus.FAILED:
            try:
                await progress_msg.delete()
            except Exception:
                pass

            await state.clear()
            await auth_service.close_session(user_id)

            error_msg = session.error_message or "Неизвестная ошибка"

            # Специальная обработка rate limit
            if "rate limit" in error_msg.lower() or "запрос кода возможен" in error_msg.lower():
                await message.answer(
                    f"⏳ <b>Слишком много попыток</b>\n\n"
                    f"{error_msg}\n\n"
                    f"Wildberries временно заблокировал запросы кода для этого номера.\n"
                    f"Подождите указанное время и попробуйте снова: /auth",
                    parse_mode="HTML"
                )
            else:
                await message.answer(
                    f"❌ Ошибка авторизации: {error_msg}\n\n"
                    f"Попробуйте ещё раз: /auth"
                )
        else:
            try:
                await progress_msg.delete()
            except Exception:
                pass

            await state.clear()
            await auth_service.close_session(user_id)
            await message.answer(
                f"Неожиданный статус: {session.status.value}\n\n"
                f"Попробуйте ещё раз: /auth"
            )

    except Exception as e:
        logger.error(f"Ошибка при авторизации: {e}")
        try:
            await progress_msg.delete()
        except Exception:
            pass

        await state.clear()
        await message.answer(
            f"Произошла ошибка при авторизации.\n"
            f"Попробуйте позже: /auth"
        )


async def _handle_code_result(message: Message, state: FSMContext, session, phone: str):
    """Обработка результата submit_code (вынесено для переиспользования)"""
    user_id = message.from_user.id
    db = get_db()
    auth_service = get_auth_service()

    if session.status == AuthStatus.SUCCESS:
        # Успешная авторизация - сохраняем сессию
        cookies_json = auth_service._browser_service.serialize_cookies(session.cookies) if auth_service._browser_service else ""

        if not cookies_json:
            logger.error(f"Cookies пусты для user_id={user_id}")
            await message.answer("Ошибка сохранения сессии. Попробуйте авторизоваться заново: /auth")
            await state.clear()
            await auth_service.close_session(user_id)
            return

        cookies_encrypted = encrypt_token(cookies_json)

        session_id = db.add_browser_session(
            user_id=user_id,
            phone=phone,
            cookies_encrypted=cookies_encrypted,
            supplier_name=session.supplier_name
        )

        # Создаем suppliers для всех доступных профилей
        suppliers_created = 0
        if session.available_profiles:
            logger.info(f"Создаем suppliers для {len(session.available_profiles)} профилей")

            # Создаем фейковый токен для browser-based авторизации
            token_id = db.add_wb_token(
                user_id=user_id,
                encrypted_token="browser_session",
                name=f"Browser Session ({phone[-4:]})"
            )

            for i, profile in enumerate(session.available_profiles):
                try:
                    # Название supplier - имя профиля или компания
                    supplier_name = profile.get('company') or profile.get('name') or f"Кабинет {phone[-4:]}"

                    # Добавляем ИНН если есть
                    if profile.get('inn'):
                        supplier_name = f"{supplier_name} (ИНН: {profile['inn']})"

                    db.add_supplier(
                        user_id=user_id,
                        name=supplier_name,
                        token_id=token_id,
                        is_default=(i == 0 or profile.get('is_active', False))  # Первый или активный = default
                    )
                    suppliers_created += 1
                    logger.info(f"  ✅ Создан supplier: {supplier_name}")
                except Exception as e:
                    logger.error(f"Ошибка при создании supplier для профиля {profile}: {e}")

            logger.info(f"Создано {suppliers_created} suppliers из {len(session.available_profiles)} профилей")

        await state.clear()
        await auth_service.close_session(user_id)

        # Информация о профилях для сообщения
        if suppliers_created > 1:
            supplier_info = f"\n📛 Доступно кабинетов: <b>{suppliers_created}</b>"
        elif session.supplier_name:
            supplier_info = f"\n📛 Магазин: <b>{session.supplier_name}</b>"
        else:
            supplier_info = ""

        webapp_url = Config.WEBAPP_URL
        if webapp_url and webapp_url.startswith("https://"):
            full_url = f"{webapp_url.rstrip('/')}/webapp/index.html"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📦 Открыть Перераспределение",
                    web_app=WebAppInfo(url=full_url)
                )],
                [InlineKeyboardButton(
                    text="🔄 Войти в другой аккаунт",
                    callback_data="reauth"
                )]
            ])

            await message.answer(
                f"✅ <b>Авторизация успешна!</b>{supplier_info}\n"
                f"📱 Номер: <code>{phone}</code>\n\n"
                f"🔐 Сессия сохранена в защищённом хранилище.\n\n"
                f"👇 <b>Нажмите кнопку ниже</b>, чтобы открыть панель перераспределения:",
                parse_mode="HTML",
                reply_markup=keyboard
            )
        else:
            await message.answer(
                f"✅ <b>Авторизация успешна!</b>{supplier_info}\n"
                f"📱 Номер: <code>{phone}</code>\n\n"
                f"🔐 Сессия сохранена в защищённом хранилище.\n\n"
                f"<b>Что дальше?</b>\n"
                f"• /redistribute — создать заявку на перемещение\n"
                f"• /sessions — посмотреть активные сессии\n"
                f"• /logout — выйти из аккаунта",
                parse_mode="HTML"
            )

    elif session.status == AuthStatus.INVALID_CODE:
        # WB сбрасывает код после неверной попытки
        # Не закрываем сессию - будем ждать и запрашивать новый код автоматически
        await message.answer(
            "❌ <b>Неверный код</b>\n\n"
            "Wildberries сбросил попытку ввода.\n"
            "Старый код больше не действует.\n\n"
            "⏳ <b>Ожидайте ~1 минуту</b> — бот автоматически запросит новый код...",
            parse_mode="HTML"
        )

        # Запускаем фоновую задачу для ожидания и запроса нового кода
        asyncio.create_task(
            _wait_and_request_new_code(message.bot, user_id, phone, state)
        )

    elif session.status == AuthStatus.CODE_EXPIRED:
        await state.clear()
        await auth_service.close_session(user_id)
        await message.answer("Код истёк. Начните авторизацию заново: /auth")

    elif session.status == AuthStatus.TOO_MANY_ATTEMPTS:
        await state.clear()
        await auth_service.close_session(user_id)
        await message.answer("Слишком много попыток.\nПодождите несколько минут и попробуйте снова: /auth")

    else:
        await state.clear()
        await auth_service.close_session(user_id)
        error_msg = session.error_message or "Неизвестная ошибка"
        await message.answer(f"Ошибка: {error_msg}\n\nПопробуйте ещё раз: /auth")


async def _wait_and_request_new_code(bot: Bot, user_id: int, phone: str, state: FSMContext):
    """
    Фоновая задача: ждёт появления кнопки запроса нового кода и нажимает её.

    Args:
        bot: Инстанс бота для отправки сообщений
        user_id: Telegram user ID
        phone: Номер телефона
        state: FSM контекст
    """
    auth_service = get_auth_service()

    try:
        logger.info(f"[WAIT_NEW_CODE] Запуск фоновой задачи для user {user_id}")

        # Ждём и запрашиваем новый код (до 70 сек)
        session = await auth_service.request_new_code(user_id, max_wait_seconds=70)

        if session.status == AuthStatus.NEW_CODE_SENT:
            # Успех - новый код запрошен
            await bot.send_message(
                user_id,
                "✅ <b>Новый код запрошен!</b>\n\n"
                "📱 SMS с новым кодом должно прийти на ваш телефон.\n\n"
                "Введите 6-значный код:",
                parse_mode="HTML"
            )
            # Устанавливаем состояние ожидания кода
            await state.set_state(AuthStates.waiting_code)
            await state.update_data(phone=phone)
            logger.info(f"[WAIT_NEW_CODE] Новый код запрошен для user {user_id}")

        elif session.status == AuthStatus.WAITING_NEW_CODE:
            # Всё ещё ждём - что-то пошло не так
            await bot.send_message(
                user_id,
                "⚠️ Не удалось дождаться кнопки запроса нового кода.\n\n"
                "Попробуйте начать заново: /auth",
                parse_mode="HTML"
            )
            await state.clear()
            await auth_service.close_session(user_id)

        else:
            # Ошибка
            error_msg = session.error_message or "Неизвестная ошибка"
            await bot.send_message(
                user_id,
                f"❌ Не удалось запросить новый код.\n\n"
                f"Ошибка: {error_msg}\n\n"
                f"Попробуйте начать заново: /auth",
                parse_mode="HTML"
            )
            await state.clear()
            await auth_service.close_session(user_id)

    except Exception as e:
        logger.error(f"[WAIT_NEW_CODE] Ошибка для user {user_id}: {e}")
        try:
            await bot.send_message(
                user_id,
                "❌ Произошла ошибка при запросе нового кода.\n\n"
                "Попробуйте начать заново: /auth"
            )
            await state.clear()
            await auth_service.close_session(user_id)
        except Exception:
            pass


@router.message(AuthStates.waiting_code)
async def process_code(message: Message, state: FSMContext):
    """Обработка введённого SMS кода"""
    user_id = message.from_user.id
    code = message.text.strip()
    db = get_db()

    # Валидация кода
    if not code.isdigit() or len(code) != 6:
        await message.answer(
            "Код должен содержать 6 цифр.\n"
            "Введите код из SMS:"
        )
        return

    data = await state.get_data()
    phone = data.get('phone')

    auth_service = get_auth_service()

    # Проверяем, готова ли сессия (start_auth мог ещё не завершиться)
    if not auth_service.has_session(user_id):
        await message.answer(
            "⏳ Подождите, идёт подготовка...\n\n"
            "SMS ещё запрашивается. Как только придёт код — отправьте его снова."
        )
        # Сохраняем код в state, чтобы попробовать автоматически позже
        await state.update_data(pending_code=code)
        return

    await message.answer("Проверяю код...")

    try:
        session = await auth_service.submit_code(user_id, code)
        await _handle_code_result(message, state, session, phone)

    except ValueError as e:
        error_msg = str(e)
        if "Сессия не найдена" in error_msg or "session" in error_msg.lower():
            # Сессия истекла - автоматически перезапускаем авторизацию
            logger.warning(f"Session expired for user {user_id}, auto-restarting auth with phone {phone}")

            await message.answer(
                "⚠️ <b>Сессия истекла</b>\n\n"
                "Браузерная сессия была закрыта (слишком долго ждали код).\n"
                "🔄 <b>Автоматически перезапускаю авторизацию...</b>",
                parse_mode="HTML"
            )

            # Сохраняем код для автоматической отправки после перезапуска
            await state.update_data(pending_code=code)

            # Перезапускаем авторизацию с тем же номером
            if phone:
                try:
                    # Создаем прогресс сообщение
                    progress_msg = await message.answer(
                        f"📱 Номер: <code>{phone}</code>\n\n"
                        f"⏳ Повторная авторизация...",
                        parse_mode="HTML"
                    )

                    # Запускаем авторизацию
                    session = await auth_service.start_auth(user_id, phone)

                    if session.status == AuthStatus.PENDING_CODE:
                        await progress_msg.edit_text(
                            f"✅ SMS отправлен заново!\n\n"
                            f"📩 Код придёт от <b>Wildberries</b>.\n"
                            f"Напишите 6-значный код:\n\n"
                            f"💡 <b>Сохранённый код ({code}) будет проверен автоматически...</b>",
                            parse_mode="HTML"
                        )

                        # Пробуем автоматически отправить сохранённый код
                        await asyncio.sleep(1)
                        try:
                            code_session = await auth_service.submit_code(user_id, code)
                            await _handle_code_result(message, state, code_session, phone)
                        except Exception as retry_error:
                            logger.error(f"Failed to auto-submit saved code: {retry_error}")
                            await message.answer(
                                "Не удалось автоматически отправить сохранённый код.\n"
                                "Введите новый код из SMS:"
                            )
                    else:
                        await progress_msg.delete()
                        await message.answer(
                            f"❌ Не удалось перезапустить авторизацию.\n\n"
                            f"Попробуйте заново: /auth"
                        )
                except Exception as restart_error:
                    logger.error(f"Failed to restart auth: {restart_error}")
                    await state.clear()
                    await message.answer(
                        "❌ Не удалось автоматически перезапустить авторизацию.\n\n"
                        "Попробуйте заново: /auth"
                    )
            else:
                await state.clear()
                await message.answer(
                    "⚠️ Сессия истекла.\n\n"
                    "Начните авторизацию заново: /auth"
                )
        else:
            # Другая ошибка ValueError
            logger.error(f"ValueError при вводе кода: {e}")
            await state.clear()
            await message.answer(
                f"❌ Ошибка: {error_msg}\n\n"
                "Попробуйте заново: /auth"
            )

    except Exception as e:
        logger.error(f"Ошибка при вводе кода: {e}")
        await state.clear()
        await message.answer(
            "Произошла ошибка.\n"
            "Попробуйте авторизоваться заново: /auth"
        )


# ==================== /sessions ====================

@router.message(Command("sessions"))
async def cmd_sessions(message: Message):
    """Показать активные сессии"""
    user_id = message.from_user.id
    db = get_db()
    sessions = db.get_browser_sessions(user_id, active_only=True)

    if not sessions:
        await message.answer(
            "У вас нет активных сессий.\n\n"
            "Используйте /auth для авторизации в ЛК Wildberries."
        )
        return

    text = f"Ваши активные сессии ({len(sessions)}):\n\n"

    for i, session in enumerate(sessions, 1):
        phone = session['phone']
        # Маскируем номер
        masked_phone = f"{phone[:5]}***{phone[-2:]}"
        supplier = session.get('supplier_name') or 'Не определён'
        created = session['created_at'][:16] if session.get('created_at') else 'N/A'

        text += (
            f"{i}. {masked_phone}\n"
            f"   Магазин: {supplier}\n"
            f"   Создана: {created}\n\n"
        )

    text += "Для выхода из сессии: /logout <номер>"

    await message.answer(text)


# ==================== /logout ====================

@router.message(Command("logout"))
async def cmd_logout(message: Message):
    """Выйти из сессии"""
    user_id = message.from_user.id
    db = get_db()

    # Проверяем аргументы
    args = message.text.split()
    if len(args) < 2:
        sessions = db.get_browser_sessions(user_id, active_only=True)
        if not sessions:
            await message.answer("У вас нет активных сессий.")
            return

        text = "Укажите номер для выхода:\n\n"
        for session in sessions:
            phone = session['phone']
            masked = f"{phone[:5]}***{phone[-2:]}"
            text += f"/logout {phone}\n"

        await message.answer(text)
        return

    phone = args[1].strip()

    # Нормализуем номер
    auth_service = get_auth_service()
    try:
        normalized_phone = auth_service.normalize_phone(phone)
    except ValueError:
        await message.answer("Некорректный номер телефона.")
        return

    # Ищем сессию
    session = db.get_browser_session_by_phone(user_id, normalized_phone)
    if not session:
        await message.answer(
            f"Сессия с номером {normalized_phone} не найдена.\n"
            f"Используйте /sessions для списка активных сессий."
        )
        return

    # Деактивируем сессию
    db.deactivate_browser_session(session['id'])

    await message.answer(
        f"Сессия с номером {normalized_phone[:5]}*** деактивирована.\n\n"
        f"Для новой авторизации: /auth"
    )


# ==================== /screenshot ====================

@router.message(Command("screenshot"))
async def cmd_screenshot(message: Message):
    """Сделать скриншот текущей страницы (для отладки)"""
    user_id = message.from_user.id

    auth_service = get_auth_service()
    screenshot = await auth_service.take_screenshot(user_id)

    if screenshot:
        photo = BufferedInputFile(screenshot, filename="screenshot.png")
        await message.answer_photo(photo, caption="Текущая страница браузера")
    else:
        await message.answer(
            "Нет активной сессии браузера.\n"
            "Скриншот доступен только во время авторизации."
        )


# ==================== Fallback для кода без состояния ====================

@router.message(F.text.regexp(r'^\d{6}$'))
async def process_code_fallback(message: Message, state: FSMContext):
    """
    Fallback хэндлер для 6-значного кода, отправленного без активного состояния.
    Срабатывает когда пользователь отправил код после ошибки авторизации.
    """
    current_state = await state.get_state()

    # Если состояние waiting_code - пропускаем, обработает основной хэндлер
    if current_state == AuthStates.waiting_code.state:
        return

    user_id = message.from_user.id
    code = message.text.strip()

    auth_service = get_auth_service()

    # Проверяем, есть ли активная браузерная сессия
    if auth_service.has_session(user_id):
        # Есть сессия - пробуем отправить код
        logger.info(f"[FALLBACK] User {user_id} отправил код {code[:2]}**** при наличии сессии")

        data = await state.get_data()
        phone = data.get('phone', 'неизвестен')

        await message.answer("🔄 Проверяю код...")

        try:
            session = await auth_service.submit_code(user_id, code)
            await _handle_code_result(message, state, session, phone)
        except ValueError as e:
            error_msg = str(e)
            if "Сессия не найдена" in error_msg or "session" in error_msg.lower():
                # Сессия истекла - информируем пользователя
                logger.warning(f"[FALLBACK] Session expired for user {user_id}")
                await message.answer(
                    "⚠️ <b>Сессия авторизации истекла</b>\n\n"
                    "Браузерная сессия была закрыта.\n\n"
                    "Начните авторизацию заново: /auth",
                    parse_mode="HTML"
                )
            else:
                logger.error(f"[FALLBACK] ValueError: {e}")
                await message.answer(
                    f"❌ Ошибка: {error_msg}\n\n"
                    "Начните авторизацию заново: /auth"
                )
        except Exception as e:
            logger.error(f"[FALLBACK] Ошибка при вводе кода: {e}")
            await message.answer(
                "❌ Произошла ошибка при проверке кода.\n\n"
                "Начните авторизацию заново: /auth"
            )
    else:
        # Нет сессии - предлагаем начать заново
        logger.info(f"[FALLBACK] User {user_id} отправил код без активной сессии")
        await message.answer(
            "⚠️ <b>Сессия авторизации истекла</b>\n\n"
            "Вы отправили код, но авторизация уже была прервана или завершена.\n\n"
            "Чтобы войти заново, нажмите /auth",
            parse_mode="HTML"
        )


# ==================== Отмена ====================

@router.message(Command("cancel"), AuthStates)
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена авторизации"""
    user_id = message.from_user.id

    auth_service = get_auth_service()
    await auth_service.close_session(user_id)
    await state.clear()

    await message.answer(
        "Авторизация отменена.\n\n"
        "Для новой попытки: /auth"
    )
