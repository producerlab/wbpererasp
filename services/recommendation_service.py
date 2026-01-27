"""
Сервис рекомендаций "куда везти товар".

Анализирует коэффициенты по всем складам и выдаёт
рекомендации по наиболее выгодным направлениям.
"""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import date

from database import Database
from wb_api.client import WBApiClient
from wb_api.coefficients import CoefficientsAPI, Coefficient
from wb_api.warehouses import WarehousesAPI

logger = logging.getLogger(__name__)


@dataclass
class Recommendation:
    """Рекомендация по складу"""
    warehouse_id: int
    warehouse_name: str
    region: str
    date: date
    coefficient: float
    score: float  # Чем выше - тем лучше

    @property
    def is_free(self) -> bool:
        return self.coefficient == 0

    @property
    def is_profitable(self) -> bool:
        return 0 <= self.coefficient <= 1


class RecommendationService:
    """
    Сервис рекомендаций куда везти товар.

    Анализирует текущие коэффициенты и возвращает
    топ-N рекомендаций по выгодности.

    Использование:
        service = RecommendationService(db)
        recommendations = await service.get_recommendations(
            api_token="...",
            limit=10
        )
    """

    # Регионы для группировки
    REGIONS = [
        "Центральный",
        "Северо-Западный",
        "Приволжский",
        "Уральский",
        "Сибирский",
        "Южный"
    ]

    def __init__(self, db: Database):
        self.db = db

    async def get_recommendations(
        self,
        api_token: str,
        limit: int = 10,
        max_coefficient: float = 2.0,
        region: str = None
    ) -> List[Recommendation]:
        """
        Получает рекомендации куда везти товар.

        Args:
            api_token: WB API токен
            limit: Максимальное количество рекомендаций
            max_coefficient: Максимальный коэффициент для включения
            region: Фильтр по региону (опционально)

        Returns:
            Список рекомендаций, отсортированный по выгодности
        """
        async with WBApiClient(api_token) as client:
            # Получаем коэффициенты
            coeff_api = CoefficientsAPI(client)
            coefficients = await coeff_api.get_acceptance_coefficients()

            if not coefficients:
                logger.warning("No coefficients received")
                return []

            # Получаем информацию о складах
            warehouse_info = self._get_warehouse_info()

            # Преобразуем в рекомендации
            recommendations = []
            for coeff in coefficients:
                # Пропускаем недоступные
                if coeff.coefficient < 0:
                    continue

                # Фильтр по максимальному коэффициенту
                if coeff.coefficient > max_coefficient:
                    continue

                # Получаем регион склада
                wh_info = warehouse_info.get(coeff.warehouse_id, {})
                wh_region = wh_info.get("region", "Другой")

                # Фильтр по региону
                if region and wh_region != region:
                    continue

                # Рассчитываем score (выше = лучше)
                score = self._calculate_score(coeff)

                rec = Recommendation(
                    warehouse_id=coeff.warehouse_id,
                    warehouse_name=coeff.warehouse_name or wh_info.get("name", str(coeff.warehouse_id)),
                    region=wh_region,
                    date=coeff.date,
                    coefficient=coeff.coefficient,
                    score=score
                )
                recommendations.append(rec)

            # Сортируем по score (убывание)
            recommendations.sort(key=lambda r: r.score, reverse=True)

            # Ограничиваем количество
            return recommendations[:limit]

    async def get_recommendations_by_region(
        self,
        api_token: str,
        limit_per_region: int = 3
    ) -> Dict[str, List[Recommendation]]:
        """
        Получает рекомендации сгруппированные по регионам.

        Args:
            api_token: WB API токен
            limit_per_region: Максимум рекомендаций на регион

        Returns:
            Словарь {регион: [рекомендации]}
        """
        result = {}

        for region in self.REGIONS:
            recs = await self.get_recommendations(
                api_token=api_token,
                limit=limit_per_region,
                region=region
            )
            if recs:
                result[region] = recs

        return result

    async def get_best_slot(
        self,
        api_token: str,
        warehouse_ids: List[int] = None
    ) -> Optional[Recommendation]:
        """
        Находит лучший слот для бронирования.

        Args:
            api_token: WB API токен
            warehouse_ids: Ограничить поиск этими складами

        Returns:
            Лучшая рекомендация или None
        """
        async with WBApiClient(api_token) as client:
            coeff_api = CoefficientsAPI(client)
            coefficients = await coeff_api.get_acceptance_coefficients(warehouse_ids)

            if not coefficients:
                return None

            # Фильтруем доступные
            available = [c for c in coefficients if c.coefficient >= 0]
            if not available:
                return None

            # Находим лучший (минимальный коэффициент, ближайшая дата)
            best = min(available, key=lambda c: (c.coefficient, c.date))

            warehouse_info = self._get_warehouse_info()
            wh_info = warehouse_info.get(best.warehouse_id, {})

            return Recommendation(
                warehouse_id=best.warehouse_id,
                warehouse_name=best.warehouse_name or wh_info.get("name", str(best.warehouse_id)),
                region=wh_info.get("region", "Другой"),
                date=best.date,
                coefficient=best.coefficient,
                score=self._calculate_score(best)
            )

    def _calculate_score(self, coeff: Coefficient) -> float:
        """
        Рассчитывает score для ранжирования.

        Формула:
        - Бесплатный (0): 100 баллов
        - 0.5: 90 баллов
        - 1.0: 80 баллов
        - Остальные: 70 - coefficient * 10

        Дополнительно:
        - Ближайшая дата получает бонус
        """
        if coeff.coefficient == 0:
            base_score = 100
        elif coeff.coefficient == 0.5:
            base_score = 90
        elif coeff.coefficient == 1.0:
            base_score = 80
        else:
            base_score = max(0, 70 - coeff.coefficient * 10)

        # Бонус за ближайшую дату (до 5 баллов)
        days_ahead = (coeff.date - date.today()).days
        date_bonus = max(0, 5 - days_ahead * 0.5)

        return base_score + date_bonus

    def _get_warehouse_info(self) -> Dict[int, Dict]:
        """Получает информацию о складах из справочника"""
        return WarehousesAPI.POPULAR_WAREHOUSES

    def format_recommendations(
        self,
        recommendations: List[Recommendation],
        show_region: bool = True
    ) -> str:
        """
        Форматирует рекомендации для отображения.

        Args:
            recommendations: Список рекомендаций
            show_region: Показывать регион

        Returns:
            Отформатированная строка
        """
        if not recommendations:
            return "Нет доступных рекомендаций"

        lines = []
        for i, rec in enumerate(recommendations, 1):
            # Эмодзи по коэффициенту
            if rec.coefficient == 0:
                emoji = "🆓"
            elif rec.coefficient <= 1:
                emoji = "✅"
            else:
                emoji = "💰"

            line = f"{i}. {emoji} <b>{rec.warehouse_name}</b>"
            if show_region:
                line += f" ({rec.region})"
            line += f"\n   📅 {rec.date.strftime('%d.%m')} | 💰 {rec.coefficient}"

            lines.append(line)

        return "\n\n".join(lines)

    def format_by_region(
        self,
        by_region: Dict[str, List[Recommendation]]
    ) -> str:
        """
        Форматирует рекомендации по регионам.

        Args:
            by_region: Словарь {регион: рекомендации}

        Returns:
            Отформатированная строка
        """
        if not by_region:
            return "Нет доступных рекомендаций"

        lines = []
        for region, recs in by_region.items():
            lines.append(f"\n📍 <b>{region}</b>")
            for rec in recs:
                if rec.coefficient == 0:
                    emoji = "🆓"
                elif rec.coefficient <= 1:
                    emoji = "✅"
                else:
                    emoji = "💰"

                lines.append(
                    f"  {emoji} {rec.warehouse_name}: "
                    f"{rec.date.strftime('%d.%m')} ({rec.coefficient})"
                )

        return "\n".join(lines)
