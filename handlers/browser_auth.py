"""
Handlers для авторизации через SMS в ЛК Wildberries.

Команды:
- /auth - начать авторизацию
- /sessions - список активных сессий
- /logout - выйти из сессии
"""

import logging
from io import BytesIO

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from browser.auth import WBAuthService, AuthStatus, get_auth_service
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
        "Авторизация в ЛК Wildberries\n\n"
        "Для перемещения остатков между складами нужна авторизация "
        "через номер телефона (как в личном кабинете WB).\n\n"
    )

    if sessions:
        text += f"У вас уже есть {len(sessions)} активных сессий.\n"
        text += "Отправьте номер телефона для новой авторизации или /sessions для списка.\n\n"

    text += (
        "Отправьте номер телефона в формате:\n"
        "+79001234567 или 89001234567"
    )

    await state.set_state(AuthStates.waiting_phone)
    await message.answer(text)


@router.message(AuthStates.waiting_phone)
async def process_phone(message: Message, state: FSMContext):
    """Обработка введённого номера телефона"""
    user_id = message.from_user.id
    phone = message.text.strip()
    db = get_db()

    # Валидация номера
    auth_service = get_auth_service()
    try:
        normalized_phone = auth_service.normalize_phone(phone)
    except ValueError as e:
        await message.answer(
            f"Некорректный номер телефона.\n\n"
            f"Отправьте номер в формате:\n"
            f"+79001234567 или 89001234567"
        )
        return

    await message.answer(
        f"📱 Номер: {normalized_phone}\n\n"
        f"⏳ Запрашиваю SMS код...\n"
        f"📩 SMS придёт от <b>ВАЛБОРИС</b>\n\n"
        f"Напишите 6-значный код из SMS сюда в чат.",
        parse_mode="HTML"
    )

    try:
        # Начинаем авторизацию
        session = await auth_service.start_auth(user_id, normalized_phone)

        if session.status == AuthStatus.PENDING_CODE:
            # SMS отправлено, ждём код
            await state.update_data(phone=normalized_phone)
            await state.set_state(AuthStates.waiting_code)
            await message.answer(
                f"✅ SMS отправлено!\n\n"
                f"📩 Код придёт от <b>ВАЛБОРИС</b>\n"
                f"Напишите 6 цифр из SMS:",
                parse_mode="HTML"
            )
        elif session.status == AuthStatus.FAILED:
            await state.clear()
            await auth_service.close_session(user_id)
            await message.answer(
                f"Ошибка авторизации: {session.error_message}\n\n"
                f"Попробуйте ещё раз: /auth"
            )
        else:
            await state.clear()
            await auth_service.close_session(user_id)
            await message.answer(
                f"Неожиданный статус: {session.status.value}\n\n"
                f"Попробуйте ещё раз: /auth"
            )

    except Exception as e:
        logger.error(f"Ошибка при авторизации: {e}")
        await state.clear()
        await message.answer(
            f"Произошла ошибка при авторизации.\n"
            f"Попробуйте позже: /auth"
        )


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

    await message.answer("Проверяю код...")

    auth_service = get_auth_service()

    try:
        session = await auth_service.submit_code(user_id, code)

        if session.status == AuthStatus.SUCCESS:
            # Успешная авторизация - сохраняем сессию
            cookies_json = auth_service._browser_service.serialize_cookies(session.cookies) if auth_service._browser_service else ""

            # Проверка что cookies не пустые
            if not cookies_json:
                logger.error(f"Cookies пусты для user_id={user_id}, сессия не может быть сохранена")
                await message.answer("Ошибка сохранения сессии. Попробуйте авторизоваться заново: /auth")
                await state.clear()
                await auth_service.close_session(user_id)
                return

            cookies_encrypted = encrypt_token(cookies_json)

            # Сохраняем в БД
            session_id = db.add_browser_session(
                user_id=user_id,
                phone=phone,
                cookies_encrypted=cookies_encrypted,
                supplier_name=session.supplier_name
            )

            await state.clear()
            await auth_service.close_session(user_id)

            supplier_info = f"\nМагазин: {session.supplier_name}" if session.supplier_name else ""

            await message.answer(
                f"Авторизация успешна!{supplier_info}\n\n"
                f"Номер: {phone}\n"
                f"Сессия сохранена.\n\n"
                f"Теперь вы можете использовать перемещение остатков.\n"
                f"Используйте /redistribute для создания заявок."
            )

        elif session.status == AuthStatus.INVALID_CODE:
            await message.answer(
                "Неверный код. Попробуйте ещё раз.\n"
                "Введите 6-значный код из SMS:"
            )

        elif session.status == AuthStatus.CODE_EXPIRED:
            await state.clear()
            await auth_service.close_session(user_id)
            await message.answer(
                "Код истёк. Начните авторизацию заново: /auth"
            )

        elif session.status == AuthStatus.TOO_MANY_ATTEMPTS:
            await state.clear()
            await auth_service.close_session(user_id)
            await message.answer(
                "Слишком много попыток.\n"
                "Подождите несколько минут и попробуйте снова: /auth"
            )

        else:
            await state.clear()
            await auth_service.close_session(user_id)
            error_msg = session.error_message or "Неизвестная ошибка"
            await message.answer(
                f"Ошибка: {error_msg}\n\n"
                f"Попробуйте ещё раз: /auth"
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
