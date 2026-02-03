#!/usr/bin/env python3
"""
Локальный скрипт для авторизации в WB.

SMS приходит на ваш телефон (запрос с обычного IP),
cookies сохраняются в Railway PostgreSQL базу.

Использование:
    python local_auth.py
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

from browser.auth import WBAuthService
from browser.browser_service import get_browser_service
from db_factory import get_database
from utils.encryption import encrypt_token


async def main():
    """Главная функция локальной авторизации"""
    print("=" * 60)
    print("🔐 Локальная авторизация для WB Redistribution Bot")
    print("=" * 60)
    print()

    # Запрашиваем данные у пользователя
    print("📝 Введите ваши данные:")
    print()

    telegram_id = input("Telegram ID (например 98314773): ").strip()
    if not telegram_id.isdigit():
        print("❌ Ошибка: Telegram ID должен быть числом")
        sys.exit(1)
    telegram_id = int(telegram_id)

    phone = input("Номер телефона (+79991234567): ").strip()
    if not phone.startswith('+'):
        phone = '+' + phone

    # Маскируем телефон для безопасного отображения
    phone_masked = f"****{phone[-4:]}" if len(phone) >= 4 else "****"

    print()
    print("=" * 60)
    print(f"👤 Telegram ID: {telegram_id}")
    print(f"📱 Телефон: {phone_masked}")
    print("=" * 60)
    print()

    # Инициализация БД (подключаемся к Railway PostgreSQL)
    database_url = os.getenv('DATABASE_URL', '')
    if not database_url:
        print("⚠️  DATABASE_URL не найден в .env")
        print("Данные будут сохранены локально в SQLite.")
        print()
    else:
        print(f"✅ Подключение к Railway PostgreSQL")
        print()

    db = get_database()

    # Регистрируем пользователя в БД
    db.add_user(
        telegram_id=telegram_id,
        username=None,
        first_name="Local Auth User"
    )
    print(f"✅ Пользователь {telegram_id} зарегистрирован в БД")
    print()

    # Инициализация браузера в ВИДИМОМ режиме
    print("🌐 Запуск браузера...")
    print("   (Откроется окно Chrome - НЕ ЗАКРЫВАЙТЕ его)")
    print()

    # Запускаем браузер с headless=False (видимый режим)
    browser_service = await get_browser_service(headless=False)
    auth = WBAuthService()
    auth._browser_service = browser_service  # Подставляем браузер

    try:
        # Шаг 1: Начать авторизацию
        print("🚀 Шаг 1: Открываю страницу WB и отправляю SMS...")
        session = await auth.start_auth(telegram_id, phone)

        if session.error_message:
            print(f"❌ Ошибка при запросе SMS: {session.error_message}")
            sys.exit(1)

        print()
        print("✅ SMS запрошен успешно!")
        print(f"📱 SMS должен прийти на {phone_masked}")
        print()

        # Шаг 2: Запрашиваем код у пользователя
        print("=" * 60)
        code = input("Введите 6-значный код из SMS: ").strip()
        print("=" * 60)
        print()

        if not code.isdigit() or len(code) != 6:
            print("❌ Ошибка: код должен быть 6-значным числом")
            sys.exit(1)

        # Шаг 3: Отправляем код
        print("🔄 Отправляю код в WB...")
        result = await auth.submit_code(telegram_id, code)

        if result.error_message:
            print(f"❌ Ошибка при вводе кода: {result.error_message}")
            sys.exit(1)

        print()
        print("✅ Авторизация успешна!")
        print()

        # Шаг 4: Парсим профили
        if result.available_profiles:
            print(f"👥 Найдено поставщиков: {len(result.available_profiles)}")
            print()
            for i, profile in enumerate(result.available_profiles, 1):
                name = profile.get('name') or profile.get('company', 'N/A')
                inn = profile.get('inn', '')
                is_active = "🟢 активный" if profile.get('is_active') else ""
                print(f"   {i}. {name} {f'(ИНН: {inn})' if inn else ''} {is_active}")
            print()

        # Шаг 5: Сохраняем данные в БД
        print("💾 Сохраняю данные в Railway БД...")
        print()

        # Получаем cookies из результата авторизации и ШИФРУЕМ
        import json
        cookies_json = json.dumps(result.cookies) if result.cookies else "{}"
        cookies_encrypted = encrypt_token(cookies_json)  # КРИТИЧНО: шифруем cookies!

        # Определяем имя поставщика
        supplier_name = None
        if result.available_profiles:
            first_profile = result.available_profiles[0]
            supplier_name = first_profile.get('name') or first_profile.get('company')
            if first_profile.get('inn'):
                supplier_name = f"{supplier_name} (ИНН: {first_profile['inn']})"

        # Сохраняем browser_session (это то, что проверяет бот!)
        session_id = db.add_browser_session(
            user_id=telegram_id,
            phone=phone,
            cookies_encrypted=cookies_encrypted,  # Зашифрованные cookies
            supplier_name=supplier_name or f"WB ({phone[-4:]})"
        )
        print(f"   ✅ Browser session создан (ID: {session_id})")

        # Также создаем токен для совместимости
        token_id = db.add_wb_token(
            user_id=telegram_id,
            encrypted_token="browser_session",
            name=f"Browser Session ({phone[-4:]})"
        )
        print(f"   ✅ Токен создан (ID: {token_id})")

        # Создаем suppliers для всех профилей
        suppliers_created = 0
        if result.available_profiles:
            for i, profile in enumerate(result.available_profiles):
                profile_name = profile.get('name') or profile.get('company')
                if profile.get('inn'):
                    profile_name = f"{profile_name} (ИНН: {profile['inn']})"

                supplier_id = db.add_supplier(
                    user_id=telegram_id,
                    name=profile_name,
                    token_id=token_id,
                    is_default=(i == 0 or profile.get('is_active', False))
                )
                suppliers_created += 1
                print(f"   ✅ Поставщик создан: {profile_name}")

        print()
        print("=" * 60)
        print("🎉 ГОТОВО!")
        print("=" * 60)
        print()
        print(f"✅ Сохранено:")
        print(f"   - Браузерная сессия (cookies)")
        print(f"   - {suppliers_created} поставщик(ов)")
        print()
        print("📱 Теперь запустите /start в Telegram боте")
        print("   → Авторизация НЕ потребуется!")
        print("   → Сразу откроется кнопка Mini App")
        print()
        print("=" * 60)

    except KeyboardInterrupt:
        print()
        print("⛔ Прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Закрываем браузер
        if browser_service:
            await browser_service.stop()


if __name__ == "__main__":
    asyncio.run(main())
