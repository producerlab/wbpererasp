"""
API для поиска товаров WB по артикулу.

Использует WB Content API для получения информации о товарах.
Fallback на публичный API карточек.
"""

import logging
import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Optional

from database import Database
from api.main import get_current_user, get_db
from wb_api.client import WBApiClient
from wb_api.stocks import StocksAPI
from utils.encryption import decrypt_token

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_product_from_public_api(nm_id: int) -> Optional[Dict]:
    """
    Получает информацию о товаре через публичный API WB.
    Не требует авторизации.
    """
    try:
        # Публичный API карточки товара
        url = f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    products = data.get('data', {}).get('products', [])
                    if products:
                        product = products[0]
                        return {
                            "nm_id": product.get('id'),
                            "product_name": product.get('name'),
                            "brand": product.get('brand'),
                            "supplier": product.get('supplier'),
                            "price": product.get('salePriceU', 0) // 100,
                            "rating": product.get('reviewRating'),
                            "feedbacks": product.get('feedbacks'),
                        }
    except Exception as e:
        logger.warning(f"Public API failed for nm_id={nm_id}: {e}")

    return None


async def search_via_wb_api(api_token: str, nm_id: int) -> Optional[Dict]:
    """
    Поиск товара через WB Content API (требует токен).
    """
    try:
        async with WBApiClient(api_token) as client:
            stocks_api = StocksAPI(client)

            # Пробуем получить карточку товара по nmId
            products = await stocks_api.get_products_list(filter_nm_id=nm_id)

            if products:
                product = products[0]
                return {
                    "nm_id": product.nm_id,
                    "product_name": product.title,
                    "brand": product.brand,
                    "sku": product.sku,
                    "vendor_code": product.vendor_code,
                    "photos": product.photos[:1] if product.photos else [],
                }
    except Exception as e:
        logger.warning(f"WB API search failed for nm_id={nm_id}: {e}")

    return None


@router.get("/products/search")
async def search_product(
    q: str = Query(..., min_length=1, description="Артикул WB (nmId) или часть артикула"),
    supplier_id: Optional[int] = Query(None, description="ID поставщика"),
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Поиск товара по артикулу WB (nmId).

    Стратегия поиска:
    1. Публичный API WB (быстро, не требует авторизации)
    2. WB Content API (если есть API токен)
    """
    user_id = user['user_id']

    # Проверяем что запрос - это число (nmId)
    try:
        nm_id = int(q)
    except ValueError:
        # Если не число - возможно это артикул продавца
        # Для поиска по артикулу нужен API токен
        logger.info(f"Query '{q}' is not nmId, trying vendor code search")

        # Получаем API токен
        if supplier_id:
            supplier = db.get_supplier(supplier_id)
            if supplier:
                token_data = db.get_wb_token(user_id, supplier.get('token_id'))
                if token_data:
                    api_token = decrypt_token(token_data['encrypted_token'])
                    try:
                        async with WBApiClient(api_token) as client:
                            stocks_api = StocksAPI(client)
                            product = await stocks_api.search_product_by_sku(q)
                            if product:
                                return {
                                    "found": True,
                                    "nm_id": product.nm_id,
                                    "product_name": product.title,
                                    "brand": product.brand,
                                    "sku": product.sku,
                                }
                    except Exception as e:
                        logger.warning(f"Vendor code search failed: {e}")

        return {
            "found": False,
            "query": q,
            "message": "Введите числовой артикул WB (nmId)"
        }

    logger.info(f"Searching for product: {nm_id}")

    # Стратегия 1: Публичный API (быстро, без авторизации)
    result = await get_product_from_public_api(nm_id)
    if result:
        logger.info(f"Found product via public API: {result.get('product_name')}")
        return {
            "found": True,
            "source": "public_api",
            **result
        }

    # Стратегия 2: WB Content API (если есть токен)
    api_token = None
    if supplier_id:
        supplier = db.get_supplier(supplier_id)
        if supplier:
            token_data = db.get_wb_token(user_id, supplier.get('token_id'))
            if token_data:
                api_token = decrypt_token(token_data['encrypted_token'])
    else:
        # Пробуем получить любой активный токен пользователя
        token_data = db.get_wb_token(user_id)
        if token_data:
            api_token = decrypt_token(token_data['encrypted_token'])

    if api_token:
        result = await search_via_wb_api(api_token, nm_id)
        if result:
            logger.info(f"Found product via WB API: {result.get('product_name')}")
            return {
                "found": True,
                "source": "wb_api",
                **result
            }

    # Товар не найден
    return {
        "found": False,
        "query": q,
        "nm_id": nm_id,
        "message": "Товар не найден. Проверьте правильность артикула."
    }
