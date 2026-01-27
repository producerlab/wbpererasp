"""
Сервис мониторинга коэффициентов приёмки WB.

ОТКЛЮЧЕН! Этот модуль больше не используется.
"""

raise RuntimeError(
    "❌ МОНИТОРИНГ КОЭФФИЦИЕНТОВ ОТКЛЮЧЕН!\n"
    "Этот модуль больше не должен импортироваться.\n"
    "Если вы видите эту ошибку - обновите код, который импортирует coefficient_monitor"
)

import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Optional, Callable, Awaitable
from dataclasses import dataclass

from config import Config
from database import Database
from wb_api.client import WBApiClient, Endpoint
from wb_api.coefficients import CoefficientsAPI, Coefficient, CoefficientChange

logger = logging.getLogger(__name__)


@dataclass
class MonitoringEvent:
    """Событие мониторинга для передачи в callback"""
    change: CoefficientChange
    subscriptions: List[Dict]
    timestamp: datetime


class CoefficientMonitor:
    """
    Сервис мониторинга коэффициентов приёмки.

    Использование:
        monitor = CoefficientMonitor(db, api_token)
        monitor.on_change(callback_function)
        await monitor.start()

    Callback получает MonitoringEvent с изменением и подписками.
    """

    def __init__(
        self,
        db: Database,
        api_token: str,
        poll_interval: int = None
    ):
        """
        Args:
            db: Экземпляр Database
            api_token: Системный WB API токен (для получения коэффициентов)
            poll_interval: Интервал опроса в секундах (по умолчанию из Config)
        """
        self.db = db
        self.api_token = api_token
        self.poll_interval = poll_interval or Config.COEFFICIENT_POLL_INTERVAL

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable[[MonitoringEvent], Awaitable[None]]] = []

        # Кэш предыдущих коэффициентов
        self._previous_coefficients: Dict[str, Coefficient] = {}

        # Статистика
        self._polls_count = 0
        self._changes_detected = 0
        self._last_poll_time: Optional[datetime] = None

    def on_change(
        self,
        callback: Callable[[MonitoringEvent], Awaitable[None]]
    ):
        """
        Регистрирует callback для обработки изменений.

        Args:
            callback: Async функция, принимающая MonitoringEvent
        """
        self._callbacks.append(callback)

    async def start(self):
        """Запускает мониторинг в фоне"""
        if self._running:
            logger.warning("Monitor already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._polling_loop())
        logger.info(f"Coefficient monitor started (interval: {self.poll_interval}s)")

    async def stop(self):
        """Останавливает мониторинг"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Coefficient monitor stopped")

    async def _polling_loop(self):
        """Основной цикл опроса"""
        while self._running:
            try:
                await self._poll_and_process()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in polling loop: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    async def _poll_and_process(self):
        """Один цикл опроса и обработки"""
        self._polls_count += 1
        self._last_poll_time = datetime.now()

        async with WBApiClient(self.api_token) as client:
            api = CoefficientsAPI(client)

            # 1. Получаем текущие коэффициенты
            try:
                current = await api.get_acceptance_coefficients()
            except Exception as e:
                logger.error(f"Failed to fetch coefficients: {e}")
                return

            if not current:
                logger.debug("No coefficients received")
                return

            # 2. Детектим изменения
            changes = self._detect_changes(current)

            if not changes:
                logger.debug(f"No changes detected (poll #{self._polls_count})")
                return

            logger.info(f"Detected {len(changes)} coefficient changes")
            self._changes_detected += len(changes)

            # 3. Сохраняем историю
            await self._save_history(changes)

            # 4. Обрабатываем каждое изменение
            for change in changes:
                await self._process_change(change)

    def _detect_changes(
        self,
        current: List[Coefficient]
    ) -> List[CoefficientChange]:
        """Детектирует изменения коэффициентов"""
        changes = []

        for coeff in current:
            key = f"{coeff.warehouse_id}_{coeff.date}_{coeff.box_type_id}"

            if key in self._previous_coefficients:
                old = self._previous_coefficients[key]
                if old.coefficient != coeff.coefficient:
                    change = CoefficientChange(
                        warehouse_id=coeff.warehouse_id,
                        warehouse_name=coeff.warehouse_name,
                        old_coefficient=old.coefficient,
                        new_coefficient=coeff.coefficient,
                        date=coeff.date,
                        box_type_id=coeff.box_type_id,
                        priority=self._calculate_priority(
                            old.coefficient, coeff.coefficient
                        )
                    )
                    changes.append(change)

            # Обновляем кэш
            self._previous_coefficients[key] = coeff

        # Сортируем по приоритету
        return sorted(changes, key=lambda c: c.priority, reverse=True)

    def _calculate_priority(self, old: float, new: float) -> int:
        """Рассчитывает приоритет изменения"""
        if new == 0:
            return 100  # Бесплатно!
        elif new == 0.5:
            return 90
        elif new == 1.0:
            return 80
        elif old < 0 and new >= 0:
            return 70  # Стало доступно
        elif new < old:
            return 50  # Снижение
        else:
            return 10  # Повышение

    async def _save_history(self, changes: List[CoefficientChange]):
        """Сохраняет изменения в историю"""
        for change in changes:
            try:
                # Конвертируем дату в строку если нужно
                date_str = change.date
                if hasattr(change.date, 'isoformat'):
                    date_str = change.date.isoformat()

                self.db.add_coefficient_record(
                    warehouse_id=change.warehouse_id,
                    coefficient=change.new_coefficient,
                    date=date_str
                )
            except Exception as e:
                logger.error(f"Failed to save coefficient history: {e}")

    async def _process_change(self, change: CoefficientChange):
        """Обрабатывает одно изменение"""
        # Находим подписки для этого склада
        subscriptions = self.db.get_active_subscriptions_for_warehouses(
            warehouse_ids=[change.warehouse_id]
        )

        # Фильтруем по целевым коэффициентам
        filtered_subs = []
        for sub in subscriptions:
            target_coeffs = sub.get('target_coefficients', [])
            # Проверяем что новый коэффициент <= максимальному целевому
            if target_coeffs and change.new_coefficient <= max(target_coeffs):
                filtered_subs.append(sub)
        subscriptions = filtered_subs

        if not subscriptions:
            logger.debug(f"No subscriptions for warehouse {change.warehouse_id}")
            return

        logger.info(
            f"Found {len(subscriptions)} subscriptions for "
            f"warehouse {change.warehouse_name} (coeff: {change.new_coefficient})"
        )

        # Создаём событие
        event = MonitoringEvent(
            change=change,
            subscriptions=subscriptions,
            timestamp=datetime.now()
        )

        # Вызываем все callback'и параллельно
        tasks = [callback(event) for callback in self._callbacks]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Callback {i} failed: {result}")

    async def poll_once(self) -> List[CoefficientChange]:
        """
        Выполняет один опрос (для ручного запуска).

        Returns:
            Список обнаруженных изменений
        """
        async with WBApiClient(self.api_token) as client:
            api = CoefficientsAPI(client)
            current = await api.get_acceptance_coefficients()
            return self._detect_changes(current)

    async def get_current_coefficients(
        self,
        warehouse_ids: List[int] = None
    ) -> List[Coefficient]:
        """
        Получает текущие коэффициенты.

        Args:
            warehouse_ids: Фильтр по складам

        Returns:
            Список коэффициентов
        """
        async with WBApiClient(self.api_token) as client:
            api = CoefficientsAPI(client)
            coefficients = await api.get_acceptance_coefficients(warehouse_ids)

            # Фильтруем если нужно
            if warehouse_ids:
                return [c for c in coefficients if c.warehouse_id in warehouse_ids]
            return coefficients

    async def get_profitable_slots(
        self,
        max_coefficient: float = 1.0,
        warehouse_ids: List[int] = None
    ) -> List[Coefficient]:
        """
        Получает выгодные слоты.

        Args:
            max_coefficient: Максимальный коэффициент
            warehouse_ids: Фильтр по складам

        Returns:
            Список выгодных коэффициентов
        """
        async with WBApiClient(self.api_token) as client:
            api = CoefficientsAPI(client)
            return await api.get_profitable_slots(max_coefficient, warehouse_ids)

    def get_stats(self) -> Dict:
        """Возвращает статистику мониторинга"""
        return {
            'running': self._running,
            'polls_count': self._polls_count,
            'changes_detected': self._changes_detected,
            'last_poll_time': self._last_poll_time,
            'cached_coefficients': len(self._previous_coefficients),
            'callbacks_count': len(self._callbacks),
        }

    def clear_cache(self):
        """Очищает кэш коэффициентов"""
        self._previous_coefficients.clear()
        logger.info("Coefficient cache cleared")
