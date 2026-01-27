#!/usr/bin/env python3
"""
Setup script для WB Redistribution Bot.

Используйте этот скрипт для первоначальной настройки безопасности бота:
- Генерация encryption key
- Проверка file permissions
- Валидация конфигурации
"""

import os
import sys
from pathlib import Path
from cryptography.fernet import Fernet


def print_header(text: str):
    """Красиво печатает заголовок"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")


def generate_encryption_key():
    """Генерирует новый encryption key"""
    print_header("🔐 Генерация Encryption Key")

    key = Fernet.generate_key().decode()

    print("Сгенерирован новый encryption key:")
    print(f"\n{key}\n")
    print("✅ ВАЖНО: Сохраните этот ключ в безопасном месте!\n")
    print("Добавьте его в файл .env:")
    print(f"WB_ENCRYPTION_KEY={key}\n")
    print("⚠️  НИКОГДА не коммитьте .env файл в git!")
    print("⚠️  В production используйте secrets manager (AWS Secrets, Vault, etc.)\n")

    return key


def check_file_permissions():
    """Проверяет permissions критичных файлов"""
    print_header("🔒 Проверка File Permissions")

    project_root = Path(__file__).parent.parent
    critical_files = {
        '.env': '600',
        'bot_data.db': '600',
    }

    issues = []

    for filename, expected_mode in critical_files.items():
        filepath = project_root / filename

        if not filepath.exists():
            print(f"⚪ {filename} - не существует (будет создан)")
            continue

        # Получить текущие permissions
        current_mode = oct(filepath.stat().st_mode)[-3:]

        if current_mode == expected_mode:
            print(f"✅ {filename} - корректные права ({current_mode})")
        else:
            print(f"❌ {filename} - НЕПРАВИЛЬНЫЕ права!")
            print(f"   Текущие: {current_mode}, Должны быть: {expected_mode}")
            print(f"   Исправьте: chmod {expected_mode} {filename}")
            issues.append((filename, expected_mode))

    if issues:
        print("\n⚠️  Обнаружены проблемы с permissions!")
        print("Запустите скрипт для автоматического исправления:")
        print("  bash scripts/setup_permissions.sh\n")
        return False
    else:
        print("\n✅ Все file permissions корректны!\n")
        return True


def check_env_file():
    """Проверяет наличие и содержимое .env файла"""
    print_header("📝 Проверка .env файла")

    project_root = Path(__file__).parent.parent
    env_file = project_root / '.env'

    if not env_file.exists():
        print("❌ Файл .env не найден!")
        print("\nСоздайте файл .env на основе .env.example:")
        print("  cp .env.example .env")
        print("\nИли создайте вручную со следующими параметрами:")
        print("  BOT_TOKEN=your_telegram_bot_token")
        print("  WB_ENCRYPTION_KEY=generated_key_from_above")
        print("  DATABASE_PATH=bot_data.db\n")
        return False

    # Прочитать .env и проверить ключевые параметры
    env_content = env_file.read_text()

    required_vars = {
        'BOT_TOKEN': False,
        'WB_ENCRYPTION_KEY': False
    }

    for line in env_content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        for var in required_vars:
            if line.startswith(f'{var}='):
                value = line.split('=', 1)[1].strip()
                if value and value != 'your_token_here':
                    required_vars[var] = True

                    # Дополнительная валидация для encryption key
                    if var == 'WB_ENCRYPTION_KEY' and len(value) < 32:
                        print(f"⚠️  {var} слишком короткий ({len(value)} символов)")
                        print("   Минимум 32 символа требуется для безопасности")
                        required_vars[var] = False

    # Вывести результаты
    all_ok = True
    for var, is_set in required_vars.items():
        if is_set:
            print(f"✅ {var} - настроен")
        else:
            print(f"❌ {var} - НЕ настроен или некорректен")
            all_ok = False

    if not all_ok:
        print("\n⚠️  Необходимо настроить отсутствующие переменные в .env\n")
        return False
    else:
        print("\n✅ Все обязательные переменные настроены!\n")
        return True


def validate_encryption():
    """Тестирует шифрование/дешифрование"""
    print_header("🧪 Тестирование шифрования")

    try:
        # Добавить путь к проекту в sys.path
        project_root = Path(__file__).parent.parent
        sys.path.insert(0, str(project_root))

        from utils.encryption import encrypt_token, decrypt_token

        # Тестовый токен
        test_token = "test_token_123456789"

        print("Тестирую шифрование...")
        encrypted = encrypt_token(test_token)
        print(f"✅ Токен зашифрован: {encrypted[:50]}...")

        print("Тестирую дешифрование...")
        decrypted = decrypt_token(encrypted)

        if decrypted == test_token:
            print("✅ Токен успешно дешифрован!")
            print("\n✅ Шифрование работает корректно!\n")
            return True
        else:
            print("❌ Ошибка: дешифрованный токен не совпадает с оригиналом")
            return False

    except RuntimeError as e:
        print(f"❌ Ошибка шифрования: {e}")
        print("\nВозможно, WB_ENCRYPTION_KEY не настроен в .env")
        return False
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        return False


def main():
    """Главная функция"""
    print("\n" + "🚀" * 30)
    print(" " * 20 + "WB Redistribution Bot")
    print(" " * 25 + "Security Setup")
    print("🚀" * 30 + "\n")

    print("Этот скрипт поможет настроить безопасность бота.\n")

    # Шаг 1: Генерация ключа
    response = input("Сгенерировать новый encryption key? (y/n): ").lower()
    if response == 'y':
        key = generate_encryption_key()
        input("Нажмите Enter после добавления ключа в .env...")

    # Шаг 2: Проверка .env
    env_ok = check_env_file()

    # Шаг 3: Проверка permissions
    perms_ok = check_file_permissions()

    # Шаг 4: Тестирование шифрования
    if env_ok:
        encrypt_ok = validate_encryption()
    else:
        encrypt_ok = False
        print("⏭️  Пропускаю тестирование шифрования (сначала настройте .env)\n")

    # Финальный отчет
    print_header("📊 Итоговый отчет")

    checks = {
        "Файл .env": "✅" if env_ok else "❌",
        "File permissions": "✅" if perms_ok else "❌",
        "Шифрование": "✅" if encrypt_ok else "❌"
    }

    for check, status in checks.items():
        print(f"{status} {check}")

    all_passed = all([env_ok, perms_ok, encrypt_ok])

    if all_passed:
        print("\n🎉 Отлично! Все проверки пройдены!")
        print("\nТеперь вы можете запустить бота:")
        print("  python bot.py\n")
    else:
        print("\n⚠️  Некоторые проверки не пройдены.")
        print("Исправьте проблемы выше перед запуском бота.\n")
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⛔ Прервано пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)
