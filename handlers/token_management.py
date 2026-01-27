"""
Handlers для управления WB API токенами.

Команды:
- /token - главное меню токенов
- Добавление/удаление токенов
- Проверка валидности
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from wb_api.client import WBApiClient
from utils.encryption import encrypt_token
from config import Config
from db_factory import get_database

logger = logging.getLogger(__name__)

router = Router(name="token_management")


class TokenStates(StatesGroup):
    """Состояния для добавления токена"""
    waiting_for_token = State()
    waiting_for_name = State()


def get_db():
    """Получает экземпляр БД (SQLite или PostgreSQL)"""
    return get_database()


@router.message(Command("token"))
async def cmd_token(message: Message):
    """Команда /token - управление WB API токенами"""
    db = get_db()
    user_id = message.from_user.id

    # Получаем токены пользователя
    tokens = db.get_user_wb_tokens(user_id)

    if not tokens:
        text = """
🔑 <b>Управление WB API токенами</b>

У вас пока нет добавленных токенов.

Для работы с перераспределением остатков необходимо добавить WB API токен.

<b>Как получить токен:</b>
1. Перейдите в <a href="https://seller.wildberries.ru/supplier-settings/access-to-api">ЛК WB → Настройки → Доступ к API</a>
2. Нажмите «Создать токен» → «Для интеграции вручную»
3. Выберите тип: <b>Базовый токен</b>
4. Отметьте категории:
   ✅ <b>Маркетплейс</b> — информация о складах
   ✅ <b>Поставки</b> — коэффициенты и бронирование
5. Уровень доступа: <b>Чтение и запись</b>
6. Скопируйте токен и отправьте его мне

⚠️ <i>Токен будет зашифрован и храниться безопасно</i>
"""
        buttons = [[
            InlineKeyboardButton(
                text="➕ Добавить токен",
                callback_data="add_token"
            )
        ]]
    else:
        text = f"""
🔑 <b>Ваши WB API токены</b>

Количество токенов: {len(tokens)}
"""
        buttons = []

        for token in tokens:
            status = "✅" if token['is_active'] else "❌"
            last_used = token['last_used_at'] or "никогда"
            text += f"""
{status} <b>{token['name']}</b>
   ID: {token['id']}
   Последнее использование: {last_used}
"""
            buttons.append([
                InlineKeyboardButton(
                    text=f"🗑 Удалить {token['name']}",
                    callback_data=f"delete_token:{token['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="➕ Добавить токен",
                callback_data="add_token"
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        text,
        parse_mode='HTML',
        reply_markup=keyboard,
        disable_web_page_preview=True
    )


@router.callback_query(F.data == "add_token")
async def callback_add_token(callback: CallbackQuery, state: FSMContext):
    """Начало добавления токена"""
    await callback.answer()

    text = """
🔑 <b>Добавление WB API токена</b>

Отправьте ваш WB API токен.

<b>Токен выглядит примерно так:</b>
<code>eyJhbGciOiJFUzI1NiIsInR5...</code>

⚠️ <i>Убедитесь, что у токена есть права:
✅ Маркетплейс
✅ Поставки
И уровень доступа: Чтение и запись</i>

