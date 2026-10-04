"""Handlers for reminder inline callbacks."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from datetime import datetime, timezone, timedelta

from squares_vault.db.connection import get_db
from squares_vault.i18n.loader import t

router = Router(name="reminders")


@router.callback_query(F.data.startswith("rem:done:"))
async def on_reminder_done(cq: CallbackQuery) -> None:
    """Mark a record as done."""
    _, _, record_id = cq.data.split(":")
    
    async with get_db() as db:
        await db.execute("UPDATE records SET status = 'done' WHERE id = ?", (record_id,))
        # Cancel pending reminders
        await db.execute("UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND state = 'pending'", (record_id,))
        await db.commit()
        
    await cq.answer(t("rem_marked_done", locale="en"))
    # Hide the keyboard
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.reply("✅ Record marked as done.")


@router.callback_query(F.data.startswith("rem:snz:"))
async def on_reminder_snooze(cq: CallbackQuery) -> None:
    """Snooze a reminder."""
    _, _, record_id, days = cq.data.split(":")
    
    # We create a new pending reminder for +days
    fire_at = (datetime.now(timezone.utc) + timedelta(days=int(days))).isoformat() + "Z"
    
    import ulid
    async with get_db() as db:
        await db.execute(
            "INSERT INTO reminders (id, record_id, user_id, kind, fire_at, state) VALUES (?, ?, ?, ?, ?, ?)",
            (str(ulid.ULID()), record_id, cq.from_user.id, "snoozed", fire_at, "pending")
        )
        await db.commit()
        
    await cq.answer(f"Snoozed for {days} days")
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.reply(f"💤 I will remind you again in {days} days.")
