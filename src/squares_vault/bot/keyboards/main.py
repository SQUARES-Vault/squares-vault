"""Main keyboards for SQUARES Vault."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from squares_vault.i18n.loader import t


def get_main_keyboard(locale: str = "en") -> ReplyKeyboardMarkup:
    """Return the persistent bottom keyboard menu."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=t("menu_add", locale=locale)),
                KeyboardButton(text=t("menu_find", locale=locale)),
                KeyboardButton(text=t("menu_browse", locale=locale)),
            ],
            [
                KeyboardButton(text=t("menu_ask", locale=locale)),
                KeyboardButton(text=t("menu_upcoming", locale=locale)),
                KeyboardButton(text=t("menu_settings", locale=locale)),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
