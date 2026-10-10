"""Handlers for Draft cards (Save, Edit, Discard)."""

from __future__ import annotations

import json
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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
    # temp_paths holds JSON with file_id/mime or files list
    files_to_attach = []
    try:
        temp_data = json.loads(row["temp_paths"])
        if "files" in temp_data:
            files_to_attach = temp_data["files"]
        elif "file_id" in temp_data:
            files_to_attach = [{"file_id": temp_data["file_id"], "mime": temp_data.get("mime", "image/jpeg")}]
        else:
            files_to_attach = [{"file_id": row["temp_paths"], "mime": "image/jpeg"}]
    except Exception:
        files_to_attach = [{"file_id": row["temp_paths"], "mime": "image/jpeg"}]

    primary_mime = files_to_attach[0].get("mime", "image/jpeg") if files_to_attach else "image/jpeg"
    ext = ".pdf" if "pdf" in primary_mime else (".png" if "png" in primary_mime else ".jpg")
    code = generate_coded_name(ext)
    
    dt = None
    if record.key_date:
        try:
            from datetime import datetime
            dt = datetime.strptime(record.key_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            pass
        
    # Determine if it's an expiry/due date that warrants reminders
    valid_reminder_types = {"expiry", "due", "renewal", "end"}
    is_expiry = record.key_date_type and record.key_date_type.lower() in valid_reminder_types
    
    record_db_id = await create_record(
        user_id=cq.from_user.id,
        category=record.category,
        title=record.title,
        code=code,
        amount_minor=record.amount_minor,
        currency=record.currency,
        notes=record.notes,
        key_date=record.key_date if dt else None,
        key_date_type=record.key_date_type if dt else None,
        extra_json={
            "entity_name": record.entity_name,
            "vehicle_number": record.vehicle_number,
            "provider": record.provider,
            "document_number": record.document_number
        }
    )
    
    # 2. Attach all files (pages/images)
    for idx, item in enumerate(files_to_attach):
        f_id = item["file_id"]
        f_mime = item.get("mime", primary_mime)
        f_ext = ".pdf" if "pdf" in f_mime else (".png" if "png" in f_mime else ".jpg")
        f_code = code if idx == 0 else generate_coded_name(f_ext)
        await add_file(
            record_id=record_db_id,
            user_id=cq.from_user.id,
            drive_file_id=f_id,
            coded_name=f_code,
            mime=f_mime,
            size=0
        )
    
    # 3. Generate reminders ONLY if it's an expiry or due date
    reminders_set = False
    if dt and is_expiry:
        reminders = generate_reminders(record_db_id, cq.from_user.id, record.category, dt)
        async with get_db() as db:
            for rm in reminders:
                await db.execute(
                    "INSERT INTO reminders (id, record_id, user_id, kind, offset_days, fire_at, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rm["id"], rm["record_id"], rm["user_id"], rm["kind"], rm["offset_days"], rm["fire_at"], rm["state"])
                )
            await db.commit()
        reminders_set = True
            
    async with get_db() as db:
        await db.execute("UPDATE drafts SET state = 'saved' WHERE id = ?", (draft_id,))
        await db.commit()
        
    await cq.answer("Saved successfully!")
    await cq.message.edit_reply_markup(reply_markup=_get_undo_keyboard(record_db_id))
    
    msg = f"✅ **{record.title}** has been securely saved to your Vault."
    if reminders_set:
        msg += "\nReminders have been set."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📅 Add to Phone Calendar", callback_data=f"send_ics:{record_db_id}")]])
        await cq.message.reply(msg, reply_markup=kb, parse_mode="Markdown")
    else:
        await cq.message.reply(msg, parse_mode="Markdown")
    
    
@router.callback_query(F.data.startswith("undo:"))
async def on_undo_save(cq: CallbackQuery) -> None:
    """Undo a saved record within 30 seconds."""
    _, record_id = cq.data.split(":")
    
    # Fetch drive file IDs to delete from Google Drive
    async with get_db() as db:
        async with db.execute("SELECT drive_file_id FROM files WHERE record_id = ? AND user_id = ?", (record_id, cq.from_user.id)) as cur:
            files = await cur.fetchall()
            
    if files:
        from squares_vault.drive.api import DriveAPI
        drive = DriveAPI(cq.from_user.id)
        for f in files:
            try:
                await drive.delete_file(f["drive_file_id"])
            except Exception as e:
                log.warning(f"Failed to delete file from drive on undo: {e}")

    await soft_delete_record(cq.from_user.id, record_id)
    
    await cq.answer("Action undone.")
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.reply("↩️ The record has been undone and deleted.")


def _get_undo_keyboard(record_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Undo", callback_data=f"undo:{record_id}")]
    ])
