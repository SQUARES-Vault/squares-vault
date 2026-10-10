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


from aiogram.fsm.context import FSMContext


@router.callback_query(F.data.startswith("rem:rnw:"))
async def on_reminder_renew(cq: CallbackQuery, state: FSMContext) -> None:
    """Start full renewal wizard directly from reminder notification."""
    _, _, record_id = cq.data.split(":")
    from squares_vault.bot.handlers.manual import start_renewal_flow
    await start_renewal_flow(cq, record_id, state)


@router.callback_query(F.data.startswith("rem:opn:"))
async def on_reminder_open(cq: CallbackQuery, state: FSMContext = None) -> None:
    """Open document details directly from reminder notification."""
    _, _, record_id = cq.data.split(":")
    cq.data = f"view_record:{record_id}"
    from squares_vault.bot.handlers.search import on_view_record
    await on_view_record(cq, state)


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


@router.callback_query(F.data.startswith("send_ics:"))
async def on_send_ics(cq: CallbackQuery) -> None:
    record_id = cq.data.split(":")[1]
    
    from squares_vault.db.connection import get_db
    async with get_db() as db:
        async with db.execute("SELECT title, key_date, notes FROM records WHERE id = ?", (record_id,)) as cur:
            row = await cur.fetchone()
            
    if not row or not row["key_date"]:
        await cq.answer("Date not available.")
        return
        
    from squares_vault.bot.calendar_sync import generate_ics
    from aiogram.types import BufferedInputFile
    
    ics_bio = generate_ics(row["title"], row["key_date"], row["notes"] or "")
    safe_title = "".join(c for c in row["title"] if c.isalnum() or c in " _-").strip() or "Event"
    
    await cq.message.answer_document(BufferedInputFile(ics_bio.getvalue(), filename=f"{safe_title}.ics"), caption="Tap the file above to add to your calendar.")
    await cq.answer()


@router.callback_query(F.data.startswith("rem:cancel:"))
async def on_reminder_cancel(cq: CallbackQuery) -> None:
    """Cancel all pending reminders for this document."""
    _, _, record_id = cq.data.split(":")
    async with get_db() as db:
        await db.execute(
            "UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'",
            (record_id, cq.from_user.id)
        )
        await db.commit()
    await cq.answer("🔕 All future reminders for this document have been cancelled.", show_alert=True)
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.reply("🔕 All future reminders for this document have been cancelled.")

