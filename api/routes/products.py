"""
API для поиска товаров WB по артикулу.

Использует внутренний API WB с cookies браузерной сессии.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Optional

from database import Database
from wb_api.internal_client import WBInternalClient
from api.main import get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/products/search")
async def search_product(
    q: str = Query(..., min_length=3, description="Артикул WB (nmId)"),
    supplier_id: Optional[int] = Query(None, description="ID поставщика"),
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Поиск товара по артикулу WB.

    Использует cookies браузерной сессии для доступа к внутреннему API WB.
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
        nm_id = int(q)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid article number. Please enter a valid WB nmId"
        )

    try:
        async with WBInternalClient(cookies_encrypted) as client:
            # Получаем остатки товара
            logger.info(f"Searching for product {nm_id}")

            # Сначала попробуем получить все остатки
            remains = await client.get_warehouse_remains()
            logger.info(f"Got {len(remains)} items from warehouse remains")

            # Ищем наш товар
            product_stocks = []
            product_name = None

            for item in remains:
                item_nm = item.get('nmId') or item.get('nm_id') or item.get('nmID')
                if item_nm == nm_id:
                    product_name = item.get('name') or item.get('subject') or item.get('productName')
                    product_stocks.append({
                        "warehouse_id": item.get('warehouseId') or item.get('warehouse_id'),
                        "warehouse_name": item.get('warehouseName') or item.get('warehouse_name') or 'Unknown',
                        "quantity": item.get('quantity') or item.get('qty') or 0,
                        "available": item.get('available') or item.get('quantity') or 0
                    })

            if not product_stocks:
                # Товар не найден в остатках - возможно его нет на складах
                return {
                    "found": False,
                    "nm_id": nm_id,
                    "message": "Product not found in warehouse remains"
                }

            total_quantity = sum(s['quantity'] for s in product_stocks)

            return {
                "found": True,
                "nm_id": nm_id,
                "product_name": product_name,
                "total_quantity": total_quantity,
                "warehouses": product_stocks
            }

    except Exception as e:
        logger.error(f"Error searching product {nm_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search product: {str(e)}"
        )
