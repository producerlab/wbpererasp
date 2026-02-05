"""
API endpoints для управления сессиями WB.

Позволяет импортировать cookies из браузера для избежания частой SMS авторизации.
"""

import logging
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional

from database import Database
from api.main import get_current_user, get_db
from utils.encryption import encrypt_token

logger = logging.getLogger(__name__)

router = APIRouter()


class CookieItem(BaseModel):
    """Модель одного cookie"""
    name: str
    value: str
    domain: str
    path: str = "/"
    expires: Optional[float] = None
    httpOnly: Optional[bool] = False
    secure: Optional[bool] = False
    sameSite: Optional[str] = "Lax"


class ImportCookiesRequest(BaseModel):
    """Запрос на импорт cookies из браузера"""
    cookies: List[CookieItem]


@router.post("/sessions/import-cookies")
async def import_cookies_from_browser(
    request: ImportCookiesRequest,
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Импорт cookies из браузера для обновления сессии.

    Позволяет использовать существующую сессию из браузера (Chrome/Firefox)
    без необходимости повторной SMS авторизации.

    Использование:
    1. Зайдите на seller.wildberries.ru в браузере
    2. Убедитесь что вы залогинены
    3. Используйте расширение Cookie-Editor для экспорта cookies
    4. Отправьте cookies в этот endpoint

    Returns:
        Информация об обновлённой сессии
    """
    user_id = user['user_id']

    try:
        # Фильтруем только wildberries cookies
        wb_cookies = []
        for cookie in request.cookies:
            if 'wildberries' in cookie.domain.lower():
                # Валидируем sameSite - Playwright требует строго Strict|Lax|None
                same_site = cookie.sameSite
                if same_site not in ['Strict', 'Lax', 'None']:
                    same_site = 'Lax'  # По умолчанию

                # Преобразуем в формат Playwright
                wb_cookies.append({
                    'name': cookie.name,
                    'value': cookie.value,
                    'domain': cookie.domain,
                    'path': cookie.path,
                    'expires': cookie.expires if cookie.expires else -1,
                    'httpOnly': cookie.httpOnly if cookie.httpOnly is not None else False,
                    'secure': cookie.secure if cookie.secure is not None else False,
                    'sameSite': same_site
                })

        if not wb_cookies:
            raise HTTPException(
                status_code=400,
                detail="No Wildberries cookies found. Make sure you're logged in to seller.wildberries.ru"
            )

        logger.info(f"Importing {len(wb_cookies)} Wildberries cookies for user {user_id}")

        # Проверяем наличие важных cookies
        important_cookies = ['WILDAUTHNEW_V3', 'WBToken', 'x-supplier-id']
        found_cookies = [c['name'] for c in wb_cookies]
        missing = [c for c in important_cookies if c not in found_cookies]

        if missing:
            logger.warning(f"Missing important cookies: {missing}")

        # Сериализуем и шифруем
        cookies_json = json.dumps(wb_cookies)
        cookies_encrypted = encrypt_token(cookies_json)

        # Сохраняем cookies в БД
        # Сначала деактивируем старые сессии
        db.invalidate_browser_session(user_id)
        # Затем создаём новую сессию
        db.add_browser_session(
            user_id=user_id,
            phone="",  # Телефон не требуется при импорте cookies
            cookies_encrypted=cookies_encrypted,
            supplier_name=None,
            expires_days=7  # Ставим 7 дней как у WB
        )
        logger.info(f"Imported browser session for user {user_id}")

        return {
            "success": True,
            "message": f"Successfully imported {len(wb_cookies)} cookies",
            "cookies_count": len(wb_cookies),
            "important_cookies_found": [c for c in important_cookies if c in found_cookies],
            "expires_days": 7
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing cookies: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to import cookies: {str(e)}"
        )


@router.post("/sessions/refresh")
async def refresh_session(
    user: Dict = Depends(get_current_user),
    db: Database = Depends(get_db)
):
    """
    Попытка обновить существующую сессию без SMS.

    Открывает главную страницу WB с текущими cookies.
    Если сессия ещё валидна, обновляет cookies.

    Returns:
        Результат попытки обновления
    """
    user_id = user['user_id']

    # Получаем текущую сессию
    session = db.get_browser_session(user_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail="No active session found. Please authenticate first."
        )

    cookies_encrypted = session.get('cookies_encrypted')
    if not cookies_encrypted:
        raise HTTPException(
            status_code=400,
            detail="No cookies in session. Please authenticate first."
        )

    try:
        from browser.redistribution import WBRedistributionService

        service = WBRedistributionService()
        new_cookies_encrypted = await service.refresh_session(cookies_encrypted)

        if new_cookies_encrypted:
            # Обновляем сессию с новыми cookies
            # Деактивируем старые сессии
            db.invalidate_browser_session(user_id)
            # Создаём новую с обновлёнными cookies
            db.add_browser_session(
                user_id=user_id,
                phone="",
                cookies_encrypted=new_cookies_encrypted,
                supplier_name=None,
                expires_days=7
            )

            logger.info(f"Session refreshed successfully for user {user_id}")
            return {
                "success": True,
                "message": "Session refreshed successfully",
                "expires_days": 7
            }
        else:
            logger.warning(f"Session refresh failed for user {user_id} - session expired")
            return {
                "success": False,
                "message": "Session expired. Please re-authenticate with SMS or import cookies from browser.",
                "requires_reauth": True
            }

    except Exception as e:
        logger.error(f"Error refreshing session: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh session: {str(e)}"
        )
