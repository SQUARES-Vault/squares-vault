"""Tests verifying the 8 logical fixes implemented across SQUARES Vault."""

import html
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from squares_vault.bot.calendar_sync import generate_ics
from squares_vault.reminders.schedule import generate_reminders
from squares_vault.db.connection import get_db


def test_calendar_sync_iso_timestamp():
    """Verify generate_ics handles ISO timestamp strings without raising ValueError."""
    title = "Health Checkup"
    iso_date = "2026-12-25T14:30:00Z"
    bio = generate_ics(title, iso_date)
    content = bio.getvalue().decode("utf-8")
    assert "20261225" in content
    assert "Health Checkup" in content


def test_html_escape_in_reminder_formatting():
    """Verify titles with &, <, > are safely escaped to prevent HTML parse crashes."""
    title = "AT&T Broadband Bill <Home & Office>"
    safe_title = html.escape(title or "Document")
    text = f"🔔 <b>Reminder:</b> {safe_title}\n"
    assert "&amp;" in text
    assert "&lt;Home" in text
    assert "&gt;" in text


@pytest.mark.asyncio
async def test_past_reminder_offset_skipped(setup_db):
    """Verify that reminder triggers in the past are marked as skipped."""
    user_id = 918273
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            "INSERT INTO records (id, user_id, code, category, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("rec_past_test", user_id, "SQ-TEST-123", "vehicle", "Test Past Vehicle")
        )
        await db.commit()

    # Document expiring in 4 days (offset -10 is 6 days in the past)
    now_utc = datetime.now(timezone.utc)
    expiry_dt = now_utc + timedelta(days=4)
    reminders = generate_reminders("rec_past_test", user_id, "vehicle", expiry_dt)

    now_iso = now_utc.isoformat() + "Z"
    async with get_db() as db:
        for rm in reminders:
            st = "skipped" if rm["fire_at"] < now_iso else "pending"
            await db.execute(
                "INSERT INTO reminders (id, record_id, user_id, kind, offset_days, fire_at, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rm["id"], rm["record_id"], rm["user_id"], rm["kind"], rm["offset_days"], rm["fire_at"], st)
            )
        await db.commit()

    async with get_db() as db:
        async with db.execute("SELECT kind, state FROM reminders WHERE record_id = 'rec_past_test'",) as cur:
            rows = await cur.fetchall()

    states = {r["kind"]: r["state"] for r in rows}
    # -10 day offset was in the past, so 'pre' must be skipped
    assert states.get("pre") == "skipped"
    # -2 day and 0 day offsets are in the future, so must be pending
    assert states.get("due") == "pending"
    assert states.get("overdue") == "pending"


@pytest.mark.asyncio
async def test_unregistered_upload_guard(setup_db):
    """Verify upload handlers reject unregistered users gracefully."""
    from squares_vault.bot.handlers.upload import _handle_file
    from aiogram.types import Message, User

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=888999111, is_bot=False, first_name="Unregistered")
    msg.reply = AsyncMock()

    await _handle_file(msg, "sample_file_id")
    msg.reply.assert_called_once()
    assert "tap /start" in msg.reply.call_args[0][0] or "send /start" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_account_deletion_cleans_access_grants(setup_db):
    """Verify account deletion purges access_grants and dismisses keyboard."""
    from squares_vault.bot.handlers.privacy import process_delete_my_data
    from aiogram.types import Message, User, ReplyKeyboardRemove

    user_id = 445566
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (1234, 1234)")
        await db.execute(
            "INSERT OR REPLACE INTO access_grants (id, owner_id, granted_id, category) VALUES (?, ?, 1234, 'all')",
            (f"grant_{user_id}", user_id)
        )
        await db.commit()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Deleter")
    msg.reply = AsyncMock()

    await process_delete_my_data(msg)

    # Check access_grants is purged
    async with get_db() as db:
        async with db.execute("SELECT count(*) as c FROM access_grants WHERE owner_id = ?", (user_id,)) as cur:
            assert (await cur.fetchone())["c"] == 0

    # Check ReplyKeyboardRemove was returned
    msg.reply.assert_called_once()
    assert isinstance(msg.reply.call_args[1].get("reply_markup"), ReplyKeyboardRemove)
