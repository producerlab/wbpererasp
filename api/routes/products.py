"""
API для поиска товаров WB по артикулу.

Использует WB Internal API с cookies браузерной сессии.
Это позволяет получать данные напрямую без парсинга страниц.
"""

import logging
import json
import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Optional, List

from database import Database
from api.main import get_current_user, get_db
from utils.encryption import decrypt_token

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_warehouse_remains_via_api(cookies_encrypted: str) -> List[Dict]:
    """
    Получает остатки товаров через внутренний API WB.

    Args:
        cookies_encrypted: Зашифрованные cookies из browser_session

    Returns:
        Список товаров с остатками по складам
    """
    # Расшифровываем cookies
    try:
        cookies_json = decrypt_token(cookies_encrypted)
        cookies_list = json.loads(cookies_json)

        # Преобразуем в словарь для aiohttp
        cookies_dict = {}
        for cookie in cookies_list:
            if isinstance(cookie, dict):
                name = cookie.get('name')
                value = cookie.get('value')
                if name and value:
                    cookies_dict[name] = value

        logger.info(f"Загружено {len(cookies_dict)} cookies для API запроса")
    except Exception as e:
        logger.error(f"Ошибка расшифровки cookies: {e}")
        raise HTTPException(status_code=401, detail="Invalid session cookies")

    # Делаем запрос к внутреннему API WB
    base_url = "https://seller.wildberries.ru"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Origin': base_url,
        'Referer': f'{base_url}/analytics-reports/warehouse-remains',
    }

    # Пробуем разные endpoints для получения остатков
    endpoints_to_try = [
        '/ns/balances/analytics-back/api/v1/warehouses-remains',
        '/ns/analytics-back/api/v1/warehouse-remains',
        '/ns/balances/api/v1/balances',
    ]

    async with aiohttp.ClientSession(cookies=cookies_dict, headers=headers) as session:
        for endpoint in endpoints_to_try:
            url = f"{base_url}{endpoint}"
            logger.info(f"Trying endpoint: {url}")

            try:
                async with session.get(url, timeout=30) as response:
                    logger.info(f"Response status from {endpoint}: {response.status}")

                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Got data from {endpoint}, type: {type(data)}")

                        # Определяем где находятся данные
                        if isinstance(data, list):
                            logger.info(f"Found {len(data)} items in list")
                            return data
                        elif isinstance(data, dict):
                            # Ищем данные в разных полях
                            for key in ['data', 'items', 'result', 'rows', 'warehouses', 'remains']:
                                if key in data and isinstance(data[key], list):
                                    logger.info(f"Found {len(data[key])} items in '{key}'")
                                    return data[key]
                            # Возвращаем весь dict если это единственный товар
                            if 'nmId' in data or 'nm_id' in data:
                                return [data]

                    elif response.status == 401:
                        logger.warning(f"Session expired (401) from {endpoint}")
                        raise HTTPException(
                            status_code=401,
                            detail="session_expired"
                        )
                    elif response.status == 403:
                        logger.warning(f"Access denied (403) from {endpoint}")
                        continue
                    else:
                        response_text = await response.text()
                        logger.warning(f"Error {response.status} from {endpoint}: {response_text[:200]}")
                        continue

            except aiohttp.ClientError as e:
                logger.error(f"Network error for {endpoint}: {e}")
                continue
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error for {endpoint}: {e}")
                continue

    logger.warning("All endpoints failed, returning empty list")
    return []


def find_product_in_remains(remains: List[Dict], nm_id: int) -> Optional[Dict]:
    """
    Ищет товар по nmId в списке остатков.

    Args:
        remains: Список товаров с остатками
        nm_id: Артикул WB для поиска

    Returns:
        Найденный товар или None
    """
    for item in remains:
        item_nm_id = item.get('nmId') or item.get('nm_id') or item.get('nmID')
        if item_nm_id == nm_id:
            return item
    return None


