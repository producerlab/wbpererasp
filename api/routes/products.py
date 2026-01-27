"""
API для поиска товаров WB по артикулу.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Optional

from database import Database
from wb_api.client import WBApiClient
from wb_api.stocks import StocksAPI
from api.main import get_current_user, get_db
from utils.encryption import decrypt_token


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

    Возвращает информацию о товаре и его остатках.
    """
    user_id = user['user_id']

    # Получаем токен
    if supplier_id:
        supplier = db.get_supplier(supplier_id)
        if not supplier or supplier['user_id'] != user_id:
            raise HTTPException(status_code=404, detail="Supplier not found")
        token = db.get_wb_token(user_id, supplier['token_id'])
    else:
        token = db.get_wb_token(user_id)

    if not token:
        raise HTTPException(status_code=404, detail="WB API token not found")

    # Расшифровываем токен
    decrypted_token = decrypt_token(token['encrypted_token'])

    try:
        # Ищем товар
        async with WBApiClient(decrypted_token) as client:
            api = StocksAPI(client)

            # Пытаемся преобразовать в число
            try:
                nm_id = int(q)
            except ValueError:
                # Если не число - ищем по артикулу продавца
                product = await api.search_product_by_sku(q)
                if not product:
                    return {"found": False, "message": "Product not found"}
                nm_id = product.nm_id

            # Получаем остатки
            stocks = await api.get_stocks_for_sku(str(nm_id))

            if not stocks:
                return {
                    "found": True,
                    "nm_id": nm_id,
                    "product_name": None,
                    "total_quantity": 0,
                    "warehouses": []
                }

            # Формируем ответ
            total_quantity = sum(s.quantity for s in stocks)
            warehouses = [
                {
                    "warehouse_id": s.warehouse_id,
                    "warehouse_name": s.warehouse_name,
                    "quantity": s.quantity,
                    "available": s.available
                }
                for s in stocks
            ]

            return {
                "found": True,
                "nm_id": nm_id,
                "product_name": stocks[0].product_name if stocks else None,
                "total_quantity": total_quantity,
                "warehouses": warehouses
            }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search product: {str(e)}"
        )
