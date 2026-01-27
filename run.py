"""
Запуск Telegram бота + FastAPI сервера одновременно.

Использование:
    python run.py
"""

import asyncio
import logging
import sys
from threading import Thread

import uvicorn
from config import Config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


def run_api_server():
    """Запускает FastAPI сервер в отдельном потоке"""
    logger.info("Starting FastAPI server on port 8080...")
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )


async def run_telegram_bot():
    """Запускает Telegram бота"""
    from bot import main as bot_main

    logger.info("Starting Telegram bot...")
    await bot_main()


def main():
    """Главная функция запуска обоих сервисов"""
    # КРИТИЧНО: Убиваем все старые процессы bot.py перед запуском
    import subprocess
    import os
    try:
        current_pid = os.getpid()
        logger.info(f"Current process PID: {current_pid}")

        # Находим все Python процессы
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )

        for line in result.stdout.split('\n'):
            if 'python' in line.lower() and 'bot.py' in line:
                parts = line.split()
                if len(parts) > 1:
                    pid = int(parts[1])
                    if pid != current_pid:
                        logger.warning(f"Killing old bot process: PID {pid}")
                        try:
                            subprocess.run(["kill", "-9", str(pid)])
                        except:
                            pass
    except Exception as e:
        logger.warning(f"Failed to kill old processes: {e}")

    print("=" * 50)
    print("🚀 WB Redistribution Bot + API")
    print("=" * 50)
    print()
    print("📱 Telegram Bot: Starting...")
    print("🌐 FastAPI Server: http://localhost:8080")
    print("📚 API Docs: http://localhost:8080/docs")
    print("🖥  Mini App: http://localhost:8080/webapp")
    print()
    print("⏳ Press Ctrl+C to stop")
    print("=" * 50)
    print()

    # Запускаем API в отдельном потоке
    api_thread = Thread(target=run_api_server, daemon=True)
    api_thread.start()

    # Даём API время на старт
    import time
    time.sleep(2)

    # Запускаем бота в главном потоке
    try:
        asyncio.run(run_telegram_bot())
    except KeyboardInterrupt:
        logger.info("\n\n✅ Stopping services...")
        sys.exit(0)


if __name__ == "__main__":
    main()
