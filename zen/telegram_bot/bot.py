"""Telegram bot bootstrap.

Run with: `uv run python -m zen.telegram_bot.bot`
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from zen.config.settings import get_settings
from zen.telegram_bot.client import AgentClient
from zen.telegram_bot.handlers import (
    detect_lang,
    format_ack,
    process_message,
    split_reply,
)

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(
        "Send me a SQL question in plain language and I'll draft a query.\n"
        "All SQL is AI-generated — review before running."
    )


@router.message()
async def on_text(message: Message) -> None:
    if not message.text or not message.from_user or message.bot is None:
        return
    settings = get_settings()
    # Acknowledge immediately in the user's language so they know we got it.
    await message.answer(format_ack(detect_lang(message.text)))
    client = AgentClient(
        base_url=settings.agent_api_base_url,
        token=settings.agent_api_token,
        # Budget one queued same-user run ahead of ours plus our own run; the
        # server bounds its lock wait to one run and answers BUSY beyond that.
        timeout_s=float(settings.agent_timeout_s * 2 + 10),
    )
    try:
        # Keep the "typing…" action alive (auto-resent every ~5s) until the
        # result is ready — the agent run can take tens of seconds.
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            reply = await process_message(
                message.text,
                f"tg:{message.from_user.id}",
                settings,
                client,
            )
    except Exception:
        # The user already got the ack — never leave them hanging silently.
        logger.exception("unhandled error while processing message")
        await message.answer("⚠️ Something went wrong — please try again.")
        return
    try:
        # Telegram caps messages at 4096 chars; long SQL is sent in chunks.
        for chunk in split_reply(reply):
            await message.answer(chunk, parse_mode="HTML")
    except TelegramAPIError:
        logger.exception("failed to deliver reply")
        await message.answer("⚠️ The reply could not be delivered — please try again.")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    token = settings.telegram_bot_token.get_secret_value()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Starting Telegram bot polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
