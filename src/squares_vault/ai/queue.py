"""Background worker for processing AI queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ulid

from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger
from squares_vault.ai.quota import check_and_increment_quota, QuotaExceededError, GlobalCeilingExceededError
from squares_vault.ai.gemini import extract_document_data, AIProviderError
from squares_vault.drive.api import DriveAPI

log = get_logger(__name__)


async def queue_document(user_id: int, file_id: str, file_unique_id: str) -> str:
    """Add a telegram document to the AI queue."""
    draft_id = str(ulid.ULID())
    queue_id = str(ulid.ULID())
    
    async with get_db() as db:
        # Create a draft
        await db.execute(
            "INSERT INTO drafts (id, user_id, state, temp_paths, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (draft_id, user_id, "queued", file_id) # Storing telegram file_id in temp_paths for now
        )
        # Create queue item
        await db.execute(
            "INSERT INTO ai_queue (id, user_id, draft_id) VALUES (?, ?, ?)",
            (queue_id, user_id, draft_id)
        )
        await db.commit()
        
    return queue_id


async def process_queue_item(bot, item_id: str, user_id: int, draft_id: str, file_id: str) -> None:
    """Process a single queue item."""
    try:
        # 1. Check quota
        await check_and_increment_quota(user_id)
        
        # 2. Download file
        file_info = await bot.get_file(file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        img_data = file_bytes.getvalue()
        
        # 3. Process with Gemini
        # Assuming it's an image for now. If PDF, we'd use PyMuPDF.
        from squares_vault.ops.images import process_image
        try:
            clean_image = process_image(img_data)
            mime = "image/jpeg"
        except Exception:
            # Maybe it's a PDF
            clean_image = img_data
            mime = "application/pdf"
            
        record = await extract_document_data(user_id, clean_image, mime)
        
        # 4. Save to draft
        async with get_db() as db:
            await db.execute(
                "UPDATE drafts SET state = 'review', ai_json = ? WHERE id = ?",
                (record.model_dump_json(), draft_id)
            )
            await db.execute("DELETE FROM ai_queue WHERE id = ?", (item_id,))
            await db.commit()
            
        # 5. Notify user
        kb = _get_draft_keyboard(draft_id)
        await bot.send_message(
            chat_id=user_id,
            text=f"📄 **{record.title}**\n\nCategory: {record.category}\nDate: {record.key_date}\n\nReview this draft before saving.",
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
        await bot.send_message(user_id, "❌ Sorry, I failed to read that document. You can try manual entry.")


def _get_draft_keyboard(draft_id: str):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
