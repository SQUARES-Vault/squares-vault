"""Middleware to allow bottom menu buttons to interrupt active FSM states cleanly."""

from __future__ import annotations

from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from aiogram.fsm.context import FSMContext

MENU_BUTTON_EXACT = {
    "➕ add",
    "🔍 search",
    "🔍 find",
    "📁 browse",
    "💬 ask your vault",
    "💬 ask vault",
    "📅 upcoming",
    "🗓️ upcoming",
    "⚙️ settings",
    "add",
    "search",
    "find",
    "browse",
    "ask your vault",
    "ask vault",
    "upcoming",
    "settings",
}


class MenuInterruptMiddleware(BaseMiddleware):
    """If user presses a persistent bottom menu button, clear any active wizard state."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text:
            raw = event.text.strip()
            raw_lower = raw.lower()

            # Exact button match, or starts with menu emoji + short menu label, or slash command
            is_menu_press = (
                raw_lower in MENU_BUTTON_EXACT
                or (raw.startswith(("➕", "🔍", "📁", "💬", "📅", "🗓️", "⚙️")) and len(raw) <= 25)
                or (raw.startswith("/") and len(raw.split()[0]) <= 20)
            )

            if is_menu_press:
                state: FSMContext | None = data.get("state")
                if state:
                    cur_state = await state.get_state()
                    if cur_state is not None:
                        # Clear active wizard state so user seamlessly switches to the selected menu
                        await state.clear()
        return await handler(event, data)
