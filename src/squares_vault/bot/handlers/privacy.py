"""Privacy and lifecycle handlers."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="privacy")


@router.message(Command("storage"))
async def cmd_storage(message: Message) -> None:
    """Show storage stats."""
    user_id = message.from_user.id
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as c FROM records WHERE user_id = ?", (user_id,)) as cur:
            records = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM reminders WHERE user_id = ?", (user_id,)) as cur:
            reminders = (await cur.fetchone())["c"]
            
    await message.reply(f"📊 **Storage Stats**\n\nRecords: {records}\nReminders: {reminders}\n\nAll your documents are securely backed up in your Google Drive.")


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Export data."""
    await message.reply(
        "📦 Your data is already safely stored in your Google Drive folder named 'SQUARES Vault Backup'.\n"
        "You can view and download everything directly from there!"
    )


@router.message(Command("deletemydata"))
async def cmd_deletemydata(message: Message) -> None:
    """Delete all user data (7 steps)."""
    user_id = message.from_user.id
    
    # 1. Ask for confirmation
    await message.reply(
        "⚠️ **DANGER ZONE** ⚠️\n\n"
        "Are you absolutely sure you want to delete your account? This will:\n"
        "1. Delete all your data from my database.\n"
        "2. Keep a tombstone for synchronization.\n"
        "3. (Your files in Google Drive will remain untouched).\n\n"
        "Reply with `CONFIRM DELETE` to proceed."
    )


@router.message(F.text == "CONFIRM DELETE")
async def process_delete_my_data(message: Message) -> None:
    """Execute deletion."""
    user_id = message.from_user.id
    
    import hashlib
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc).isoformat() + "Z"
    user_hash = hashlib.sha256(f"deleted_{user_id}_{now}".encode()).hexdigest()
    
    async with get_db() as db:
        # Delete everything
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_keys WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM records WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM files WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM ai_usage WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM ai_queue WHERE user_id = ?", (user_id,))
        
        # Add tombstone
        await db.execute("INSERT INTO tombstones (user_hash, deleted_at) VALUES (?, ?)", (user_hash, now))
        await db.commit()
        
    await message.reply("✅ Your account and data have been permanently deleted.")
