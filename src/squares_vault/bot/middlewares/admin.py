"""Admin gate middleware or filter."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from squares_vault import config


class AdminOnly(BaseFilter):
    """Filter that restricts a handler to admin IDs only."""
    
    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in config.settings.admin_user_ids
