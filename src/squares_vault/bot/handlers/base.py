"""Base handlers: /start, /help, /menu."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from squares_vault.bot.keyboards.main import get_main_keyboard
from squares_vault.i18n.loader import t

router = Router(name="base")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help."""
    await message.answer(t("help_text"))


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    """Show the main keyboard."""
    await message.answer("Menu:", reply_markup=get_main_keyboard())
