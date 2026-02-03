"""
Клиент для внутреннего API WB (seller.wildberries.ru).

Использует cookies браузерной сессии вместо API токена.
Позволяет получать данные с тех же страниц, что видит пользователь в ЛК.
"""

import asyncio
import json
import logging
from typing import Optional, Dict, Any, List

import aiohttp

from utils.encryption import decrypt_token

logger = logging.getLogger(__name__)


class WBInternalClient:
    """
    Клиент для внутреннего API WB с использованием cookies.

    Внутренний API WB использует cookies сессии для авторизации,
    в отличие от публичного API, который требует токен.
    """

    BASE_URL = "https://seller.wildberries.ru"

    # Известные внутренние API endpoints
    ENDPOINTS = {
        # Остатки на складах
        'warehouse_remains': '/ns/analytics-back/api/v1/warehouse-remains',
        'stocks': '/ns/stocks-api/api/v1/stocks',

        # Информация о поставщике
        'supplier_info': '/ns/supplier-data/api/v1/supplier/info',

        # Товары
        'products': '/ns/nomenclature-api/api/v1/nomenclatures/list',
        'product_cards': '/ns/products-api/api/v1/cards',

        # Поиск товара по артикулу
        'search_nm': '/ns/nomenclature-api/api/v1/nomenclatures/search',
    }

    def __init__(self, cookies_encrypted: str):
        """
        Args:
            cookies_encrypted: Зашифрованные cookies из browser_sessions
        """
        self._cookies_encrypted = cookies_encrypted
        self._cookies: Optional[Dict[str, str]] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _ensure_session(self):
        """Создает HTTP сессию с cookies"""
        if self._session is not None:
            return

        # Расшифровываем cookies
        if self._cookies is None:
            try:
                cookies_json = decrypt_token(self._cookies_encrypted)
                cookies_list = json.loads(cookies_json)

                # Преобразуем список cookies в словарь
                self._cookies = {}
                for cookie in cookies_list:
                    if isinstance(cookie, dict):
                        name = cookie.get('name')
                        value = cookie.get('value')
                        if name and value:
                            self._cookies[name] = value

                logger.info(f"Загружено {len(self._cookies)} cookies")
            except Exception as e:
                logger.error(f"Ошибка расшифровки cookies: {e}")
                self._cookies = {}

        # Создаем сессию
        self._session = aiohttp.ClientSession(
            cookies=self._cookies,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                'Origin': self.BASE_URL,
                'Referer': f'{self.BASE_URL}/',
            }
        )

    async def close(self):
        """Закрывает HTTP сессию"""
        if self._session:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Выполняет HTTP запрос к внутреннему API.

        Args:
            method: HTTP метод (GET, POST, etc.)
            endpoint: Путь API (например, '/ns/stocks-api/...')
            params: Query параметры
            json_data: JSON тело запроса

        Returns:
            Ответ API в виде словаря
        """
        await self._ensure_session()

        url = f"{self.BASE_URL}{endpoint}"
        logger.debug(f"WB Internal API {method} {url}")

        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_data,
                **kwargs
            ) as response:
                response_text = await response.text()

                logger.debug(f"Response status: {response.status}")
                logger.debug(f"Response: {response_text[:500]}...")

                if response.status == 200:
                    try:
                        return await response.json()
                    except Exception:
                        return {"success": True, "raw": response_text}

                elif response.status == 401:
                    logger.error("Session expired - cookies invalid")
                    raise Exception("Browser session expired. Please re-authenticate.")

                elif response.status == 403:
                    logger.error(f"Access denied: {response_text[:200]}")
                    raise Exception("Access denied to this resource")

                else:
                    logger.error(f"API error {response.status}: {response_text[:200]}")
                    raise Exception(f"WB Internal API error: {response.status}")

        except aiohttp.ClientError as e:
            logger.error(f"HTTP error: {e}")
            raise Exception(f"Network error: {e}")

    async def get_warehouse_remains(self, supplier_id: Optional[int] = None) -> List[Dict]:
        """
        Получает остатки товаров на складах.

        Эквивалент страницы: https://seller.wildberries.ru/analytics-reports/warehouse-remains

        Returns:
            Список товаров с остатками по складам
        """
        params = {}
        if supplier_id:
            params['supplierId'] = supplier_id

        # Пробуем разные endpoints
        endpoints_to_try = [
            '/ns/analytics-back/api/v1/warehouse-remains',
            '/ns/stocks-api/api/v1/stocks',
            '/ns/balances/api/v1/balances',
        ]

        for endpoint in endpoints_to_try:
            try:
                result = await self._request('GET', endpoint, params=params)
                if result:
                    logger.info(f"Успешный запрос к {endpoint}")
                    return result if isinstance(result, list) else result.get('data', [])
            except Exception as e:
                logger.debug(f"Endpoint {endpoint} failed: {e}")
                continue

        return []

    async def search_product(self, nm_id: int) -> Optional[Dict]:
        """
        Поиск товара по артикулу WB (nmId).

        Args:
            nm_id: Артикул WB

        Returns:
            Информация о товаре или None
        """
        # Пробуем разные способы поиска
        endpoints_to_try = [
            ('/ns/nomenclature-api/api/v1/nomenclatures/search', {'nmId': nm_id}),
            ('/ns/products-api/api/v1/cards/search', {'nmId': nm_id}),
            (f'/ns/nomenclature-api/api/v1/nomenclatures/{nm_id}', None),
        ]

        for endpoint, params in endpoints_to_try:
            try:
                result = await self._request('GET', endpoint, params=params)
                if result:
                    logger.info(f"Найден товар {nm_id} через {endpoint}")
                    return result
            except Exception as e:
                logger.debug(f"Search via {endpoint} failed: {e}")
                continue

        return None

    async def get_stocks_by_nm(self, nm_id: int) -> List[Dict]:
        """
        Получает остатки конкретного товара по всем складам.

        Args:
            nm_id: Артикул WB

        Returns:
            Список остатков по складам
        """
        try:
            # Получаем все остатки и фильтруем
            all_remains = await self.get_warehouse_remains()

            result = []
            for item in all_remains:
                if item.get('nmId') == nm_id or item.get('nm_id') == nm_id:
                    result.append(item)

            return result
        except Exception as e:
            logger.error(f"Error getting stocks for {nm_id}: {e}")
            return []
