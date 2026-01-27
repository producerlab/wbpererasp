"""
API для получения коэффициентов приёмки Wildberries.

Критичный endpoint с rate limit 6 запросов/минуту.
Используется для мониторинга выгодных слотов.
"""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import IntEnum

from .client import WBApiClient, Endpoint

logger = logging.getLogger(__name__)


class BoxType(IntEnum):
    """Типы коробов для поставки"""
    SMALL_BOX = 2      # Короба
    SUPER_SAFE = 5     # Суперсейф
    PALLET = 6         # Монопаллеты


@dataclass
class Coefficient:
    """Коэффициент приёмки для склада"""
    warehouse_id: int
    warehouse_name: str
    date: date
    coefficient: float              # -1 = недоступно, 0 = бесплатно, >0 = множитель
    box_type_id: Optional[int] = None
    box_type_name: Optional[str] = None
    storage_coefficient: Optional[float] = None  # Коэффициент хранения

    @property
    def is_available(self) -> bool:
        """Доступна ли приёмка"""
        return self.coefficient >= 0

    @property
    def is_free(self) -> bool:
        """Бесплатная приёмка"""
        return self.coefficient == 0

    @property
    def is_profitable(self) -> bool:
        """Выгодный коэффициент (0, 0.5, 1)"""
        return 0 <= self.coefficient <= 1

    @classmethod
    def from_api_response(
        cls,
        data: Dict[str, Any],
        warehouse_name: str = ""
    ) -> 'Coefficient':
        """Создаёт Coefficient из ответа API"""
        # WB возвращает дату в формате "2026-01-22"
        date_str = data.get('date', '')
        try:
            coeff_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            coeff_date = date.today()

        return cls(
            warehouse_id=data.get('warehouseID') or data.get('warehouseId', 0),
            warehouse_name=data.get('warehouseName', warehouse_name),
            date=coeff_date,
            coefficient=data.get('coefficient', -1),
            box_type_id=data.get('boxTypeID') or data.get('boxTypeId'),
            box_type_name=data.get('boxTypeName'),
            storage_coefficient=data.get('storageCoefficient'),
        )


@dataclass
class CoefficientChange:
    """Изменение коэффициента"""
    warehouse_id: int
    warehouse_name: str
    old_coefficient: float
    new_coefficient: float
    date: date
    box_type_id: Optional[int] = None
    detected_at: datetime = field(default_factory=datetime.now)
    priority: int = 0  # Выше = важнее

    @property
    def is_improvement(self) -> bool:
        """Улучшение (снижение) коэффициента"""
        return self.new_coefficient < self.old_coefficient

    @property
    def became_free(self) -> bool:
        """Стал бесплатным"""
        return self.new_coefficient == 0 and self.old_coefficient != 0

    @property
    def became_available(self) -> bool:
        """Стал доступным (был -1)"""
        return self.old_coefficient < 0 and self.new_coefficient >= 0


class CoefficientsAPI:
    """
    API для работы с коэффициентами приёмки.

    ВАЖНО: Rate limit 6 запросов/минуту!
    Используйте один экземпляр для всей системы.

    Использование:
        async with WBApiClient(token) as client:
            api = CoefficientsAPI(client)
            coefficients = await api.get_acceptance_coefficients()
    """

    # Кэш коэффициентов для diff detection
    _previous_state: Dict[str, Coefficient] = {}

    def __init__(self, client: WBApiClient):
        self.client = client

    async def get_acceptance_coefficients(
        self,
        warehouse_ids: Optional[List[int]] = None
    ) -> List[Coefficient]:
        """
        Получает коэффициенты приёмки для складов.

        ВНИМАНИЕ: Rate limit 6 запросов/минуту!

        Args:
            warehouse_ids: Список ID складов (если None - все склады)

        Returns:
            Список коэффициентов
        """
        try:
            params = {}
            if warehouse_ids:
                params['warehouseIDs'] = ','.join(map(str, warehouse_ids))

            response = await self.client.get(
                "/api/v1/acceptance/coefficients",
                Endpoint.COEFFICIENTS,
                base_url=self.client.SUPPLIES_URL,
                params=params if params else None
            )

            coefficients = []

            # WB может возвращать разные структуры
            data = response if isinstance(response, list) else response.get('data', [])

            for item in data:
                try:
                    coeff = Coefficient.from_api_response(item)
                    coefficients.append(coeff)
                except Exception as e:
                    logger.warning(f"Failed to parse coefficient: {e}")
                    continue

            logger.debug(f"Loaded {len(coefficients)} coefficients")
            return coefficients

        except Exception as e:
            logger.error(f"Failed to get coefficients: {e}")
            raise

    async def get_coefficients_by_date(
        self,
        target_date: date,
        warehouse_ids: Optional[List[int]] = None
    ) -> List[Coefficient]:
        """
        Получает коэффициенты для конкретной даты.

        Args:
            target_date: Целевая дата
            warehouse_ids: Список ID складов

        Returns:
            Отфильтрованный список коэффициентов
        """
        all_coefficients = await self.get_acceptance_coefficients(warehouse_ids)
        return [c for c in all_coefficients if c.date == target_date]

    async def get_profitable_slots(
        self,
        max_coefficient: float = 1.0,
        warehouse_ids: Optional[List[int]] = None
    ) -> List[Coefficient]:
        """
        Получает выгодные слоты (с низким коэффициентом).

        Args:
            max_coefficient: Максимальный коэффициент (по умолчанию 1.0)
            warehouse_ids: Список ID складов

        Returns:
            Список выгодных коэффициентов, отсортированный по выгодности
        """
        all_coefficients = await self.get_acceptance_coefficients(warehouse_ids)

        profitable = [
            c for c in all_coefficients
            if c.is_available and c.coefficient <= max_coefficient
        ]

        # Сортируем: сначала бесплатные, потом по коэффициенту
        return sorted(profitable, key=lambda c: (c.coefficient, c.date))

    def detect_changes(
        self,
        current: List[Coefficient],
        previous: Optional[Dict[str, Coefficient]] = None
    ) -> List[CoefficientChange]:
        """
        Детектирует изменения коэффициентов.

        Args:
            current: Текущие коэффициенты
            previous: Предыдущее состояние (если None - используется внутренний кэш)

        Returns:
            Список изменений с приоритетами
        """
        if previous is None:
            previous = self._previous_state

        changes = []

        for coeff in current:
            # Ключ = warehouse_id + date + box_type
            key = f"{coeff.warehouse_id}_{coeff.date}_{coeff.box_type_id}"

            if key in previous:
                old = previous[key]
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
            self._previous_state[key] = coeff

        # Сортируем по приоритету (выше = важнее)
        return sorted(changes, key=lambda c: c.priority, reverse=True)

    def _calculate_priority(self, old: float, new: float) -> int:
        """
        Рассчитывает приоритет изменения.

        Приоритеты:
        - 100: Стало бесплатно (0)
        - 90: Стало 0.5
        - 80: Стало 1.0
        - 70: Стало доступно (было -1)
        - 50: Снижение коэффициента
        - 10: Повышение коэффициента
        """
        if new == 0:
            return 100  # Бесплатно - максимальный приоритет!
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

    def clear_cache(self):
        """Очищает кэш предыдущих коэффициентов"""
        self._previous_state.clear()

    def get_cache_state(self) -> Dict[str, Coefficient]:
        """Возвращает текущее состояние кэша"""
        return self._previous_state.copy()

    def set_cache_state(self, state: Dict[str, Coefficient]):
        """Устанавливает состояние кэша (для восстановления из Redis)"""
        self._previous_state = state
