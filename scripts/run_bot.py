"""Entry point for the Telegram bot.

Run with: `uv run python scripts/run_bot.py`
"""
from __future__ import annotations

import asyncio

from zen.telegram_bot.bot import main

if __name__ == "__main__":
    asyncio.run(main())
