#!/usr/bin/env python3
"""Проверка browser_session в БД"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

from db_factory import get_database

db = get_database()

telegram_id = 98314773

print(f"Checking session for user {telegram_id}...")
print()

session = db.get_browser_session(telegram_id)

if session:
    print("✅ Session found:")
    for key, value in session.items():
        if key == 'cookies_encrypted':
            print(f"   {key}: {value[:50]}..." if value else f"   {key}: None")
        else:
            print(f"   {key}: {value}")
else:
    print("❌ No session found")

print()
print("All sessions:")
sessions = db.get_browser_sessions(telegram_id, active_only=False)
print(f"Total: {len(sessions)}")
for s in sessions:
    print(f"  - ID: {s.get('id')}, status: {s.get('status')}, phone: {s.get('phone')}")
