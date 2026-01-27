"""
API для управления заявками на перемещение.

CRUD операции над redistribution_requests.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime

from database import Database
from wb_api.client import WBApiClient
from wb_api.supplies import SuppliesAPI, CargoType
from api.main import get_current_user, get_db
from utils.encryption import decrypt_token


router = APIRouter()


class RequestCreate(BaseModel):
    """Модель для создания заявки"""
    supplier_id: int
    nm_id: int
    product_name: str
    source_warehouse_id: int
    source_warehouse_name: str
    target_warehouse_id: int
    target_warehouse_name: str
    quantity: int


class RequestUpdate(BaseModel):
    """Модель для обновления заявки"""
    quantity: Optional[int] = None
    status: Optional[str] = None


class RequestResponse(BaseModel):
    """Модель ответа с заявкой"""
    id: int
    supplier_id: int
    supplier_name: str
    nm_id: int
    product_name: Optional[str]
    source_warehouse_id: int
    source_warehouse_name: Optional[str]
    target_warehouse_id: int
    target_warehouse_name: Optional[str]
    quantity: int
    status: str
    supply_id: Optional[str]
    created_at: str
    completed_at: Optional[str]


@router.get("/requests", response_model=List[RequestResponse])
async def get_requests(
    status_filter: Optional[str] = Query(None, alias="status"),
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Получить заявки пользователя.

    Query params:
    - status: pending, searching, completed, cancelled
    """
    user_id = user['user_id']
    requests = db.get_redistribution_requests(user_id, status_filter)
    return requests


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def create_request(
    request: RequestCreate,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Создать заявку на перемещение.

    После создания заявка переходит в статус 'pending'
    и бот начинает искать доступные слоты.
    """
    user_id = user['user_id']

    # Проверяем поставщика
    supplier = db.get_supplier(request.supplier_id)
    if not supplier or supplier['user_id'] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found"
        )

    # Создаём заявку
    request_id = db.add_redistribution_request(
        user_id=user_id,
        supplier_id=request.supplier_id,
        nm_id=request.nm_id,
        product_name=request.product_name,
        source_warehouse_id=request.source_warehouse_id,
        source_warehouse_name=request.source_warehouse_name,
        target_warehouse_id=request.target_warehouse_id,
        target_warehouse_name=request.target_warehouse_name,
        quantity=request.quantity
    )

    # TODO: Запустить фоновую задачу поиска слотов

    return {
        "id": request_id,
        "message": "Request created",
        "status": "pending"
    }


@router.get("/requests/{request_id}", response_model=RequestResponse)
async def get_request(
    request_id: int,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Получить заявку по ID"""
    user_id = user['user_id']

    request = db.get_redistribution_request(request_id)
    if not request or request['user_id'] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
        )

    return request


@router.patch("/requests/{request_id}")
async def update_request(
    request_id: int,
    request_update: RequestUpdate,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Обновить заявку.

    Можно изменить:
    - quantity: количество товара
    - status: статус (pending, searching, completed, cancelled)
    """
    user_id = user['user_id']

    # Проверяем заявку
    request = db.get_redistribution_request(request_id)
    if not request or request['user_id'] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
        )

    # Обновляем
    updates = request_update.dict(exclude_unset=True)

    # Если меняется статус на completed - ставим дату
    if updates.get('status') in ('completed', 'cancelled'):
        updates['completed_at'] = datetime.now().isoformat()

    success = db.update_redistribution_request(request_id, **updates)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update request"
        )

    return {"message": "Request updated"}


@router.delete("/requests/{request_id}")
async def delete_request(
    request_id: int,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Удалить заявку"""
    user_id = user['user_id']

    # Проверяем заявку
    request = db.get_redistribution_request(request_id)
    if not request or request['user_id'] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
        )

    # Если есть supply_id - отменяем поставку
    if request.get('supply_id'):
        try:
            supplier = db.get_supplier(request['supplier_id'])
            token = db.get_wb_token(user_id, supplier['token_id'])
            decrypted_token = decrypt_token(token['encrypted_token'])

            async with WBApiClient(decrypted_token) as client:
                api = SuppliesAPI(client)
                await api.cancel_supply(request['supply_id'])
        except Exception as e:
            # Логируем ошибку, но продолжаем удаление
            print(f"Failed to cancel supply: {e}")

    # Удаляем заявку
    success = db.delete_redistribution_request(request_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete request"
        )

    return {"message": "Request deleted"}


@router.post("/requests/{request_id}/execute")
async def execute_request(
    request_id: int,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Выполнить заявку - создать поставку WB.

    Создаёт реальную поставку через WB API.
    """
    user_id = user['user_id']

    # Получаем заявку
    request = db.get_redistribution_request(request_id)
    if not request or request['user_id'] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
        )

    if request['status'] != 'pending':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request is not in pending status"
        )

    # Получаем токен
    supplier = db.get_supplier(request['supplier_id'])
    token = db.get_wb_token(user_id, supplier['token_id'])

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found"
        )

    # Расшифровываем токен
    decrypted_token = decrypt_token(token['encrypted_token'])

    try:
        # Создаём поставку
        async with WBApiClient(decrypted_token) as client:
            api = SuppliesAPI(client)

            supply_name = f"Перемещение {request['nm_id']} → {request['target_warehouse_name']}"
            result = await api.create_supply(
                name=supply_name,
                warehouse_id=request['target_warehouse_id'],
                cargo_type=CargoType.BOX
            )

            if result.success:
                # Обновляем заявку
                db.update_redistribution_request(
                    request_id,
                    status='completed',
                    supply_id=result.supply_id,
                    completed_at=datetime.now().isoformat()
                )

                return {
                    "success": True,
                    "supply_id": result.supply_id,
                    "message": "Supply created"
                }
            else:
                return {
                    "success": False,
                    "error": result.error_message
                }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create supply: {str(e)}"
        )
