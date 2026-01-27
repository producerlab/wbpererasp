"""
API для получения списка складов WB.
"""

from fastapi import APIRouter, Depends
from typing import Dict, List

from wb_api.warehouses import WarehousesAPI
from api.main import get_current_user


router = APIRouter()


@router.get("/warehouses")
async def get_warehouses(
    user: Dict = Depends(get_current_user)
):
    """
    Получить список всех складов WB.

    Возвращает популярные склады из справочника.
    """
    warehouses = []

    for wh_id, wh_info in WarehousesAPI.POPULAR_WAREHOUSES.items():
        warehouses.append({
            "id": wh_id,
            "name": wh_info.get('name', f'Склад {wh_id}'),
            "region": wh_info.get('region', ''),
            "address": wh_info.get('address', '')
        })

    # Сортируем по названию
    warehouses.sort(key=lambda x: x['name'])

    return warehouses
