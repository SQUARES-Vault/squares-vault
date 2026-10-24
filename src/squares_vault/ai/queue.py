"""Background worker for processing AI queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ulid

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger
from squares_vault.ai.quota import check_and_increment_quota, QuotaExceededError, GlobalCeilingExceededError
from squares_vault.ai.gemini import extract_document_data, AIProviderError
from squares_vault.drive.api import DriveAPI
from squares_vault.ops.images import process_image

log = get_logger(__name__)


async def queue_document(user_id: int, file_id: str, file_unique_id: str, mime: str = "image/jpeg") -> str:
    """Add a telegram document to the AI queue."""
    draft_id = str(ulid.ULID())
    queue_id = str(ulid.ULID())
    import json
    paths_data = json.dumps({"file_id": file_id, "mime": mime})
    
    async with get_db() as db:
        # Create a draft
        await db.execute(
            "INSERT INTO drafts (id, user_id, state, temp_paths, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (draft_id, user_id, "queued", paths_data)
        )
        # Create queue item
        await db.execute(
            "INSERT INTO ai_queue (id, user_id, draft_id) VALUES (?, ?, ?)",
            (queue_id, user_id, draft_id)
        )
        await db.commit()
        
    return queue_id


async def queue_multi_document(user_id: int, files: list[dict[str, str]]) -> str:
    """Add a multi-page telegram media group to the AI queue."""
    draft_id = str(ulid.ULID())
    queue_id = str(ulid.ULID())
    import json
    paths_json = json.dumps({"files": files})
    
    async with get_db() as db:
        await db.execute(
            "INSERT INTO drafts (id, user_id, state, temp_paths, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (draft_id, user_id, "queued", paths_json)
        )
        await db.execute(
            "INSERT INTO ai_queue (id, user_id, draft_id) VALUES (?, ?, ?)",
            (queue_id, user_id, draft_id)
        )
        await db.commit()
        
    return queue_id


async def process_queue_item(bot, item_id: str, user_id: int, draft_id: str, file_id: str) -> None:
    """Process a single queue item (single or multi-page)."""
    try:
        # Check if AI pipeline is enabled by admin
        from squares_vault.bot.handlers.admin import get_system_setting
        ai_on = (await get_system_setting("ai_enabled", "1")) == "1"
        if not ai_on:
            log.warning("ai_pipeline_paused_deferring", item_id=item_id)
            async with get_db() as db:
                await db.execute(
                    "UPDATE ai_queue SET state = 'queued', next_try_at = datetime('now', '+10 minutes') WHERE id = ?",
                    (item_id,),
                )
                await db.commit()
            return

        # 1. Check quota
        await check_and_increment_quota(user_id)

        # 2. Check if multi-file album or dict metadata
        import json
        files_to_fetch = []
        try:
            meta = json.loads(file_id)
            if isinstance(meta, dict) and "files" in meta:
                files_to_fetch = meta["files"]
            elif isinstance(meta, dict) and "file_id" in meta:
                files_to_fetch = [{"file_id": meta["file_id"], "mime": meta.get("mime", "image/jpeg")}]
        except Exception:
            pass

        if not files_to_fetch:
            files_to_fetch = [{"file_id": file_id, "mime": "image/jpeg"}]

        # 3. Download files from Telegram
        from squares_vault.ops.office import is_supported_office_doc
        await bot.send_chat_action(user_id, "typing")
        page_msg = f" ({len(files_to_fetch)} pages)" if len(files_to_fetch) > 1 else ""
        msg_obj = await bot.send_message(user_id, f"🔍 *Scanning text{page_msg}...*", parse_mode="Markdown")
        
        all_bytes = []
        dominant_mime = "image/jpeg"
        for item in files_to_fetch:
            fid = item["file_id"]
            file_info = await bot.get_file(fid)
            f_bytes = await bot.download_file(file_info.file_path)
            raw_data = f_bytes.getvalue()
            fn = getattr(file_info, "file_path", "") or ""
            decl_mime = item.get("mime", "")
            
            if raw_data[:4] == b"%PDF":
                all_bytes.append(raw_data)
                item["mime"] = "application/pdf"
                dominant_mime = "application/pdf"
            elif raw_data[:8] == b"\x89PNG\r\n\x1a\n":
                all_bytes.append(raw_data)
                item["mime"] = "image/png"
                dominant_mime = "image/png"
            elif raw_data[:4] == b"RIFF" and raw_data[8:12] == b"WEBP":
                all_bytes.append(raw_data)
                item["mime"] = "image/webp"
                dominant_mime = "image/webp"
            elif raw_data[:3] == b"\xff\xd8\xff":
                item["mime"] = "image/jpeg"
                dominant_mime = "image/jpeg"
                try:
                    all_bytes.append(process_image(raw_data))
                except Exception:
                    all_bytes.append(raw_data)
            elif is_supported_office_doc(fn, decl_mime):
                all_bytes.append(raw_data)
                if fn.endswith(".docx") or "wordprocessingml" in decl_mime:
                    item["mime"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                elif fn.endswith(".xlsx") or "spreadsheetml" in decl_mime:
                    item["mime"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                elif fn.endswith(".csv") or decl_mime == "text/csv":
                    item["mime"] = "text/csv"
                else:
                    item["mime"] = "text/plain"
                dominant_mime = item["mime"]
            else:
                item["mime"] = decl_mime or "image/jpeg"
                try:
                    all_bytes.append(process_image(raw_data))
                except Exception:
                    all_bytes.append(raw_data)

        # 4. Send to Gemini
        if msg_obj:
            await msg_obj.edit_text("🧠 *Categorizing...*", parse_mode="Markdown")
        
        record = await extract_document_data(user_id, all_bytes if len(all_bytes) > 1 else all_bytes[0], dominant_mime)

        if msg_obj:
            await msg_obj.delete()

        # 5. Save to draft
        new_temp_paths = json.dumps({"files": files_to_fetch, "mime": dominant_mime})
        async with get_db() as db:
            await db.execute(
                "UPDATE drafts SET state = 'review', ai_json = ?, temp_paths = ? WHERE id = ?",
                (record.model_dump_json(), new_temp_paths, draft_id)
            )
            await db.execute("DELETE FROM ai_queue WHERE id = ?", (item_id,))
            await db.commit()

        # 6. Notify user with full extracted data
        kb = _get_draft_keyboard(draft_id)
        amt = f"\nAmount: ₹{record.amount_minor/100:.2f}" if record.amount_minor else ""
        dt = f"\nDate: {record.key_date}" if record.key_date else ""
        notes_preview = f"\n\n📝 {record.notes[:200]}" if record.notes else ""
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ *AI Read Complete!*\n\n"
                f"📄 *{record.title}*\n"
                f"Category: {record.category}"
                f"{amt}{dt}"
                f"{notes_preview}\n\n"
                f"Review this draft before saving."
            ),
            reply_markup=kb,
            parse_mode="Markdown"
        )
        
    except (QuotaExceededError, GlobalCeilingExceededError) as e:
        log.warning("quota_exceeded_deferred", error=str(e))
        # Defer to tomorrow
        async with get_db() as db:
            await db.execute("UPDATE ai_queue SET attempts = attempts + 1, next_try_at = datetime('now', '+1 day') WHERE id = ?", (item_id,))
            await db.commit()
        await bot.send_message(user_id, f"⏳ {str(e)} Your document is queued for tomorrow.")
        
    except Exception as e:
        log.exception("ai_processing_failed")
        async with get_db() as db:
            await db.execute("UPDATE ai_queue SET state = 'failed', last_error = ? WHERE id = ?", (str(e), item_id))
            await db.commit()
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Add Manually", callback_data=f"add_manual_fail:{draft_id}")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")]
        ])
        await bot.send_message(user_id, "❌ Sorry, I failed to read that document.", reply_markup=kb)


def _get_draft_keyboard(draft_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Save to Vault", callback_data=f"draft:save:{draft_id}")],
        [InlineKeyboardButton(text="✏️ Edit manually", callback_data=f"draft:edit:{draft_id}")],
        [InlineKeyboardButton(text="🗑️ Discard", callback_data=f"draft:discard:{draft_id}")]
    ])


async def ai_worker_loop(bot) -> None:
    """Background loop to process AI queue."""
    log.info("ai_worker_started")
    while True:
        try:
            async with get_db() as db:
                # Find pending items
                async with db.execute(
                    "SELECT q.id, q.user_id, q.draft_id, d.temp_paths "
                    "FROM ai_queue q JOIN drafts d ON q.draft_id = d.id "
                    "WHERE q.state = 'queued' AND (q.next_try_at IS NULL OR q.next_try_at <= datetime('now'))"
                ) as cur:
                    rows = await cur.fetchall()
                    
            for row in rows:
                # Basic lock
                async with get_db() as db:
                    await db.execute("UPDATE ai_queue SET state = 'processing' WHERE id = ?", (row["id"],))
                    await db.commit()
                    
                await process_queue_item(bot, row["id"], row["user_id"], row["draft_id"], row["temp_paths"])
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("ai_worker_error", error=str(e))
            
        await asyncio.sleep(5)
