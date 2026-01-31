"""
API для управления поставщиками (мультиаккаунт).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict

from database import Database
from api.main import get_current_user, get_db


router = APIRouter()


class SupplierCreate(BaseModel):
    """Модель для создания поставщика"""
    name: str
    token_id: int
    is_default: bool = False


class SupplierResponse(BaseModel):
    """Модель ответа с поставщиком"""
    id: int
    name: str
    token_id: int
    token_name: str
    is_default: int
    created_at: str


@router.get("/suppliers", response_model=List[SupplierResponse])
async def get_suppliers(
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Получить список поставщиков пользователя.

    Возвращает все связки ИП/ООО с токенами.

    После SMS авторизации suppliers создаются автоматически из всех
    доступных профилей WB (мультиаккаунт поддерживается).

    Для старых browser_sessions (до добавления парсинга профилей) -
    автоматически создает хотя бы один supplier при первом запросе.
    """
    user_id = user['user_id']
    suppliers = db.get_suppliers(user_id)

    # Fallback для старых browser_sessions (миграция)
    # Если suppliers пуст, но есть активная browser_session - создаем supplier
    if not suppliers:
        sessions = db.get_browser_sessions(user_id, active_only=True)
        if sessions:
            # Берем первую активную сессию
            session = sessions[0]
            supplier_name = session.get('supplier_name') or f"Кабинет {session['phone'][-4:]}"

            # Создаем фейковый токен для browser-based авторизации
            token_id = db.add_wb_token(
                user_id=user_id,
                encrypted_token="browser_session",
                name=f"Browser Session ({session['phone'][-4:]})"
            )

            # Создаем supplier
            db.add_supplier(
                user_id=user_id,
                name=supplier_name,
                token_id=token_id,
                is_default=True
            )

            # Перезагружаем список suppliers
            suppliers = db.get_suppliers(user_id)

    return suppliers


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
async def create_supplier(
    supplier: SupplierCreate,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Создать нового поставщика.

    Связывает название (ИП/ООО) с WB API токеном.
    """
    user_id = user['user_id']

    # Проверяем что токен принадлежит пользователю
    token = db.get_wb_token(user_id, supplier.token_id)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found"
        )

    supplier_id = db.add_supplier(
        user_id=user_id,
        name=supplier.name,
        token_id=supplier.token_id,
        is_default=supplier.is_default
    )

    return {"id": supplier_id, "message": "Supplier created"}


@router.delete("/suppliers/{supplier_id}")
async def delete_supplier(
    supplier_id: int,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """Удалить поставщика"""
    # Проверяем что поставщик принадлежит пользователю
    supplier = db.get_supplier(supplier_id)
    if not supplier or supplier['user_id'] != user['user_id']:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found"
        )

    success = db.delete_supplier(supplier_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete supplier"
        )

    return {"message": "Supplier deleted"}
