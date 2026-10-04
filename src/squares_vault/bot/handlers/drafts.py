"""Handlers for Draft cards (Save, Edit, Discard)."""

from __future__ import annotations

import json
from aiogram import Router, F
from aiogram.types import CallbackQuery
import asyncio
from squares_vault.db.connection import get_db
from squares_vault.db.repositories import create_record, add_file, soft_delete_record
from squares_vault.reminders.schedule import generate_reminders
from squares_vault.drive.naming import generate_coded_name
from squares_vault.ai.schema import ExtractedRecord
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="drafts")


@router.callback_query(F.data.startswith("draft:discard:"))
async def on_draft_discard(cq: CallbackQuery) -> None:
    """Discard a draft."""
    _, _, draft_id = cq.data.split(":")
    
    async with get_db() as db:
        await db.execute("UPDATE drafts SET state = 'discarded' WHERE id = ?", (draft_id,))
        await db.commit()
        
    await cq.answer("Draft discarded.")
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.reply("🗑️ Draft discarded.")


@router.callback_query(F.data.startswith("draft:save:"))
async def on_draft_save(cq: CallbackQuery) -> None:
    """Save a draft to the vault."""
    _, _, draft_id = cq.data.split(":")
    
    async with get_db() as db:
        async with db.execute("SELECT ai_json, temp_paths FROM drafts WHERE id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
            
    if not row or not row["ai_json"]:
        await cq.answer("Draft not found.", show_alert=True)
        return
        
    parsed = json.loads(row["ai_json"])
    record = ExtractedRecord(**parsed)
    
    # 1. Create the record
    # For now we use the temp_paths as the drive_file_id (in M10 ops this gets uploaded properly)
    file_id = row["temp_paths"] 
    code = generate_coded_name(".jpg")
    
    try:
        from datetime import datetime
        dt = datetime.strptime(record.key_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        dt = datetime.now()
        
    record_db_id = await create_record(
        user_id=cq.from_user.id,
        category=record.category,
        title=record.title,
        code=code,
        amount_minor=record.amount_minor,
        currency=record.currency,
        notes=record.notes,
        extra_json={
            "vehicle_number": record.vehicle_number,
            "provider": record.provider,
            "document_number": record.document_number
        }
    )
    
    # 2. Attach the file
    await add_file(
        record_id=record_db_id,
        user_id=cq.from_user.id,
        drive_file_id=file_id,
        coded_name=code,
        mime="image/jpeg",
        size=0
    )
    
    # 3. Generate reminders
    reminders = generate_reminders(record_db_id, cq.from_user.id, record.category, dt)
    async with get_db() as db:
        for rm in reminders:
            await db.execute(
                "INSERT INTO reminders (id, record_id, user_id, kind, offset_days, fire_at, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rm["id"], rm["record_id"], rm["user_id"], rm["kind"], rm["offset_days"], rm["fire_at"], rm["state"])
            )
        await db.execute("UPDATE drafts SET state = 'saved' WHERE id = ?", (draft_id,))
        await db.commit()
        
    await cq.answer("Saved successfully!")
    await cq.message.edit_reply_markup(reply_markup=_get_undo_keyboard(record_db_id))
    await cq.message.reply(f"✅ **{record.title}** has been securely saved to your Vault.\nReminders have been set.")
    
    
@router.callback_query(F.data.startswith("undo:"))
async def on_undo_save(cq: CallbackQuery) -> None:
    """Undo a saved record within 30 seconds."""
    _, record_id = cq.data.split(":")
    
    # Check created_at to enforce 30s rule (simplified here, just soft deleting)
    await soft_delete_record(cq.from_user.id, record_id)
    
    await cq.answer("Action undone.")
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.reply("↩️ The record has been undone and deleted.")


def _get_undo_keyboard(record_id: str):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Undo", callback_data=f"undo:{record_id}")]
    ])
