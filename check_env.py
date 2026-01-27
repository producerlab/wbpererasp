"""
Проверка переменных окружения для диагностики проблем.

Запуск:
    python check_env.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

def check_env():
    """Проверяет критические переменные окружения"""
    print("=" * 60)
    print("ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ")
    print("=" * 60)

    # BOT_TOKEN
    bot_token = os.getenv('BOT_TOKEN', '')
    if bot_token:
        print(f"✅ BOT_TOKEN: {bot_token[:20]}... (length: {len(bot_token)})")
    else:
        print("❌ BOT_TOKEN: НЕ ЗАДАН")

    # ADMIN_IDS
    admin_ids = os.getenv('ADMIN_IDS', '')
    if admin_ids:
        ids = [x.strip() for x in admin_ids.split(',') if x.strip().isdigit()]
        print(f"✅ ADMIN_IDS: {ids} (count: {len(ids)})")
    else:
        print("❌ ADMIN_IDS: НЕ ЗАДАН")

    # WEBAPP_URL
    webapp_url = os.getenv('WEBAPP_URL', '')
    if webapp_url:
        print(f"✅ WEBAPP_URL: {webapp_url}")
        if webapp_url.startswith('http://localhost'):
            print("   ⚠️  ВНИМАНИЕ: localhost URL не работает в продакшене!")
            print("   💡 Для Railway установите: https://your-app.up.railway.app")
        elif webapp_url.startswith('https://'):
            print("   ✅ HTTPS URL - корректно для продакшена")
        elif webapp_url.startswith('http://'):
            print("   ⚠️  HTTP URL - может не работать с Telegram Mini App")
    else:
        print("❌ WEBAPP_URL: НЕ ЗАДАН (будет использован http://localhost:8080)")

    # WB_ENCRYPTION_KEY
    encryption_key = os.getenv('WB_ENCRYPTION_KEY', '')
    if encryption_key:
        print(f"✅ WB_ENCRYPTION_KEY: {'*' * 20} (length: {len(encryption_key)})")
        if len(encryption_key) < 32:
            print("   ⚠️  ВНИМАНИЕ: Ключ слишком короткий!")
    else:
        print("❌ WB_ENCRYPTION_KEY: НЕ ЗАДАН")

    # DATABASE_URL
    database_url = os.getenv('DATABASE_URL', '')
    if database_url:
        # Скрываем пароль
        if '@' in database_url:
            parts = database_url.split('@')
            hidden = parts[0].split(':')[0] + ':***@' + parts[1]
            print(f"✅ DATABASE_URL: {hidden}")
            print("   📊 Используется PostgreSQL (Railway)")
        else:
            print(f"✅ DATABASE_URL: {database_url[:30]}...")
    else:
        database_path = os.getenv('DATABASE_PATH', 'bot_data.db')
        print(f"ℹ️  DATABASE_URL: не задан")
        print(f"   📊 Используется SQLite: {database_path}")

    print("=" * 60)
    print()

    # Финальная диагностика
    issues = []

    if not bot_token:
        issues.append("BOT_TOKEN не задан")
    if not encryption_key:
        issues.append("WB_ENCRYPTION_KEY не задан")
    elif len(encryption_key) < 32:
        issues.append("WB_ENCRYPTION_KEY слишком короткий")
    if webapp_url and webapp_url.startswith('http://localhost') and database_url:
        issues.append("WEBAPP_URL = localhost, но используется PostgreSQL (продакшен)")

    if issues:
        print("❌ НАЙДЕНЫ ПРОБЛЕМЫ:")
        for issue in issues:
            print(f"   - {issue}")
        print()
        print("💡 Исправьте эти проблемы в Railway:")
        print("   1. Перейдите в Variables на Railway")
        print("   2. Исправьте указанные переменные")
        print("   3. Перезапустите деплоймент")
    else:
        print("✅ ВСЕ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ КОРРЕКТНЫ")

    print()

if __name__ == "__main__":
    check_env()
