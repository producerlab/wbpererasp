"""
Запуск Telegram бота + FastAPI сервера + Workers одновременно.

Использование:
    python run.py

Компоненты:
- Telegram Bot (aiogram)
- FastAPI Server (Mini App backend)
- Task Workers (обработка перемещений)
- Quota Monitor (мониторинг квот) - опционально
"""

import asyncio
import logging
import sys
import os
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


async def run_workers(num_workers: int = 3, bot=None):
    """
    Запускает пул воркеров для обработки задач.

    Args:
        num_workers: Количество воркеров
        bot: Telegram bot instance для уведомлений
    """
    from workers.task_worker import get_worker_pool, shutdown_worker_pool

    # Callback для отправки уведомлений через бота
    async def notify_user(user_id: int, message: str):
        if bot:
            try:
                await bot.send_message(user_id, message, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")

    try:
        pool = await get_worker_pool(
            num_workers=num_workers,
            notify_callback=notify_user if bot else None
        )
        await pool.start()
    except Exception as e:
        logger.error(f"Worker pool error: {e}", exc_info=True)
    finally:
        await shutdown_worker_pool()


async def run_quota_monitor(bot=None):
    """
    Запускает мониторинг квот на складах.

    Args:
        bot: Telegram bot instance для уведомлений
    """
    from browser.quota_monitor import get_quota_monitor, shutdown_quota_monitor

    # Callback для уведомлений
    async def notify_user(user_id: int, message: str):
        if bot:
            try:
                await bot.send_message(user_id, message, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Failed to notify user {user_id}: {e}")

    try:
        monitor = await get_quota_monitor(
            check_interval=60,  # каждую минуту
            notify_callback=notify_user if bot else None
        )
        await monitor.start()
    except Exception as e:
        logger.error(f"Quota monitor error: {e}", exc_info=True)
    finally:
        await shutdown_quota_monitor()


async def run_all_services():
    """Запускает все асинхронные сервисы параллельно"""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    # Создаём бота для уведомлений
    bot = None
    if Config.BOT_TOKEN:
        bot = Bot(
            token=Config.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )

    # Проверяем, нужны ли воркеры (только если Redis настроен)
    workers_enabled = bool(Config.REDIS_URL)

    tasks = []

    # Telegram бот (основной)
    from bot import main as bot_main
    tasks.append(asyncio.create_task(bot_main()))

    # Workers (если Redis настроен)
    if workers_enabled:
        logger.info("Redis configured - starting workers...")
        num_workers = int(os.getenv('NUM_WORKERS', '3'))
        tasks.append(asyncio.create_task(run_workers(num_workers, bot)))

        # Мониторинг квот (опционально)
        if os.getenv('ENABLE_QUOTA_MONITOR', '').lower() == 'true':
            logger.info("Starting quota monitor...")
            tasks.append(asyncio.create_task(run_quota_monitor(bot)))
    else:
        logger.warning("Redis not configured - workers disabled")

    try:
        # Ждём завершения любой задачи (или все)
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Services cancelled")
    finally:
        # Cleanup
        if bot:
            await bot.session.close()


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

    workers_enabled = bool(Config.REDIS_URL)
    quota_monitor_enabled = os.getenv('ENABLE_QUOTA_MONITOR', '').lower() == 'true'

    print("=" * 50)
    print("🚀 WB Redistribution Bot + API")
    print("=" * 50)
    print()
    print("📱 Telegram Bot: Starting...")
    print("🌐 FastAPI Server: http://localhost:8080")
    print("📚 API Docs: http://localhost:8080/docs")
    print("🖥  Mini App: http://localhost:8080/webapp")
    print()
    print(f"👷 Workers: {'Enabled' if workers_enabled else 'Disabled (no Redis)'}")
    print(f"📊 Quota Monitor: {'Enabled' if quota_monitor_enabled else 'Disabled'}")
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

    # Запускаем все асинхронные сервисы
    try:
        if workers_enabled:
            # Режим с воркерами
            asyncio.run(run_all_services())
        else:
            # Только бот (без воркеров)
            asyncio.run(run_telegram_bot())
    except KeyboardInterrupt:
        logger.info("\n\n✅ Stopping services...")
        sys.exit(0)


if __name__ == "__main__":
    main()
