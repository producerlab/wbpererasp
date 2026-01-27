"""
Redis очередь задач для перемещения остатков.

Функционал:
- Добавление задач в очередь
- Получение задач для обработки
- Обновление статуса задач
- Приоритеты (VIP клиенты)
- Retry логика
"""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, List

import redis.asyncio as redis

from config import Config

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """Статусы задачи"""
    PENDING = "pending"           # Ожидает обработки
    PROCESSING = "processing"     # В обработке
    COMPLETED = "completed"       # Выполнена
    FAILED = "failed"             # Ошибка
    CANCELLED = "cancelled"       # Отменена


@dataclass
class Task:
    """Задача на перемещение"""
    id: str                        # Уникальный ID задачи
    user_id: int                   # Telegram user ID
    session_id: int                # ID browser session
    request_id: int                # ID redistribution_request в БД
    nm_id: int                     # Артикул товара
    source_warehouse_id: int       # Склад-источник
    target_warehouse_id: int       # Склад-назначение
    quantity: int                  # Количество
    priority: int = 0              # Приоритет (выше = важнее)
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0              # Количество попыток
    max_attempts: int = 3          # Максимум попыток
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Сериализация в dict"""
        data = asdict(self)
        data['status'] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'Task':
        """Десериализация из dict"""
        data['status'] = TaskStatus(data['status'])
        return cls(**data)

    def to_json(self) -> str:
        """Сериализация в JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> 'Task':
        """Десериализация из JSON"""
        return cls.from_dict(json.loads(json_str))


