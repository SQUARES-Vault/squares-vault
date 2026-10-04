"""Private chat only middleware.

Blueprint S3: Group, supergroup and channel updates are ignored.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message

from squares_vault.i18n.loader import t


class PrivateChatOnlyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.chat.type != "private":
                # We can answer once or just drop it. Blueprint S3 says "ignored", 
                # but it's polite to answer if someone adds bot to a group.
                # Just drop to be strictly silent, or answer once.
                return None
        return await handler(event, data)
