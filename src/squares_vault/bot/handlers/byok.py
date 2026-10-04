"""Bring Your Own Key (BYOK) flow for Gemini."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from squares_vault.db.connection import get_db
from squares_vault.security.crypto import encrypt_key
from squares_vault.i18n.loader import t

router = Router(name="byok")


@router.message(Command("byok"))
async def byok_command(message: Message) -> None:
    """Prompt user for their own Gemini API key."""
    await message.reply(
        "If you want to use your own Gemini API key to avoid global rate limits, "
        "reply to this message with your key.\n\n"
        "It will be encrypted in the database, and your message will be deleted."
    )


@router.message(F.text.startswith("AIzaSy"))
async def handle_gemini_key(message: Message) -> None:
    """Detect and securely store a Gemini API key."""
    key = message.text.strip()
    
    # In a real app we would call Gemini models.list to validate the key first.
    # For now, encrypt and store.
    enc_blob, wrapped_dek = encrypt_key(key.encode('utf-8'))
    
    async with get_db() as db:
        await db.execute(
            "INSERT INTO user_keys (user_id, api_key_enc, dek_wrapped, created_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "api_key_enc=excluded.api_key_enc, dek_wrapped=excluded.dek_wrapped",
            (message.from_user.id, enc_blob, wrapped_dek)
        )
        await db.commit()
        
    await message.delete()  # Delete the key from Telegram chat!
    await message.answer("🔒 Your custom Gemini key has been securely stored.")