class TaskQueue:
    """Redis очередь задач"""

    # Ключи Redis
    QUEUE_KEY = "wb:redistribution:queue"        # Основная очередь (sorted set по приоритету)
    PROCESSING_KEY = "wb:redistribution:processing"  # Задачи в обработке
    TASKS_KEY = "wb:redistribution:tasks"        # Данные задач (hash)
    RESULTS_KEY = "wb:redistribution:results"    # Результаты (для уведомлений)

    def __init__(self, redis_url: str = None):
        """
        Инициализация очереди.

        Args:
            redis_url: URL Redis (redis://localhost:6379/0)
        """
        self.redis_url = redis_url or Config.REDIS_URL
        self._redis: Optional[redis.Redis] = None

    async def connect(self) -> None:
        """Подключение к Redis"""
        if not self.redis_url:
            logger.warning("Redis URL not configured, queue disabled")
            return

        try:
            self._redis = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            await self._redis.ping()
            logger.info("Connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._redis = None

    async def disconnect(self) -> None:
        """Отключение от Redis"""
        if self._redis:
            await self._redis.close()
            self._redis = None
            logger.info("Disconnected from Redis")

    @property
    def is_connected(self) -> bool:
        """Проверка подключения"""
        return self._redis is not None

    async def add_task(self, task: Task) -> bool:
        """
        Добавить задачу в очередь.

        Args:
            task: Задача

        Returns:
            True если успешно
        """
        if not self.is_connected:
            logger.warning("Redis not connected, cannot add task")
            return False

        try:
            task.created_at = datetime.now().isoformat()
            task.status = TaskStatus.PENDING

            # Сохраняем данные задачи
            await self._redis.hset(self.TASKS_KEY, task.id, task.to_json())

            # Добавляем в очередь с приоритетом (score = -priority для обратной сортировки)
            await self._redis.zadd(
                self.QUEUE_KEY,
                {task.id: -task.priority}
            )

            logger.info(f"Task {task.id} added to queue (priority: {task.priority})")
            return True

        except Exception as e:
            logger.error(f"Failed to add task: {e}")
            return False

    async def get_next_task(self) -> Optional[Task]:
        """
        Получить следующую задачу из очереди.

        Returns:
            Task или None если очередь пуста
        """
        if not self.is_connected:
            return None

        try:
            # Атомарно берём задачу с наивысшим приоритетом
            result = await self._redis.zpopmin(self.QUEUE_KEY, 1)
            if not result:
                return None

            task_id, _ = result[0]

            # Получаем данные задачи
            task_json = await self._redis.hget(self.TASKS_KEY, task_id)
            if not task_json:
                logger.warning(f"Task {task_id} not found in storage")
                return None

            task = Task.from_json(task_json)
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now().isoformat()
            task.attempts += 1

            # Перемещаем в processing
            await self._redis.sadd(self.PROCESSING_KEY, task_id)
            await self._redis.hset(self.TASKS_KEY, task_id, task.to_json())

            logger.info(f"Task {task_id} taken for processing (attempt {task.attempts})")
            return task

        except Exception as e:
            logger.error(f"Failed to get next task: {e}")
            return None

    async def complete_task(
        self,
        task_id: str,
        success: bool,
        error_message: str = None
    ) -> bool:
        """
        Завершить обработку задачи.

        Args:
            task_id: ID задачи
            success: Успешно ли выполнена
            error_message: Сообщение об ошибке (если не успешно)

        Returns:
            True если успешно обновлено
        """
        if not self.is_connected:
            return False

        try:
            # Получаем задачу
            task_json = await self._redis.hget(self.TASKS_KEY, task_id)
            if not task_json:
                logger.warning(f"Task {task_id} not found")
                return False

            task = Task.from_json(task_json)

            if success:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now().isoformat()
            else:
                task.error_message = error_message
                if task.attempts >= task.max_attempts:
                    task.status = TaskStatus.FAILED
                    task.completed_at = datetime.now().isoformat()
                else:
                    # Возвращаем в очередь для retry
                    task.status = TaskStatus.PENDING
                    await self._redis.zadd(
                        self.QUEUE_KEY,
                        {task_id: -(task.priority - task.attempts)}  # Снижаем приоритет при retry
                    )

            # Обновляем данные
            await self._redis.hset(self.TASKS_KEY, task_id, task.to_json())

            # Убираем из processing
            await self._redis.srem(self.PROCESSING_KEY, task_id)

            # Публикуем результат для уведомления
            await self._redis.publish(
                self.RESULTS_KEY,
                json.dumps({
                    'task_id': task_id,
                    'user_id': task.user_id,
                    'status': task.status.value,
                    'error': error_message
                })
            )

            logger.info(f"Task {task_id} completed with status: {task.status.value}")
            return True

        except Exception as e:
            logger.error(f"Failed to complete task: {e}")
            return False

    async def cancel_task(self, task_id: str) -> bool:
        """
        Отменить задачу.

        Args:
            task_id: ID задачи

        Returns:
            True если успешно отменена
        """
        if not self.is_connected:
            return False

        try:
            # Удаляем из очереди
            await self._redis.zrem(self.QUEUE_KEY, task_id)
            await self._redis.srem(self.PROCESSING_KEY, task_id)

            # Обновляем статус
            task_json = await self._redis.hget(self.TASKS_KEY, task_id)
            if task_json:
                task = Task.from_json(task_json)
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now().isoformat()
                await self._redis.hset(self.TASKS_KEY, task_id, task.to_json())

            logger.info(f"Task {task_id} cancelled")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel task: {e}")
            return False

    async def get_task(self, task_id: str) -> Optional[Task]:
        """
        Получить задачу по ID.

        Args:
            task_id: ID задачи

        Returns:
            Task или None
        """
        if not self.is_connected:
            return None

        try:
            task_json = await self._redis.hget(self.TASKS_KEY, task_id)
            if task_json:
                return Task.from_json(task_json)
            return None
        except Exception as e:
            logger.error(f"Failed to get task: {e}")
            return None

    async def get_user_tasks(self, user_id: int) -> List[Task]:
        """
        Получить все задачи пользователя.

        Args:
            user_id: Telegram user ID

        Returns:
            Список задач
        """
        if not self.is_connected:
            return []

        try:
            # Получаем все задачи
            all_tasks = await self._redis.hgetall(self.TASKS_KEY)
            user_tasks = []

            for task_json in all_tasks.values():
                task = Task.from_json(task_json)
                if task.user_id == user_id:
                    user_tasks.append(task)

            return sorted(user_tasks, key=lambda t: t.created_at or "", reverse=True)

        except Exception as e:
            logger.error(f"Failed to get user tasks: {e}")
            return []

    async def get_queue_stats(self) -> dict:
        """
        Получить статистику очереди.

        Returns:
            Статистика {pending, processing, total}
        """
        if not self.is_connected:
            return {'pending': 0, 'processing': 0, 'total': 0}

        try:
            pending = await self._redis.zcard(self.QUEUE_KEY)
            processing = await self._redis.scard(self.PROCESSING_KEY)
            total = await self._redis.hlen(self.TASKS_KEY)

            return {
                'pending': pending,
                'processing': processing,
                'total': total
            }
        except Exception as e:
            logger.error(f"Failed to get queue stats: {e}")
            return {'pending': 0, 'processing': 0, 'total': 0}

    async def cleanup_stale_tasks(self, timeout_seconds: int = 300) -> int:
        """
        Очистка зависших задач (в processing слишком долго).

        Args:
            timeout_seconds: Таймаут в секундах

        Returns:
            Количество очищенных задач
        """
        if not self.is_connected:
            return 0

        try:
            processing_ids = await self._redis.smembers(self.PROCESSING_KEY)
            cleaned = 0
            now = datetime.now()

            for task_id in processing_ids:
                task_json = await self._redis.hget(self.TASKS_KEY, task_id)
                if not task_json:
                    await self._redis.srem(self.PROCESSING_KEY, task_id)
                    cleaned += 1
                    continue

                task = Task.from_json(task_json)
                if task.started_at:
                    started = datetime.fromisoformat(task.started_at)
                    if (now - started).total_seconds() > timeout_seconds:
                        # Возвращаем в очередь как failed retry
                        await self.complete_task(
                            task_id,
                            success=False,
                            error_message="Task timed out"
                        )
                        cleaned += 1

            if cleaned:
                logger.info(f"Cleaned {cleaned} stale tasks")

            return cleaned

        except Exception as e:
            logger.error(f"Failed to cleanup stale tasks: {e}")
            return 0


# Singleton instance
_task_queue: Optional[TaskQueue] = None


async def get_task_queue() -> TaskQueue:
    """Получить singleton instance TaskQueue"""
    global _task_queue

    if _task_queue is None:
        _task_queue = TaskQueue()
        await _task_queue.connect()

    return _task_queue


async def shutdown_task_queue() -> None:
    """Корректное завершение TaskQueue"""
    global _task_queue

    if _task_queue:
        await _task_queue.disconnect()
        _task_queue = None
