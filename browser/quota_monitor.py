"""
Мониторинг квот на складах Wildberries.

Функционал:
- Периодическая проверка доступных квот
- Сохранение истории для аналитики
- Уведомления при появлении слотов
- Автоматический запуск задач при наличии квоты
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable, Awaitable, Dict, List

from playwright.async_api import BrowserContext, Page

from .browser_service import BrowserService, get_browser_service
from utils.encryption import decrypt_token
from db_factory import get_database

logger = logging.getLogger(__name__)


@dataclass
class WarehouseQuota:
    """Информация о квоте склада"""
    warehouse_id: int
    warehouse_name: str
    available: int
    total: int
    checked_at: datetime


class QuotaMonitor:
    """Сервис мониторинга квот"""

    # URL страницы с информацией о квотах
    REDISTRIBUTION_URL = "https://seller.wildberries.ru/supplies-management/redistribution"

    # Популярные склады для мониторинга
    MONITORED_WAREHOUSES = [
        # Высокий лимит
        {'id': 117501, 'name': 'Котовск'},
        {'id': 117986, 'name': 'Краснодар (Тихорецкая)'},
        # Средний лимит
        {'id': 507, 'name': 'Коледино'},
        {'id': 206236, 'name': 'Тула'},
        {'id': 130744, 'name': 'Электросталь'},
        {'id': 210515, 'name': 'Невинномысск'},
        # Низкий лимит
        {'id': 218623, 'name': 'Казань'},
        {'id': 208941, 'name': 'Новосибирск'},
        {'id': 120762, 'name': 'Екатеринбург'},
        {'id': 301229, 'name': 'Белые Столбы'},
        {'id': 210001, 'name': 'Уткина Заводь'},
    ]

    def __init__(
        self,
        check_interval: int = 60,  # секунды
        notify_callback: Optional[Callable[[int, str], Awaitable[None]]] = None
    ):
        """
        Инициализация монитора.

        Args:
            check_interval: Интервал проверки в секундах
            notify_callback: Функция для уведомлений (user_id, message)
        """
        self.check_interval = check_interval
        self.notify_callback = notify_callback

        self._running = False
        self._browser_service: Optional[BrowserService] = None
        self._last_quotas: Dict[int, int] = {}  # warehouse_id -> last_available

    async def start(self) -> None:
        """Запуск мониторинга"""
        logger.info("Starting quota monitor")

        self._browser_service = await get_browser_service(headless=True)
        self._running = True

        await self._run_loop()

    async def stop(self) -> None:
        """Остановка мониторинга"""
        logger.info("Stopping quota monitor")
        self._running = False

    async def _run_loop(self) -> None:
        """Основной цикл мониторинга"""
        logger.info("Quota monitor loop started")

        while self._running:
            try:
                await self._check_all_quotas()
            except asyncio.CancelledError:
                logger.info("Quota monitor cancelled")
                break
            except Exception as e:
                logger.error(f"Quota monitor error: {e}", exc_info=True)

            await asyncio.sleep(self.check_interval)

        logger.info("Quota monitor loop stopped")

    async def _check_all_quotas(self) -> None:
        """Проверить квоты на всех складах"""
        db = get_database()

        # Получаем активную сессию для проверки (любого пользователя)
        # В реальности нужна отдельная сервисная сессия
        sessions = []
        # TODO: Использовать сервисную сессию

        # Для каждого склада логируем изменения
        now = datetime.now()

        for wh in self.MONITORED_WAREHOUSES:
            wh_id = wh['id']
            wh_name = wh['name']

            # В реальной реализации здесь парсинг страницы WB
            # Пока используем заглушку
            available = await self._get_warehouse_quota(wh_id)

            if available is not None:
                # Проверяем изменение
                last_available = self._last_quotas.get(wh_id, 0)

                if available > 0 and last_available == 0:
                    # Появились слоты! Уведомляем и запускаем задачи
                    logger.info(f"Quota appeared on {wh_name}: {available}")
                    await self._on_quota_available(wh_id, wh_name, available)

                self._last_quotas[wh_id] = available

                # Сохраняем в историю (опционально)
                # db.log_quota(wh_id, available, now)

    async def _get_warehouse_quota(self, warehouse_id: int) -> Optional[int]:
        """
        Получить текущую квоту склада.

        В реальной реализации это парсинг страницы WB.
        Пока возвращаем None (не реализовано).

        Args:
            warehouse_id: ID склада

        Returns:
            Доступная квота или None
        """
        # TODO: Реализовать парсинг квот со страницы WB
        # Это требует активной сессии и парсинга HTML

        # Заглушка - возвращаем None (не проверено)
        return None

    async def _on_quota_available(
        self,
        warehouse_id: int,
        warehouse_name: str,
        available: int
    ) -> None:
        """
        Обработка появления квоты на складе.

        Args:
            warehouse_id: ID склада
            warehouse_name: Название склада
            available: Доступная квота
        """
        db = get_database()

        # Находим задачи, ожидающие этот склад
        # SELECT * FROM redistribution_requests
        # WHERE target_warehouse_id = ? AND status = 'pending'
        # TODO: Добавить метод в database.py

        # Уведомляем пользователей с ожидающими задачами
        if self.notify_callback:
            # Получаем пользователей с pending задачами на этот склад
            # TODO: Реализовать запрос

            message = (
                f"🎉 Появились слоты на складе <b>{warehouse_name}</b>!\n"
                f"Доступно: {available} единиц\n\n"
                f"Ваши задачи будут выполнены автоматически."
            )

            # Отправляем уведомления
            # for user_id in affected_users:
            #     await self.notify_callback(user_id, message)

    async def get_current_quotas(self) -> Dict[int, int]:
        """
        Получить текущие квоты (из кэша).

        Returns:
            Dict {warehouse_id: available}
        """
        return self._last_quotas.copy()

    async def check_quota_for_task(
        self,
        cookies_encrypted: str,
        target_warehouse_id: int
    ) -> Optional[int]:
        """
        Проверить квоту для конкретной задачи.

        Args:
            cookies_encrypted: Зашифрованные cookies
            target_warehouse_id: ID целевого склада

        Returns:
            Доступная квота или None
        """
        if not self._browser_service:
            self._browser_service = await get_browser_service(headless=True)

        context: Optional[BrowserContext] = None

        try:
            # Расшифровываем cookies
            cookies_json = decrypt_token(cookies_encrypted)
            cookies = self._browser_service.deserialize_cookies(cookies_json)

            # Создаём контекст
            context = await self._browser_service.create_context(cookies=cookies)
            page = await self._browser_service.create_page(context)

            # Открываем страницу
            await page.goto(self.REDISTRIBUTION_URL, wait_until='networkidle')
            await self._browser_service.human_delay(2000, 3000)

            # Проверяем авторизацию
            if '/login' in page.url:
                logger.warning("Session expired during quota check")
                return None

            # Парсим квоту для склада
            # TODO: Реализовать парсинг HTML

            return None

        except Exception as e:
            logger.error(f"Error checking quota: {e}")
            return None

        finally:
            if context:
                await context.close()


# Singleton instance
_quota_monitor: Optional[QuotaMonitor] = None


async def get_quota_monitor(
    check_interval: int = 60,
    notify_callback: Optional[Callable[[int, str], Awaitable[None]]] = None
) -> QuotaMonitor:
    """Получить singleton instance QuotaMonitor"""
    global _quota_monitor

    if _quota_monitor is None:
        _quota_monitor = QuotaMonitor(
            check_interval=check_interval,
            notify_callback=notify_callback
        )

    return _quota_monitor


async def shutdown_quota_monitor() -> None:
    """Корректное завершение QuotaMonitor"""
    global _quota_monitor

    if _quota_monitor:
        await _quota_monitor.stop()
        _quota_monitor = None
