"""
Telegram bot: /start shows a button that opens the Mini App (spec section 20).

The bot token is read from environment (TELEGRAM_BOT_TOKEN) and never
hardcoded or committed. Run standalone with:

    python -m app.telegram.bot
"""
from __future__ import annotations

import asyncio
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


async def run_bot() -> None:
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set - bot will not start")
        return
    if not settings.telegram_webapp_url:
        logger.warning("TELEGRAM_WEBAPP_URL not set - bot will not start")
        return

    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import CommandStart
    from aiogram.types import (
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        Message,
        WebAppInfo,
    )

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def handle_start(message: Message) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="OPEN ARBITRAGE APP",
                        web_app=WebAppInfo(url=settings.telegram_webapp_url),
                    )
                ]
            ]
        )
        await message.answer(
            "Realtime CEX spot arbitrage monitor. Tap below to open the app.",
            reply_markup=keyboard,
        )

    logger.info("Starting Telegram bot polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bot())
