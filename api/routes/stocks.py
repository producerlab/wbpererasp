"""
API для получения остатков товаров на складах WB.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict

from database import Database
from wb_api.client import WBApiClient
from wb_api.stocks import StocksAPI
from api.main import get_current_user, get_db


router = APIRouter()


@router.get("/stocks/{nm_id}")
async def get_stocks_by_nm_id(
    nm_id: int,
    supplier_id: int,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Получить остатки товара по складам.

    Args:
        nm_id: Артикул WB (nmId)
        supplier_id: ID поставщика

    Returns:
        Список остатков по складам
    """
    user_id = user['user_id']

    # Получаем поставщика и токен
    supplier = db.get_supplier(supplier_id)
    if not supplier or supplier['user_id'] != user_id:
        raise HTTPException(status_code=404, detail="Supplier not found")

    token = db.get_wb_token(user_id, supplier['token_id'])
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    try:
        async with WBApiClient(token['token']) as client:
            api = StocksAPI(client)
            stocks = await api.get_stocks_for_sku(str(nm_id))

            warehouses = [
                {
                    "warehouse_id": s.warehouse_id,
                    "warehouse_name": s.warehouse_name,
                    "quantity": s.quantity,
                    "available": s.available,
                    "in_way_to_client": s.in_way_to_client,
                    "in_way_from_client": s.in_way_from_client
                }
                for s in stocks
            ]

            return {
                "nm_id": nm_id,
                "total_quantity": sum(s.quantity for s in stocks),
                "warehouses": warehouses
            }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get stocks: {str(e)}"
        )
