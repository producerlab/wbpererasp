"""
Тестовый скрипт для проверки автоматического перераспределения.

Использование:
    python test_redistribution.py --phone +79991234567 --nm-id 123456789 --from-warehouse 1 --to-warehouse 2 --quantity 1

Или с headless=False для визуального контроля:
    python test_redistribution.py --phone +79991234567 --nm-id 123456789 --from-warehouse 1 --to-warehouse 2 --quantity 1 --visible
"""

import asyncio
import argparse
import logging
import sys
import sqlite3
from pathlib import Path

# Добавляем путь к модулям проекта
sys.path.insert(0, str(Path(__file__).parent))

from browser.redistribution import get_redistribution_service, RedistributionStatus
from browser.browser_service import BrowserService

# Путь к БД напрямую
DATABASE_PATH = "bot_data.db"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_redistribution.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


async def get_user_session(phone: str) -> tuple[int, str]:
    """
    Получить user_id и зашифрованные cookies из БД.

    Args:
        phone: Номер телефона пользователя

    Returns:
        Tuple (user_id, cookies_encrypted)
    """
    # Подключаемся к БД напрямую
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Ищем активную сессию по номеру телефона
    query = """
        SELECT user_id, cookies_encrypted
        FROM browser_sessions
        WHERE phone = ? AND status = 'active'
        AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        ORDER BY last_used_at DESC
        LIMIT 1
    """

    cursor.execute(query, (phone,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        logger.error(f"Сессия не найдена для номера {phone}")
        logger.info("Подсказка: сначала авторизуйтесь через /start в Telegram боте")
        sys.exit(1)

    user_id, cookies_encrypted = result
    logger.info(f"Найдена сессия для user_id={user_id}")

    return user_id, cookies_encrypted


async def test_redistribution(
    phone: str,
    nm_id: int,
    source_warehouse: int,
    target_warehouse: int,
    quantity: int,
    headless: bool = True
):
    """
    Протестировать автоматическое перераспределение.

    Args:
        phone: Номер телефона для поиска сессии
        nm_id: Артикул товара
        source_warehouse: ID склада-источника
        target_warehouse: ID склада-назначения
        quantity: Количество для перемещения
        headless: Запускать браузер в headless режиме
    """
    logger.info("=" * 80)
    logger.info("ТЕСТИРОВАНИЕ АВТОМАТИЧЕСКОГО ПЕРЕРАСПРЕДЕЛЕНИЯ")
    logger.info("=" * 80)
    logger.info(f"Параметры:")
    logger.info(f"  - Телефон: {phone}")
    logger.info(f"  - Артикул: {nm_id}")
    logger.info(f"  - Со склада: {source_warehouse}")
    logger.info(f"  - На склад: {target_warehouse}")
    logger.info(f"  - Количество: {quantity}")
    logger.info(f"  - Headless: {headless}")
    logger.info("=" * 80)

    # Получаем сессию из БД
    logger.info("Шаг 1: Получение сессии из БД...")
    try:
        user_id, cookies_encrypted = await get_user_session(phone)
        logger.info(f"✅ Сессия найдена для user_id={user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при получении сессии: {e}")
        return

    # Получаем сервис перераспределения
    logger.info("\nШаг 2: Инициализация сервиса перераспределения...")
    redistribution_service = get_redistribution_service()

    # Обновляем headless режим если нужен визуальный контроль
    if not headless:
        logger.info("⚠️ ВНИМАНИЕ: Запуск в режиме с GUI (браузер будет виден)")
        # Нужно создать новый browser service с headless=False
        from browser.browser_service import BrowserService
        browser = BrowserService(headless=False)
        await browser.start()
        redistribution_service._browser_service = browser

    # Выполняем перераспределение
    logger.info("\nШаг 3: Выполнение перераспределения...")
    logger.info("⏳ Это может занять 10-30 секунд...")

    try:
        result = await redistribution_service.execute_redistribution(
            cookies_encrypted=cookies_encrypted,
            nm_id=nm_id,
            source_warehouse_id=source_warehouse,
            target_warehouse_id=target_warehouse,
            quantity=quantity
        )

        # Выводим результат
        logger.info("\n" + "=" * 80)
        logger.info("РЕЗУЛЬТАТ")
        logger.info("=" * 80)
        logger.info(f"Статус: {result.status.value}")
        logger.info(f"Сообщение: {result.message}")

        if result.supply_id:
            logger.info(f"ID заявки: {result.supply_id}")

        if result.screenshot:
            screenshot_path = f"screenshots/test_redistribution_{nm_id}.png"
            Path(screenshot_path).parent.mkdir(exist_ok=True)
            with open(screenshot_path, 'wb') as f:
                f.write(result.screenshot)
            logger.info(f"Скриншот сохранён: {screenshot_path}")

        logger.info("=" * 80)

        # Интерпретация результата
        if result.status == RedistributionStatus.SUCCESS:
            logger.info("\n✅ УСПЕХ! Заявка на перемещение создана")
            logger.info("📝 Следующий шаг: Проверьте заявку в ЛК Wildberries")
            logger.info("💡 Совет: Если всё работает, можно переходить к интеграции с Mini App")
            return True

        elif result.status == RedistributionStatus.SESSION_EXPIRED:
            logger.warning("\n⚠️ Сессия истекла")
            logger.info("📝 Следующий шаг: Повторите авторизацию через /start в боте")
            return False

        elif result.status == RedistributionStatus.NO_QUOTA:
            logger.warning("\n⚠️ Нет квоты на выбранном складе")
            logger.info("📝 Следующий шаг: Попробуйте другой склад или подождите появления квоты")
            return False

        elif result.status == RedistributionStatus.INVALID_ARTICLE:
            logger.error("\n❌ Артикул не найден")
            logger.info("📝 Следующий шаг: Проверьте правильность nm_id")
            logger.info("💡 Подсказка: Используйте артикул с остатками на выбранном складе")
            return False

        elif result.status == RedistributionStatus.INVALID_QUANTITY:
            logger.error("\n❌ Недостаточно остатков")
            logger.info("📝 Следующий шаг: Уменьшите количество или проверьте остатки")
            return False

        else:
            logger.error(f"\n❌ Ошибка: {result.message}")
            logger.info("📝 Следующий шаг: Проверьте скриншот и логи для диагностики")
            logger.info("💡 Возможно, нужно уточнить селекторы в browser/redistribution.py")
            return False

    except Exception as e:
        logger.error(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: {e}", exc_info=True)
        logger.info("📝 Следующий шаг: Проверьте логи выше для деталей")
        return False

    finally:
        # Останавливаем браузер если запускали
        if not headless and redistribution_service._browser_service:
            await redistribution_service._browser_service.stop()


def main():
    """Точка входа для тестового скрипта"""
    parser = argparse.ArgumentParser(
        description='Тест автоматического перераспределения Wildberries',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:

  1. Тест с headless браузером (по умолчанию):
     python test_redistribution.py --phone +79991234567 --nm-id 123456789 --from-warehouse 1 --to-warehouse 2 --quantity 1

  2. Тест с визуальным контролем (браузер будет виден):
     python test_redistribution.py --phone +79991234567 --nm-id 123456789 --from-warehouse 1 --to-warehouse 2 --quantity 1 --visible

  3. Получить справку:
     python test_redistribution.py --help

Перед запуском:
  - Убедитесь, что авторизовались через /start в Telegram боте
  - Проверьте, что указанный артикул есть в наличии на складе-источнике
  - Убедитесь, что на складе-назначении доступна квота
        """
    )

    parser.add_argument(
        '--phone',
        required=True,
        help='Номер телефона для поиска сессии (например, +79991234567)'
    )

    parser.add_argument(
        '--nm-id',
        type=int,
        required=True,
        help='Артикул товара (nmId)'
    )

    parser.add_argument(
        '--from-warehouse',
        type=int,
        required=True,
        help='ID склада-источника'
    )

    parser.add_argument(
        '--to-warehouse',
        type=int,
        required=True,
        help='ID склада-назначения'
    )

    parser.add_argument(
        '--quantity',
        type=int,
        required=True,
        help='Количество для перемещения'
    )

    parser.add_argument(
        '--visible',
        action='store_true',
        help='Запустить браузер с GUI (для визуального контроля)'
    )

    args = parser.parse_args()

    # Запускаем тест
    headless = not args.visible
    success = asyncio.run(test_redistribution(
        phone=args.phone,
        nm_id=args.nm_id,
        source_warehouse=args.from_warehouse,
        target_warehouse=args.to_warehouse,
        quantity=args.quantity,
        headless=headless
    ))

    # Возвращаем код выхода
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
