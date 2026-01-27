"""
Базовый HTTP клиент для WB API с rate limiting.

Реализует:
- Token bucket rate limiting (6 запросов/минуту для коэффициентов)
- Retry логику с exponential backoff
- Централизованную обработку ошибок
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

logger = logging.getLogger(__name__)


class WBApiError(Exception):
    """Базовое исключение для WB API ошибок"""
    def __init__(self, message: str, status_code: int = None, response: str = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class WBAuthError(WBApiError):
    """Ошибка авторизации (невалидный токен)"""
    pass


class WBRateLimitError(WBApiError):
    """Превышен rate limit WB API"""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after}s")


class WBNotFoundError(WBApiError):
    """Ресурс не найден"""
    pass


class Endpoint(Enum):
    """WB API endpoints с их rate limits"""
    WAREHOUSES = ("warehouses", 60)          # 60 req/min
    COEFFICIENTS = ("coefficients", 6)        # 6 req/min (критично!)
    STOCKS = ("stocks", 60)                   # 60 req/min
    SUPPLIES = ("supplies", 60)               # 60 req/min
    ACCEPTANCE = ("acceptance", 60)           # 60 req/min

    def __init__(self, name: str, rate_limit: int):
        self._name = name
        self.rate_limit = rate_limit  # запросов в минуту


@dataclass
class TokenBucket:
    """Token bucket для rate limiting"""
    capacity: int           # Максимум токенов
    tokens: float          # Текущее количество
    refill_rate: float     # Токенов в секунду
    last_refill: float     # Время последнего пополнения

    def try_consume(self, tokens: int = 1) -> bool:
        """Попытка забрать токены. Возвращает True если успешно."""
        now = time.monotonic()

        # Пополняем токены
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def wait_time(self, tokens: int = 1) -> float:
        """Время ожидания до доступности токенов"""
        if self.tokens >= tokens:
            return 0
        needed = tokens - self.tokens
        return needed / self.refill_rate


class WBApiClient:
    """
    Асинхронный HTTP клиент для WB API с rate limiting.

    Использование:
        async with WBApiClient(api_token) as client:
            warehouses = await client.get("/api/v1/warehouses", Endpoint.WAREHOUSES)
    """

    BASE_URL = "https://common-api.wildberries.ru"
    MARKETPLACE_URL = "https://marketplace-api.wildberries.ru"
    SUPPLIES_URL = "https://supplies-api.wildberries.ru"

    def __init__(self, api_token: str, timeout: int = 30):
        """
        Args:
            api_token: WB API токен
            timeout: Timeout для запросов в секундах
        """
        self.api_token = api_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

        # Rate limiters для разных endpoints
        self._rate_limiters: Dict[Endpoint, TokenBucket] = {}
        for endpoint in Endpoint:
            # Создаём bucket с запасом (80% от лимита для надёжности)
            safe_limit = max(1, int(endpoint.rate_limit * 0.8))
            self._rate_limiters[endpoint] = TokenBucket(
                capacity=safe_limit,
                tokens=safe_limit,
                refill_rate=safe_limit / 60.0,  # токенов в секунду
                last_refill=time.monotonic()
            )

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _ensure_session(self):
        """Создаёт HTTP сессию если её нет"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=self._get_headers()
            )

    async def close(self):
        """Закрывает HTTP сессию"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _get_headers(self) -> Dict[str, str]:
        """Заголовки для всех запросов"""
        return {
            "Authorization": self.api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _wait_for_rate_limit(self, endpoint: Endpoint):
        """Ожидает доступности rate limit"""
        bucket = self._rate_limiters[endpoint]

        while not bucket.try_consume():
            wait_time = bucket.wait_time()
            logger.debug(f"Rate limit for {endpoint.name}, waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True
    )
    async def _request(
        self,
        method: str,
        url: str,
        endpoint: Endpoint,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Выполняет HTTP запрос с rate limiting и retry.

        Args:
            method: HTTP метод (GET, POST, etc.)
            url: Полный URL
            endpoint: Тип endpoint для rate limiting
            **kwargs: Дополнительные параметры для aiohttp

        Returns:
            JSON ответ как dict

        Raises:
            WBAuthError: Невалидный токен
            WBRateLimitError: Превышен rate limit
            WBNotFoundError: Ресурс не найден
            WBApiError: Другие ошибки API
        """
        await self._ensure_session()
        await self._wait_for_rate_limit(endpoint)

        logger.debug(f"WB API {method} {url}")

        async with self._session.request(method, url, **kwargs) as response:
            response_text = await response.text()

            if response.status == 200:
                try:
                    return await response.json()
                except Exception:
                    # Иногда WB возвращает пустой ответ при успехе
                    return {"success": True, "raw": response_text}

            elif response.status == 401:
                raise WBAuthError(
                    "Invalid API token",
                    status_code=401,
                    response=response_text
                )

            elif response.status == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                raise WBRateLimitError(retry_after=retry_after)

            elif response.status == 404:
                raise WBNotFoundError(
                    f"Resource not found: {url}",
                    status_code=404,
                    response=response_text
                )

            else:
                raise WBApiError(
                    f"WB API error: {response.status}",
                    status_code=response.status,
                    response=response_text
                )

    async def get(
        self,
        path: str,
        endpoint: Endpoint,
        base_url: str = None,
        params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        GET запрос к WB API.

        Args:
            path: Путь API (например, /api/v1/warehouses)
            endpoint: Тип endpoint для rate limiting
            base_url: Базовый URL (по умолчанию common-api)
            params: Query параметры

        Returns:
            JSON ответ
        """
        base = base_url or self.BASE_URL
        url = f"{base}{path}"
        return await self._request("GET", url, endpoint, params=params)

    async def post(
        self,
        path: str,
        endpoint: Endpoint,
        base_url: str = None,
        json: Dict[str, Any] = None,
        params: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        POST запрос к WB API.

        Args:
            path: Путь API
            endpoint: Тип endpoint для rate limiting
            base_url: Базовый URL
            json: JSON body
            params: Query параметры

        Returns:
            JSON ответ
        """
        base = base_url or self.BASE_URL
        url = f"{base}{path}"
        return await self._request("POST", url, endpoint, json=json, params=params)

    async def put(
        self,
        path: str,
        endpoint: Endpoint,
        base_url: str = None,
        json: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """PUT запрос к WB API."""
        base = base_url or self.BASE_URL
        url = f"{base}{path}"
        return await self._request("PUT", url, endpoint, json=json)

    async def delete(
        self,
        path: str,
        endpoint: Endpoint,
        base_url: str = None
    ) -> Dict[str, Any]:
        """DELETE запрос к WB API."""
        base = base_url or self.BASE_URL
        url = f"{base}{path}"
        return await self._request("DELETE", url, endpoint)

    async def check_token(self) -> bool:
        """
        Проверяет валидность токена.

        Returns:
            True если токен валиден
        """
        try:
            # Используем запрос списка складов для проверки токена
            await self.get(
                "/api/v1/warehouses",
                Endpoint.WAREHOUSES
            )
            return True
        except WBAuthError:
            return False
        except WBRateLimitError:
            # Rate limit - токен валиден, просто превышен лимит
            return True
        except Exception as e:
            logger.warning(f"Token check failed: {e}")
            # В случае других ошибок (сеть и т.д.) считаем токен валидным
            # чтобы не блокировать пользователя
            return True

    async def get_supplier_info(self) -> Optional[Dict[str, Any]]:
        """
        Получает информацию о поставщике (владельце токена).

        Returns:
            Dict с информацией: {"name": "Название магазина", "inn": "ИНН", ...}
            None если не удалось получить
        """
        try:
            # Пробуем разные endpoints для получения информации
            # 1. Пробуем получить информацию через список офисов
            try:
                response = await self.get("/api/v3/offices", Endpoint.WAREHOUSES)
                if response and len(response) > 0:
                    office = response[0]
                    return {
                        "name": office.get("name", "Неизвестный поставщик"),
                        "id": office.get("id"),
                        "address": office.get("address")
                    }
            except Exception as e:
                logger.debug(f"Failed to get offices: {e}")

            # 2. Пробуем получить через склады (обычно содержат название продавца)
            try:
                warehouses = await self.get("/api/v1/warehouses", Endpoint.WAREHOUSES)
                if warehouses and len(warehouses) > 0:
                    # Берём первый склад и извлекаем название
                    first_wh = warehouses[0]
                    # Обычно название склада содержит название магазина
                    wh_name = first_wh.get("name", "")
                    if wh_name:
                        # Пробуем извлечь название магазина из названия склада
                        # Например: "Магазин ООО - Склад Коледино" -> "Магазин ООО"
                        parts = wh_name.split("-")
                        supplier_name = parts[0].strip() if parts else wh_name
                        return {
                            "name": supplier_name[:50],  # Ограничиваем длину
                            "warehouse_count": len(warehouses)
                        }
            except Exception as e:
                logger.debug(f"Failed to get warehouses: {e}")

            # 3. Если ничего не получилось - возвращаем дефолтное имя
            return {"name": "Мой магазин"}

        except Exception as e:
            logger.error(f"Failed to get supplier info: {e}")
            return None