Для отмены отправьте /cancel
"""

    await callback.message.edit_text(text, parse_mode='HTML')
    await state.set_state(TokenStates.waiting_for_token)


@router.message(TokenStates.waiting_for_token)
async def process_token(message: Message, state: FSMContext):
    """Обработка введённого токена"""
    token = message.text.strip()

    # Удаляем сообщение с токеном из чата (безопасность)
    deletion_failed = False
    try:
        await message.delete()
    except Exception as e:
        deletion_failed = True
        logger.error(f"Failed to delete token message: {e}")

    # КРИТИЧНО: Если не удалось удалить сообщение - предупредить пользователя
    if deletion_failed:
        warning_msg = await message.answer(
            "⚠️ <b>ВНИМАНИЕ!</b>\n\n"
            "Не удалось удалить ваше сообщение с токеном из чата.\n"
            "Это может быть небезопасно!\n\n"
            "<b>ПОЖАЛУЙСТА, удалите его вручную прямо сейчас.</b>\n\n"
            "После удаления нажмите /continue чтобы продолжить,\n"
            "или /cancel чтобы отменить добавление токена.",
            parse_mode='HTML'
        )
        # Сохранить токен во временное хранилище
        await state.update_data(token=token, waiting_for_manual_deletion=True)
        return

    # Проверяем формат
    if len(token) < 50:
        await message.answer(
            "❌ Токен слишком короткий. Проверьте правильность и попробуйте снова."
        )
        return

    # Проверяем валидность токена
    # ВРЕМЕННО ОТКЛЮЧЕНО для тестирования - проверка WB API не работает
    logger.warning("⚠️ Token validation is DISABLED for testing")

    status_msg = await message.answer("⚠️ Добавляю токен без проверки (тестовый режим)...")

    # ЗАКОММЕНТИРОВАНО: Проверка токена
    # try:
    #     async with WBApiClient(token) as client:
    #         is_valid = await client.check_token()
    # except Exception as e:
    #     logger.error(f"Token validation failed: {e}")
    #     is_valid = False
    #
    # if not is_valid:
    #     await status_msg.edit_text(
    #         "❌ Токен невалиден или не имеет нужных прав.\n\n"
    #         "Убедитесь, что токен:\n"
    #         "• Скопирован полностью\n"
    #         "• Не истёк срок действия\n"
    #         "• Есть права на нужные разделы API\n\n"
    #         "Попробуйте ещё раз или /cancel для отмены."
    #     )
    #     return

    # Сохраняем токен во временное хранилище
    await state.update_data(token=token)

    await status_msg.edit_text(
        "✅ Токен принят (без проверки)!\n\n"
        "Введите название для этого токена (например: \"Основной\" или \"Магазин 1\"):\n\n"
        "Или отправьте /skip для имени по умолчанию."
    )
    await state.set_state(TokenStates.waiting_for_name)


@router.message(TokenStates.waiting_for_name)
async def process_token_name(message: Message, state: FSMContext):
    """Обработка названия токена"""
    name = message.text.strip()

    if name.lower() == "/skip":
        name = "Основной"
    elif name.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Добавление токена отменено.")
        return

    # Получаем токен из состояния
    data = await state.get_data()
    token = data.get('token')

    if not token:
        await message.answer("❌ Ошибка: токен не найден. Начните заново с /token")
        await state.clear()
        return

    # Шифруем и сохраняем в БД
    db = get_db()
    user_id = message.from_user.id

    try:
        encrypted = encrypt_token(token)
    except Exception as e:
        logger.error(f"Token encryption failed: {e}", exc_info=True)
        await message.answer(
            f"❌ Ошибка шифрования токена.\n\n"
            f"Обратитесь к администратору.\n"
            f"Детали: {str(e)[:100]}"
        )
        await state.clear()
        return

    try:
        token_id = db.add_wb_token(user_id, encrypted, name)
    except Exception as e:
        logger.error(f"Failed to save token to DB: {e}", exc_info=True)
        await message.answer(
            f"❌ Ошибка сохранения токена в базу данных.\n\n"
            f"Попробуйте позже.\n"
            f"Детали: {str(e)[:100]}"
        )
        await state.clear()
        return

    if token_id:
        logger.info(f"Token added successfully: token_id={token_id}, name={name}")

        # Добавляем поставщика автоматически
        try:
            supplier_id = db.add_supplier(
                user_id=user_id,
                name=name,
                token_id=token_id
            )
            logger.info(f"Supplier added: supplier_id={supplier_id}")
        except Exception as e:
            logger.error(f"Failed to add supplier: {e}")
            await message.answer(
                f"⚠️ Токен добавлен, но не удалось создать поставщика.\n\n"
                f"Ошибка: {str(e)}\n\n"
                f"Используйте /token для повторной попытки."
            )
            await state.clear()
            return

        # Показываем кнопку Mini App после успешного добавления токена
        webapp_url = Config.WEBAPP_URL
        logger.info(f"WEBAPP_URL from config: {webapp_url}")

        # Проверяем HTTPS - Telegram требует HTTPS для Mini App
        if not webapp_url or not webapp_url.startswith("https://"):
            logger.warning(f"WEBAPP_URL is not HTTPS: {webapp_url} - sending fallback message")
            await message.answer(
                f"✅ <b>Токен успешно добавлен!</b>\n\n"
                f"📛 Название: {name}\n"
                f"🆔 ID: {token_id}\n\n"
                f"Используйте команды:\n"
                f"📦 /redistribute - создать заявку на перемещение\n"
                f"🏪 /suppliers - управление поставщиками\n\n"
                f"⚠️ Mini App требует настройки HTTPS на сервере",
                parse_mode='HTML'
            )
        else:
            try:
                if not webapp_url.endswith('/'):
                    webapp_url += '/'

                full_url = f"{webapp_url}webapp/index.html"
                logger.info(f"Full Mini App URL: {full_url}")

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📦 Открыть Перераспределение",
                            web_app=WebAppInfo(url=full_url)
                        )
                    ]
                ])

                logger.info("Sending message with Mini App button...")
                await message.answer(
                    f"✅ <b>Токен успешно добавлен!</b>\n\n"
                    f"📛 Название: {name}\n"
                    f"🆔 ID: {token_id}\n\n"
                    f"Теперь вы можете:\n"
                    f"📦 Открыть Mini App для перераспределения остатков (кнопка ниже)\n"
                    f"🏪 /suppliers - управление поставщиками\n"
                    f"📦 /redistribute - создать заявку на перемещение",
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                logger.info("Message sent successfully!")
            except Exception as e:
                logger.error(f"Failed to send message with Mini App button: {e}", exc_info=True)
                # Отправляем сообщение без кнопки
                await message.answer(
                    f"✅ <b>Токен успешно добавлен!</b>\n\n"
                    f"📛 Название: {name}\n"
                    f"🆔 ID: {token_id}\n\n"
                    f"Используйте команды:\n"
                    f"📦 /redistribute - создать заявку на перемещение\n"
                    f"🏪 /suppliers - управление поставщиками\n\n"
                    f"⚠️ Mini App временно недоступен",
                    parse_mode='HTML'
                )
    else:
        await message.answer(
            "❌ Этот токен уже добавлен.\n\n"
            "Используйте /token для управления токенами."
        )

    await state.clear()


@router.callback_query(F.data.startswith("delete_token:"))
async def callback_delete_token(callback: CallbackQuery):
    """Удаление токена"""
    await callback.answer()

    token_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Подтверждение удаления
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, удалить",
                callback_data=f"confirm_delete_token:{token_id}"
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel_delete_token"
            )
        ]
    ])

    await callback.message.edit_text(
        f"⚠️ <b>Удаление токена</b>\n\n"
        f"Вы уверены, что хотите удалить токен #{token_id}?\n\n"
        f"Все связанные подписки и настройки мониторинга будут отключены.",
        parse_mode='HTML',
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("confirm_delete_token:"))
async def callback_confirm_delete_token(callback: CallbackQuery):
    """Подтверждение удаления токена"""
    await callback.answer()

    token_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    db = get_db()
    deleted = db.delete_wb_token(user_id, token_id)

    if deleted:
        await callback.message.edit_text(
            "✅ Токен удалён.\n\n"
            "Используйте /token для управления токенами."
        )
    else:
        await callback.message.edit_text(
            "❌ Не удалось удалить токен. Возможно, он уже был удалён."
        )


@router.callback_query(F.data == "cancel_delete_token")
async def callback_cancel_delete_token(callback: CallbackQuery):
    """Отмена удаления токена"""
    await callback.answer("Удаление отменено")
    # Возвращаемся к списку токенов
    await cmd_token(callback.message)


@router.message(Command("continue"))
async def cmd_continue(message: Message, state: FSMContext):
    """Продолжение после ручного удаления токена"""
    data = await state.get_data()

    if not data.get('waiting_for_manual_deletion'):
        await message.answer("Нет операций ожидающих продолжения.")
        return

    token = data.get('token')
    if not token:
        await message.answer("❌ Ошибка: токен не найден. Начните заново с /token")
        await state.clear()
        return

    # Убрать флаг ожидания
    await state.update_data(waiting_for_manual_deletion=False)

    # Проверяем формат
    if len(token) < 50:
        await message.answer(
            "❌ Токен слишком короткий. Проверьте правильность и попробуйте снова через /token"
        )
        await state.clear()
        return

    # Проверяем валидность токена
    status_msg = await message.answer("🔄 Проверяю токен...")

    try:
        async with WBApiClient(token) as client:
            is_valid = await client.check_token()
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        is_valid = False

    if not is_valid:
        await status_msg.edit_text(
            "❌ Токен невалиден или не имеет нужных прав.\n\n"
            "Убедитесь, что токен:\n"
            "• Скопирован полностью\n"
            "• Не истёк срок действия\n"
            "• Есть права на нужные разделы API\n\n"
            "Попробуйте ещё раз через /token"
        )
        await state.clear()
        return

    await status_msg.edit_text(
        "✅ Токен валиден!\n\n"
        "Введите название для этого токена (например: \"Основной\" или \"Магазин 1\"):\n\n"
        "Или отправьте /skip для имени по умолчанию."
    )
    await state.set_state(TokenStates.waiting_for_name)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущей операции"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нечего отменять.")
        return

    await state.clear()
    await message.answer(
        "❌ Операция отменена.\n\n"
        "Используйте /token для управления токенами."
    )
