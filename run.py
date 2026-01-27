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


def kill_old_bot_processes():
    """Убивает все старые процессы bot.py и run.py используя psutil"""
    import os
    import signal
    import time

    try:
        import psutil
    except ImportError:
        logger.warning("psutil not installed, skipping process cleanup")
        return 0

    current_pid = os.getpid()
    killed_count = 0

    logger.info(f"🔍 Current PID: {current_pid}")
    logger.info(f"🔍 Searching for old bot processes...")

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if not cmdline:
                continue

            cmdline_str = ' '.join(cmdline)

            # Проверяем, это процесс бота
            if ('python' in cmdline_str.lower() and
                ('bot.py' in cmdline_str or 'run.py' in cmdline_str)):

                pid = proc.info['pid']

                # Не убиваем текущий процесс
                if pid == current_pid:
                    continue

                logger.warning(f"⚠️  Killing old bot process: PID {pid} - {cmdline_str[:100]}")

                try:
                    # Пробуем SIGTERM
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.5)

                    # Если не помогло - SIGKILL
                    if psutil.pid_exists(pid):
                        os.kill(pid, signal.SIGKILL)

                    killed_count += 1
                    logger.info(f"✅ Killed PID {pid}")
                except ProcessLookupError:
                    pass
                except Exception as e:
                    logger.error(f"❌ Failed to kill PID {pid}: {e}")

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if killed_count > 0:
        logger.info(f"✅ Killed {killed_count} old bot process(es)")
        logger.info(f"⏳ Waiting 3 seconds for cleanup...")
        time.sleep(3)
    else:
        logger.info(f"✅ No old bot processes found")

    return killed_count


def main():
    """Главная функция запуска обоих сервисов"""
    # КРИТИЧНО: Убиваем все старые процессы bot.py перед запуском
    logger.info("=" * 50)
    logger.info("🔥 KILLING OLD BOT PROCESSES")
    logger.info("=" * 50)

    try:
        killed = kill_old_bot_processes()
        logger.info(f"Process cleanup completed: {killed} processes killed")
    except Exception as e:
        logger.error(f"Failed to kill old processes: {e}", exc_info=True)

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
