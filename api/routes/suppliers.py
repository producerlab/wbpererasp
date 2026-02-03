"""
API для управления поставщиками (мультиаккаунт).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict, Any
from datetime import datetime

from database import Database
from api.main import get_current_user, get_db
from config import Config


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
    created_at: Any  # datetime from PostgreSQL, str from SQLite

    class Config:
        from_attributes = True


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

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[GET /suppliers] user_id={user_id}")

    # Проверяем админа
    is_admin = user_id in Config.ADMIN_IDS
    if is_admin:
        logger.info(f"[GET /suppliers] ADMIN user detected: {user_id}")

    suppliers = db.get_suppliers(user_id)
    logger.info(f"[GET /suppliers] Existing suppliers: {len(suppliers)}")

    # АДМИНСКИЙ РЕЖИМ: создаем моковые suppliers для тестирования
    if not suppliers and is_admin:
        logger.info(f"[GET /suppliers] Creating MOCK suppliers for admin")
        try:
            # Создаем фейковый токен
            token_id = db.add_wb_token(
                user_id=user_id,
                encrypted_token="admin_mock_session",
                name="Admin DEMO Token"
            )
            logger.info(f"[GET /suppliers] Created admin token_id={token_id}")

            # Создаем несколько тестовых suppliers (ФИО владельцев)
            mock_suppliers = [
                "Хоснуллин Роман Аликович ИП (ИНН: 781434518365)",
                "Яковлев Вячеслав Валерьевич (ИНН: 246522599123)",
                "Колосов Глеб Андреевич (ИНН: 781436273350)"
            ]

            for i, supplier_name in enumerate(mock_suppliers):
                supplier_id = db.add_supplier(
                    user_id=user_id,
                    name=supplier_name,
                    token_id=token_id,
                    is_default=(i == 0)
                )
                logger.info(f"[GET /suppliers] Created admin supplier_id={supplier_id}: {supplier_name}")

            # Перезагружаем suppliers
            suppliers = db.get_suppliers(user_id)
            logger.info(f"[GET /suppliers] Created {len(suppliers)} MOCK suppliers for admin")

        except Exception as e:
            logger.error(f"[GET /suppliers] Error creating admin suppliers: {e}", exc_info=True)

    # Fallback для старых browser_sessions (миграция)
    # Если suppliers пуст, но есть активная browser_session - создаем supplier
    if not suppliers and not is_admin:
        sessions = db.get_browser_sessions(user_id, active_only=True)
        logger.info(f"[GET /suppliers] Active browser_sessions: {len(sessions) if sessions else 0}")
        if sessions:
            # Берем первую активную сессию
            session = sessions[0]
            supplier_name = session.get('supplier_name') or f"Кабинет {session['phone'][-4:]}"
            logger.info(f"[GET /suppliers] Creating supplier: {supplier_name}")

            try:
                # Создаем фейковый токен для browser-based авторизации
                token_id = db.add_wb_token(
                    user_id=user_id,
                    encrypted_token="browser_session",
                    name=f"Browser Session ({session['phone'][-4:]})"
                )
                logger.info(f"[GET /suppliers] Created token_id={token_id}")

                # Создаем supplier
                supplier_id = db.add_supplier(
                    user_id=user_id,
                    name=supplier_name,
                    token_id=token_id,
                    is_default=True
                )
                logger.info(f"[GET /suppliers] Created supplier_id={supplier_id}")

                # Перезагружаем список suppliers
                suppliers = db.get_suppliers(user_id)
                logger.info(f"[GET /suppliers] After creation: {len(suppliers)} suppliers")
            except Exception as e:
                logger.error(f"[GET /suppliers] Error creating supplier: {e}", exc_info=True)
                raise
        else:
            logger.warning(f"[GET /suppliers] No active browser_sessions found for user {user_id}")

    logger.info(f"[GET /suppliers] Returning {len(suppliers)} suppliers")
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
