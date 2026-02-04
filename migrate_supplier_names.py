#!/usr/bin/env python3
"""
Миграция: обновление названий поставщиков с названий магазинов на ФИО владельцев.

Скрипт проходит по всем активным browser_sessions, перепарсивает профили из WB
и обновляет названия suppliers.

Запуск:
    python migrate_supplier_names.py
"""

import asyncio
import json
import os
import sys
import logging
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Импортируем необходимые модули
from database_pg import DatabasePostgres
from browser.auth import WBAuthService
from config import Config
from crypto_utils import decrypt_token


async def migrate_suppliers():
    """Основная функция миграции"""

    logger.info("=" * 60)
    logger.info("Миграция названий поставщиков: магазин -> ФИО владельца")
    logger.info("=" * 60)

    # Подключаемся к БД
    db = DatabasePostgres()

    # Получаем все активные browser_sessions
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT bs.id, bs.user_id, bs.phone_last4, bs.cookies_encrypted, bs.supplier_name
            FROM browser_sessions bs
            WHERE bs.status = 'active'
            ORDER BY bs.user_id
        """)
        sessions = cursor.fetchall()

    logger.info(f"Найдено {len(sessions)} активных browser_sessions")

    if not sessions:
        logger.info("Нет активных сессий для миграции")
        return

    # Инициализируем auth service
    auth_service = WBAuthService()

    migrated = 0
    errors = 0

    for session in sessions:
        session_id = session['id']
        user_id = session['user_id']
        phone_last4 = session['phone_last4'] or '????'
        cookies_encrypted = session['cookies_encrypted']

        logger.info(f"\n--- Обрабатываю сессию {session_id} (user_id={user_id}, phone=***{phone_last4}) ---")

        if not cookies_encrypted:
            logger.warning(f"  Сессия {session_id}: нет cookies, пропускаю")
            errors += 1
            continue

        try:
            # Расшифровываем cookies
            cookies_json = decrypt_token(cookies_encrypted)
            cookies = json.loads(cookies_json)

            if not cookies:
                logger.warning(f"  Сессия {session_id}: пустые cookies, пропускаю")
                errors += 1
                continue

            logger.info(f"  Cookies расшифрованы: {len(cookies)} штук")

            # Запускаем браузер и парсим профили
            profiles = await parse_profiles_with_cookies(auth_service, cookies)

            if not profiles:
                logger.warning(f"  Сессия {session_id}: не удалось получить профили")
                errors += 1
                continue

            logger.info(f"  Найдено {len(profiles)} профилей")

            # Получаем текущих suppliers для этого пользователя
            with db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, name, token_id
                    FROM suppliers
                    WHERE user_id = %s
                    ORDER BY id
                """, (user_id,))
                suppliers = cursor.fetchall()

            logger.info(f"  Текущих suppliers в БД: {len(suppliers)}")

            # Сопоставляем suppliers с профилями по ИНН
            updated = 0
            for supplier in suppliers:
                supplier_id = supplier['id']
                old_name = supplier['name']

                # Извлекаем ИНН из старого названия
                import re
                inn_match = re.search(r'ИНН:\s*(\d{10,13})', old_name)
                if not inn_match:
                    logger.info(f"    Supplier {supplier_id}: нет ИНН в названии '{old_name}', пропускаю")
                    continue

                inn = inn_match.group(1)

                # Ищем профиль с таким ИНН
                matching_profile = None
                for profile in profiles:
                    if profile.get('inn') == inn:
                        matching_profile = profile
                        break

                if not matching_profile:
                    logger.info(f"    Supplier {supplier_id}: профиль с ИНН {inn} не найден в WB")
                    continue

                # Формируем новое название (ФИО приоритет)
                new_base_name = matching_profile.get('name') or matching_profile.get('company')
                new_name = f"{new_base_name} (ИНН: {inn})"

                if new_name == old_name:
                    logger.info(f"    Supplier {supplier_id}: название уже актуальное")
                    continue

                # Обновляем в БД
                with db._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE suppliers
                        SET name = %s
                        WHERE id = %s
                    """, (new_name, supplier_id))

                logger.info(f"    ✅ Supplier {supplier_id}: '{old_name}' -> '{new_name}'")
                updated += 1

            if updated > 0:
                migrated += updated
                logger.info(f"  Обновлено {updated} suppliers")
            else:
                logger.info(f"  Обновлений не требуется")

        except Exception as e:
            logger.error(f"  Ошибка при обработке сессии {session_id}: {e}")
            errors += 1
            continue

    # Закрываем auth service
    await auth_service.cleanup()

    logger.info("\n" + "=" * 60)
    logger.info(f"Миграция завершена:")
    logger.info(f"  - Обновлено suppliers: {migrated}")
    logger.info(f"  - Ошибок: {errors}")
    logger.info("=" * 60)


async def parse_profiles_with_cookies(auth_service: WBAuthService, cookies: list) -> list:
    """
    Запускает браузер с cookies и парсит профили из WB.
    """
    from playwright.async_api import async_playwright
    import re

    profiles = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        # Устанавливаем cookies
        await context.add_cookies(cookies)

        page = await context.new_page()

        try:
            # Переходим в seller.wildberries.ru
            await page.goto('https://seller.wildberries.ru/', wait_until='networkidle', timeout=30000)
            await asyncio.sleep(2)

            # Ищем dropdown с профилями (верхний правый угол)
            # Обычно это элемент с именем текущего пользователя
            profile_selectors = [
                '[class*="Profile"]',
                '[class*="profile"]',
                '[class*="Account"]',
                '[class*="account"]',
                '[class*="User"]',
                '[class*="user"]',
                'header [class*="right"]',
            ]

            profile_trigger = None
            for selector in profile_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for el in elements:
                        box = await el.bounding_box()
                        if box and box['x'] > 1000:  # Правая часть экрана
                            profile_trigger = el
                            break
                    if profile_trigger:
                        break
                except:
                    continue

            if profile_trigger:
                # Наводим мышь чтобы открыть dropdown
                await profile_trigger.hover()
                await asyncio.sleep(1)

            # Парсим профили (ищем элементы с ИНН)
            all_elements = await page.query_selector_all('div, span, li, a')

            seen_inns = set()

            for el in all_elements:
                try:
                    text = await el.inner_text()
                    if not text:
                        continue

                    # Ищем ИНН
                    inn_match = re.search(r'ИНН\s*(\d{10,13})', text)
                    if not inn_match:
                        continue

                    inn = inn_match.group(1)

                    if inn in seen_inns:
                        continue
                    seen_inns.add(inn)

                    # Парсим ID
                    id_match = re.search(r'ID\s*(\d+)', text)

                    # Разбиваем на строки
                    lines = [line.strip() for line in text.split('\n') if line.strip()]

                    name = ''
                    company = ''

                    for line in lines:
                        if 'ИНН' in line or 'ID' in line.upper():
                            continue
                        if not name:
                            name = line
                        elif not company and line != name:
                            company = line
                            break

                    if name:
                        profiles.append({
                            'name': name,
                            'company': company,
                            'inn': inn,
                            'id': id_match.group(1) if id_match else ''
                        })
                        logger.debug(f"    Найден профиль: {name} | {company} | ИНН {inn}")

                except Exception:
                    continue

        except Exception as e:
            logger.error(f"Ошибка при парсинге профилей: {e}")
        finally:
            await browser.close()

    return profiles


if __name__ == '__main__':
    asyncio.run(migrate_suppliers())
