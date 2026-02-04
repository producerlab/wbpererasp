"""
FastAPI приложение для Telegram Mini App.

API endpoints для управления перераспределением остатков.
"""

import logging
from fastapi import FastAPI, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Dict, Optional

from config import Config
from db_factory import get_database
from api.auth import validate_telegram_web_app_data

logger = logging.getLogger(__name__)

# Создаём FastAPI приложение
app = FastAPI(
    title="WB Redistribution API",
    description="API для управления перераспределением остатков WB",
    version="1.0.0"
)

# CORS для Mini App
# Ограничиваем домены для безопасности, но разрешаем Telegram
def get_cors_origins():
    """Получает список разрешённых origins для CORS"""
    origins = [
        "https://web.telegram.org",
        "https://t.me",
        "https://telegram.org",
    ]
    # Добавляем WEBAPP_URL если указан
    if Config.WEBAPP_URL:
        origins.append(Config.WEBAPP_URL.rstrip('/'))
        # Также добавляем без протокола для совместимости
        if Config.WEBAPP_URL.startswith("https://"):
            origins.append(Config.WEBAPP_URL.replace("https://", "http://"))
    # Для локальной разработки
    origins.extend([
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ])
    return origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],
)

# Статические файлы (Mini App frontend)
app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp")


# Dependency для получения пользователя
async def get_current_user(
    x_telegram_init_data: Optional[str] = Header(None)
) -> Dict:
    """Получает текущего пользователя из Telegram initData"""
    if not x_telegram_init_data:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram init data"
        )

    return validate_telegram_web_app_data(x_telegram_init_data, Config.BOT_TOKEN)


# Dependency для БД
def get_db():
    """Возвращает экземпляр базы данных (SQLite или PostgreSQL)"""
    return get_database()


@app.get("/")
async def root():
    """Главная страница API"""
    return {
        "name": "WB Redistribution API",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/api/me")
async def get_me(user: Dict = Depends(get_current_user)):
    """
    Получить информацию о текущем пользователе.

    Тестовый endpoint для проверки авторизации.
    """
    return {
        "user_id": user['user_id'],
        "username": user.get('username'),
        "first_name": user.get('first_name')
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}


# Подключаем роутеры
from api.routes import suppliers, products, stocks, warehouses, requests, sessions

app.include_router(suppliers.router, prefix="/api", tags=["suppliers"])
app.include_router(products.router, prefix="/api", tags=["products"])
app.include_router(stocks.router, prefix="/api", tags=["stocks"])
app.include_router(warehouses.router, prefix="/api", tags=["warehouses"])
app.include_router(requests.router, prefix="/api", tags=["requests"])
app.include_router(sessions.router, prefix="/api", tags=["sessions"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
