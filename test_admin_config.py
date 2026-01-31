#!/usr/bin/env python3
"""
Тестовый скрипт для проверки парсинга ADMIN_IDS
"""
import os
from config import Config

print("=" * 50)
print("🔍 Проверка конфигурации ADMIN_IDS")
print("=" * 50)

# Показываем сырое значение из env
raw_admin_ids = os.getenv('ADMIN_IDS', '')
print(f"\n📝 Сырое значение ADMIN_IDS из env:")
print(f"   '{raw_admin_ids}'")

# Показываем распарсенный список
print(f"\n✅ Распарсенный Config.ADMIN_IDS:")
print(f"   {Config.ADMIN_IDS}")
print(f"   Тип: {type(Config.ADMIN_IDS)}")
print(f"   Количество: {len(Config.ADMIN_IDS)}")

# Проверяем конкретный ID
test_user_id = 98314773
is_admin = test_user_id in Config.ADMIN_IDS
print(f"\n🎯 Проверка для user_id={test_user_id}:")
print(f"   Это админ? {is_admin}")

if Config.ADMIN_IDS:
    print(f"\n👥 Список админов:")
    for admin_id in Config.ADMIN_IDS:
        print(f"   - {admin_id} (тип: {type(admin_id).__name__})")

print("\n" + "=" * 50)
