"""Reminder keyboards."""

from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_reminder_keyboard(record_id: str) -> InlineKeyboardMarkup:
    """Inline buttons for a reminder message."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Done", callback_data=f"rem:done:{record_id}")],
        [
            InlineKeyboardButton(text="💤 Snooze 1d", callback_data=f"rem:snz:{record_id}:1"),
            InlineKeyboardButton(text="💤 3d", callback_data=f"rem:snz:{record_id}:3"),
            InlineKeyboardButton(text="💤 1w", callback_data=f"rem:snz:{record_id}:7"),
        ],
        [InlineKeyboardButton(text="🔄 Renewed", callback_data=f"rem:rnw:{record_id}")],
        [InlineKeyboardButton(text="📄 Open file", callback_data=f"rem:opn:{record_id}")]
    ])
