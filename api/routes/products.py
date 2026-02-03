"""
API для поиска товаров WB по артикулу.

Использует browser automation для работы со страницей warehouse-remains.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Optional

from database import Database
from browser.redistribution import get_redistribution_service
from api.main import get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/products/search")
async def search_product(
    q: str = Query(..., min_length=1, description="Артикул WB (nmId) или часть артикула"),
    supplier_id: Optional[int] = Query(None, description="ID поставщика"),
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Поиск товара по артикулу WB.

    Использует browser automation для поиска через модальное окно
    "Перераспределить остатки" на странице warehouse-remains.
    """
    user_id = user['user_id']

    # Получаем браузерную сессию с cookies
    session = db.get_browser_session(user_id)
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Browser session not found. Please authenticate via /auth"
        )

    cookies_encrypted = session.get('cookies_encrypted')
    if not cookies_encrypted:
        raise HTTPException(
            status_code=401,
            detail="No cookies in session. Please re-authenticate via /auth"
        )

    try:
        service = get_redistribution_service()

        # Поиск через модальное окно с autocomplete
        logger.info(f"Searching for product: {q}")
        results = await service.search_product_via_modal(cookies_encrypted, q)

        if not results:
            # Попробуем получить все остатки и найти там
            logger.info("Modal search returned nothing, trying table parse...")
            all_stocks = await service.get_warehouse_stocks(cookies_encrypted)

            if all_stocks:
                # Ищем по артикулу
                try:
                    nm_id = int(q)
                    for item in all_stocks:
                        if item.get('nmId') == nm_id:
                            return {
                                "found": True,
                                "nm_id": nm_id,
                                "product_name": item.get('subject') or item.get('brand'),
                                "brand": item.get('brand'),
                                "total_quantity": item.get('totalQuantity', 0),
                                "warehouses": []
                            }
                except ValueError:
                    pass

            return {
                "found": False,
                "query": q,
                "message": "Product not found"
            }

        # Если нашли через autocomplete
        if len(results) == 1:
            item = results[0]
            return {
                "found": True,
                "nm_id": item.get('nmId'),
                "product_name": item.get('name') or item.get('text'),
                "suggestions": results
            }

        # Несколько результатов - возвращаем список для выбора
        return {
            "found": True,
            "multiple": True,
            "query": q,
            "suggestions": results
        }

    except Exception as e:
        logger.error(f"Error searching product '{q}': {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search product: {str(e)}"
        )
