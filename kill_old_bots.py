#!/usr/bin/env python3
"""
Убивает все старые процессы бота перед запуском нового.
"""
import os
import sys
import signal
import psutil
import time

def kill_old_bot_processes():
    """Убивает все старые процессы bot.py и run.py"""
    current_pid = os.getpid()
    killed_count = 0

    print(f"🔍 Current PID: {current_pid}")
    print(f"🔍 Searching for old bot processes...")

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

                print(f"⚠️  Killing old bot process: PID {pid} - {cmdline_str[:100]}")

                try:
                    # Пробуем SIGTERM
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.5)

                    # Если не помогло - SIGKILL
                    if psutil.pid_exists(pid):
                        os.kill(pid, signal.SIGKILL)

                    killed_count += 1
                    print(f"✅ Killed PID {pid}")
                except ProcessLookupError:
                    pass
                except Exception as e:
                    print(f"❌ Failed to kill PID {pid}: {e}")

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if killed_count > 0:
        print(f"\n✅ Killed {killed_count} old bot process(es)")
        print(f"⏳ Waiting 3 seconds for cleanup...")
        time.sleep(3)
    else:
        print(f"\n✅ No old bot processes found")

    return killed_count

if __name__ == "__main__":
    print("=" * 50)
    print("🔥 KILLING OLD BOT PROCESSES")
    print("=" * 50)
    print()

    try:
        killed = kill_old_bot_processes()
        print()
        print("=" * 50)
        print(f"✅ DONE - Killed {killed} processes")
        print("=" * 50)
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        sys.exit(1)