def extract_warehouse_stocks(item: Dict) -> List[Dict]:
    """
    Извлекает остатки по складам из данных товара.

    Args:
        item: Данные товара из API

    Returns:
        Список складов с остатками
    """
    warehouses = []

    # Пробуем разные форматы данных

    # Формат 1: warehouses как вложенный список
    if 'warehouses' in item and isinstance(item['warehouses'], list):
        for wh in item['warehouses']:
            warehouses.append({
                'warehouse_id': wh.get('warehouseId') or wh.get('warehouse_id'),
                'warehouse_name': wh.get('warehouseName') or wh.get('warehouse_name') or wh.get('name'),
                'quantity': wh.get('quantity') or wh.get('qty') or wh.get('stock', 0)
            })

    # Формат 2: остатки в полях с названиями складов
    warehouse_keywords = ['коледино', 'казань', 'электросталь', 'краснодар',
                         'екатеринбург', 'тула', 'невинномысск', 'рязань',
                         'новосибирск', 'хабаровск', 'минск', 'астана']

    for key, value in item.items():
        key_lower = key.lower()
        for wh_name in warehouse_keywords:
            if wh_name in key_lower and isinstance(value, (int, float)):
                if value > 0:
                    warehouses.append({
                        'warehouse_name': key,
                        'quantity': int(value)
                    })
                break

    # Формат 3: totalQuantity + отдельные поля
    if not warehouses and 'totalQuantity' in item:
        # Ищем все числовые поля которые могут быть складами
        for key, value in item.items():
            if key not in ['nmId', 'nm_id', 'nmID', 'totalQuantity', 'volume', 'price']:
                if isinstance(value, (int, float)) and value > 0:
                    warehouses.append({
                        'warehouse_name': key,
                        'quantity': int(value)
                    })

    return warehouses


@router.get("/products/search")
async def search_product(
    q: str = Query(..., min_length=1, description="Артикул WB (nmId)"),
    supplier_id: Optional[int] = Query(None, description="ID поставщика"),
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Поиск товара по артикулу WB (nmId).

    Использует внутренний API WB через сохраненные cookies браузерной сессии.
    Возвращает информацию о товаре и остатки по складам.
    """
    user_id = user['user_id']

    # Получаем браузерную сессию с cookies
    session = db.get_browser_session(user_id)
    if not session:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "no_session",
                "message": "Браузерная сессия не найдена. Пройдите авторизацию через /auth"
            }
        )

    cookies_encrypted = session.get('cookies_encrypted')
    if not cookies_encrypted:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "no_cookies",
                "message": "Нет cookies в сессии. Пройдите повторную авторизацию через /auth"
            }
        )

    # Проверяем что запрос - это число (nmId)
    try:
        nm_id = int(q)
    except ValueError:
        return {
            "found": False,
            "query": q,
            "message": "Введите числовой артикул WB (nmId)"
        }

    logger.info(f"Searching for product: {nm_id}")

    try:
        # Получаем остатки через внутренний API
        remains = await get_warehouse_remains_via_api(cookies_encrypted)

        if not remains:
            logger.warning("No remains data received from API")
            return {
                "found": False,
                "query": q,
                "nm_id": nm_id,
                "message": "Не удалось получить данные об остатках. Возможно, сессия истекла."
            }

        logger.info(f"Got {len(remains)} items from API")

        # Ищем товар по nmId
        product = find_product_in_remains(remains, nm_id)

        if not product:
            logger.info(f"Product {nm_id} not found in {len(remains)} items")

            # Логируем первые несколько nmId для отладки
            sample_ids = [item.get('nmId') or item.get('nm_id') for item in remains[:5]]
            logger.debug(f"Sample nmIds in data: {sample_ids}")

            return {
                "found": False,
                "query": q,
                "nm_id": nm_id,
                "message": "Товар не найден среди ваших остатков"
            }

        # Извлекаем остатки по складам
        warehouses = extract_warehouse_stocks(product)

        # Формируем ответ
        total_quantity = product.get('totalQuantity') or product.get('total_quantity') or sum(
            wh.get('quantity', 0) for wh in warehouses
        )

        return {
            "found": True,
            "nm_id": nm_id,
            "product_name": product.get('subject') or product.get('name') or product.get('title'),
            "brand": product.get('brand'),
            "supplier_article": product.get('supplierArticle') or product.get('vendorCode'),
            "total_quantity": total_quantity,
            "warehouses": warehouses,
            "source": "internal_api"
        }

    except HTTPException as e:
        # Пробрасываем HTTP ошибки (включая session_expired)
        if e.detail == "session_expired":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "session_expired",
                    "message": "Сессия истекла. Пройдите повторную авторизацию через /auth"
                }
            )
        raise

    except Exception as e:
        logger.error(f"Error searching product '{q}': {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка поиска: {str(e)}"
        )
