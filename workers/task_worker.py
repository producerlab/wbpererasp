"""
Worker для обработки задач перемещения из очереди.

Функционал:
- Получение задач из Redis очереди
- Выполнение перемещений через браузер
- Отправка уведомлений о результате
- Списание/возврат средств
"""

import asyncio
import logging
from typing import Optional, Callable, Awaitable

from .queue import TaskQueue, Task, TaskStatus, get_task_queue
from browser.redistribution import WBRedistributionService, RedistributionStatus, get_redistribution_service
from payments.balance import BalanceService, get_balance_service
from db_factory import get_database

logger = logging.getLogger(__name__)


class TaskWorker:
    """Worker для обработки задач перемещения"""

    def __init__(
        self,
        worker_id: str = "worker-1",
        poll_interval: float = 1.0,
        notify_callback: Optional[Callable[[int, str], Awaitable[None]]] = None
    ):
        """
        Инициализация воркера.

        Args:
            worker_id: Уникальный ID воркера
            poll_interval: Интервал опроса очереди (секунды)
            notify_callback: Функция для отправки уведомлений (user_id, message)
        """
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.notify_callback = notify_callback

        self._running = False
        self._task_queue: Optional[TaskQueue] = None
        self._redistribution_service: Optional[WBRedistributionService] = None
        self._balance_service: Optional[BalanceService] = None

    async def start(self) -> None:
        """Запуск воркера"""
        logger.info(f"Starting worker {self.worker_id}")

        self._task_queue = await get_task_queue()
        self._redistribution_service = get_redistribution_service()
        self._balance_service = get_balance_service()

        if not self._task_queue.is_connected:
            logger.error("Redis not connected, worker cannot start")
            return

        self._running = True
        await self._run_loop()

    async def stop(self) -> None:
        """Остановка воркера"""
        logger.info(f"Stopping worker {self.worker_id}")
        self._running = False

    async def _run_loop(self) -> None:
        """Основной цикл обработки задач"""
        logger.info(f"Worker {self.worker_id} started processing loop")

        while self._running:
            try:
                # Получаем следующую задачу
                task = await self._task_queue.get_next_task()

                if task:
                    await self._process_task(task)
                else:
                    # Очередь пуста - ждём
                    await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                logger.info(f"Worker {self.worker_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Worker {self.worker_id} error: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

        logger.info(f"Worker {self.worker_id} stopped")

    async def _process_task(self, task: Task) -> None:
        """
        Обработка одной задачи.

        Args:
            task: Задача для обработки
        """
        logger.info(f"Processing task {task.id} (attempt {task.attempts}/{task.max_attempts})")

        db = get_database()

        try:
            # Получаем сессию браузера
            session = db.get_browser_session(task.session_id)
            if not session:
                await self._complete_task(task, False, "Сессия не найдена")
                return

            if session.get('status') != 'active':
                await self._complete_task(task, False, "Сессия истекла. Авторизуйтесь заново: /auth")
                return

            cookies_encrypted = session.get('cookies_encrypted')
            if not cookies_encrypted:
                await self._complete_task(task, False, "Cookies не найдены")
                return

            # Проверяем баланс перед выполнением
            balance_info = self._balance_service.get_balance(task.user_id)
            if not balance_info.can_redistribute:
                await self._complete_task(task, False, "Недостаточно средств. Пополните баланс: /pay")
                return

            # Списываем средства
            if not self._balance_service.charge_for_redistribution(task.user_id):
                await self._complete_task(task, False, "Не удалось списать средства")
                return

            # Выполняем перемещение
            result = await self._redistribution_service.execute_redistribution(
                cookies_encrypted=cookies_encrypted,
                nm_id=task.nm_id,
                source_warehouse_id=task.source_warehouse_id,
                target_warehouse_id=task.target_warehouse_id,
                quantity=task.quantity
            )

            # Обрабатываем результат
            if result.status == RedistributionStatus.SUCCESS:
                # Успех
                await self._complete_task(
                    task,
                    success=True,
                    message=f"Перемещение выполнено! ID: {result.supply_id or 'N/A'}"
                )

                # Обновляем статус в БД
                db.update_redistribution_request(
                    task.request_id,
                    status='completed',
                    supply_id=result.supply_id
                )

            elif result.status == RedistributionStatus.NO_QUOTA:
                # Нет квоты - возвращаем деньги и ставим в очередь повторно
                self._balance_service.refund_redistribution(task.user_id)
                await self._complete_task(
                    task,
                    success=False,
                    error_message=f"Нет квоты. Задача вернётся в очередь при появлении слотов."
                )

            elif result.status == RedistributionStatus.SESSION_EXPIRED:
                # Сессия истекла - деактивируем и возвращаем деньги
                db.deactivate_browser_session(task.session_id)
                self._balance_service.refund_redistribution(task.user_id)
                await self._complete_task(
                    task,
                    success=False,
                    error_message="Сессия истекла. Авторизуйтесь заново: /auth"
                )

            else:
                # Другая ошибка
                self._balance_service.refund_redistribution(task.user_id)
                await self._complete_task(
                    task,
                    success=False,
                    error_message=result.message
                )

        except Exception as e:
            logger.error(f"Error processing task {task.id}: {e}", exc_info=True)
            # Возвращаем деньги при ошибке
            try:
                self._balance_service.refund_redistribution(task.user_id)
            except Exception:
                pass
            await self._complete_task(task, False, f"Внутренняя ошибка: {str(e)}")

    async def _complete_task(
        self,
        task: Task,
        success: bool,
        message: str = None,
        error_message: str = None
    ) -> None:
        """
        Завершение задачи.

        Args:
            task: Задача
            success: Успешно ли выполнена
            message: Сообщение для пользователя (при успехе)
            error_message: Сообщение об ошибке
        """
        # Обновляем статус в очереди
        await self._task_queue.complete_task(
            task.id,
            success=success,
            error_message=error_message or message
        )

        # Отправляем уведомление пользователю
        if self.notify_callback:
            if success:
                notification = f"✅ {message or 'Задача выполнена'}"
            else:
                notification = f"❌ {error_message or message or 'Ошибка выполнения'}"

            try:
                await self.notify_callback(task.user_id, notification)
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")

        # Обновляем статус в БД
        db = get_database()
        status = 'completed' if success else 'failed'
        db.update_redistribution_request(task.request_id, status=status)

        logger.info(f"Task {task.id} completed: success={success}")


class WorkerPool:
    """Пул воркеров для параллельной обработки"""

    def __init__(
        self,
        num_workers: int = 3,
        notify_callback: Optional[Callable[[int, str], Awaitable[None]]] = None
    ):
        """
        Инициализация пула.

        Args:
            num_workers: Количество воркеров
            notify_callback: Функция для уведомлений
        """
        self.num_workers = num_workers
        self.notify_callback = notify_callback
        self._workers: list[TaskWorker] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Запуск всех воркеров"""
        logger.info(f"Starting worker pool with {self.num_workers} workers")

        for i in range(self.num_workers):
            worker = TaskWorker(
                worker_id=f"worker-{i+1}",
                notify_callback=self.notify_callback
            )
            self._workers.append(worker)

            task = asyncio.create_task(worker.start())
            self._tasks.append(task)

    async def stop(self) -> None:
        """Остановка всех воркеров"""
        logger.info("Stopping worker pool")

        for worker in self._workers:
            await worker.stop()

        # Ждём завершения
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._workers.clear()
        self._tasks.clear()

    async def get_stats(self) -> dict:
        """Получить статистику пула"""
        queue = await get_task_queue()
        queue_stats = await queue.get_queue_stats()

        return {
            'workers': self.num_workers,
            'active_workers': len([w for w in self._workers if w._running]),
            **queue_stats
        }


# Singleton instance
_worker_pool: Optional[WorkerPool] = None


async def get_worker_pool(
    num_workers: int = 3,
    notify_callback: Optional[Callable[[int, str], Awaitable[None]]] = None
) -> WorkerPool:
    """Получить singleton instance WorkerPool"""
    global _worker_pool

    if _worker_pool is None:
        _worker_pool = WorkerPool(
            num_workers=num_workers,
            notify_callback=notify_callback
        )

    return _worker_pool


async def shutdown_worker_pool() -> None:
    """Корректное завершение пула воркеров"""
    global _worker_pool

    if _worker_pool:
        await _worker_pool.stop()
        _worker_pool = None
