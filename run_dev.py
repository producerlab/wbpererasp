"""
Development runner - uses .env.local for local development.
Production uses .env on Railway.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env.local for development
env_path = Path(__file__).parent / '.env.local'
if env_path.exists():
    print(f"🔧 Loading development environment from {env_path}")
    load_dotenv(env_path, override=True)
else:
    print("⚠️ .env.local not found, using .env")
    load_dotenv()

# Import and run the main app
from run import main
import asyncio

if __name__ == "__main__":
    print("🚀 Starting bot in DEVELOPMENT mode...")
    print(f"   Database: {os.getenv('DATABASE_PATH', 'bot_data.db')}")
    print(f"   Bot token: {os.getenv('BOT_TOKEN', '')[:20]}...")
    asyncio.run(main())
