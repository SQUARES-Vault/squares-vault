"""Entry point for the Telegram bot long-polling service."""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from squares_vault.config import init_settings, settings
from squares_vault.db.connection import close_db, init_db
from squares_vault.logging_setup import get_logger, setup_logging
from squares_vault.bot.middlewares.private_chat import PrivateChatOnlyMiddleware
from squares_vault.bot.middlewares.ratelimit import RateLimitMiddleware
from squares_vault.bot.state import SqliteStorage
from squares_vault.bot.handlers.base import router as base_router

log = get_logger(__name__)


async def main() -> None:
    # 1. Setup config and logging
    setup_logging()
    init_settings()
    
    # 2. Setup DB connection pool
    await init_db()

    # 3. Setup Bot and Dispatcher
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    
    storage = SqliteStorage()
    dp = Dispatcher(storage=storage)
    
    # Register global middlewares
    dp.message.middleware(PrivateChatOnlyMiddleware())
    dp.message.middleware(RateLimitMiddleware())
    
    # Register routers
    dp.include_router(base_router)
    
    # 4. Start polling
    log.info("bot_start_polling")
    try:
        # Drop pending updates so we don't process old messages on restart
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        log.info("bot_shutdown")
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
