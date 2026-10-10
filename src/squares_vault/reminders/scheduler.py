"""Background scheduler loop for reminders."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError

from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger
from squares_vault.i18n.loader import t
from squares_vault.bot.keyboards.reminders import get_reminder_keyboard

log = get_logger(__name__)


async def tick(bot: Bot) -> None:
    """One tick of the reminder scheduler (runs every 60s)."""
    now = datetime.now(timezone.utc).isoformat()
    
    async with get_db() as db:
        # Find pending reminders due now or earlier
        async with db.execute(
            "SELECT r.id, r.user_id, r.kind, r.record_id, rec.title "
            "FROM reminders r "
            "JOIN records rec ON r.record_id = rec.id "
            "WHERE r.state = 'pending' AND r.fire_at <= ?",
            (now,)
        ) as cur:
            due = await cur.fetchall()
            
        for row in due:
            # Claim before send
            async with db.execute(
                "UPDATE reminders SET state = 'sending' WHERE id = ? AND state = 'pending'",
                (row["id"],)
            ) as cur2:
                if cur2.rowcount != 1:
                    continue  # Someone else claimed it
            await db.commit()
            
            try:
                # Send
                if row["kind"] == "pre":
                    alert_name = "Early Alert (10 Days Left)"
                elif row["kind"] == "due":
                    alert_name = "Due Date Alert (2 Days Left)"
                elif row["kind"] == "overdue":
                    alert_name = "Overdue Alert (Expires Today)"
                else:
                    alert_name = "Reminder"
                text = f"🔔 <b>{alert_name}:</b> {row['title']}\n"
                kb = get_reminder_keyboard(row["record_id"])
                
                msg = await bot.send_message(
                    chat_id=row["user_id"],
                    text=text,
                    reply_markup=kb
                )
                
                # Mark done
                await db.execute(
                    "UPDATE reminders SET state = 'sent', sent_message_id = ? WHERE id = ?",
                    (msg.message_id, row["id"])
                )
                await db.commit()
                
            except TelegramRetryAfter as e:
                # 429
                await asyncio.sleep(e.retry_after)
                await db.execute("UPDATE reminders SET state = 'pending' WHERE id = ?", (row["id"],))
                await db.commit()
                
            except TelegramForbiddenError:
                # 403 (blocked)
                await db.execute("UPDATE users SET status = 'paused' WHERE id = ?", (row["user_id"],))
                await db.execute("UPDATE reminders SET state = 'pending' WHERE id = ?", (row["id"],))
                await db.commit()
                
            except Exception as e:
                log.error("reminder_send_failed", reminder_id=row["id"], error=str(e))
                await db.execute(
                    "UPDATE reminders SET state = 'failed', last_error = ?, attempts = attempts + 1 WHERE id = ?",
                    (str(e), row["id"])
                )
                await db.commit()


async def scheduler_loop(bot: Bot) -> None:
    """Infinite loop for the scheduler."""
    log.info("scheduler_started")
    while True:
        try:
            await tick(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("scheduler_tick_error", error=str(e))
        await asyncio.sleep(60)
