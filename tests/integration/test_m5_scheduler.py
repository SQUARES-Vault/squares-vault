import pytest
import asyncio
from unittest.mock import AsyncMock
from aiogram import Bot

from squares_vault.db.connection import get_db
from squares_vault.reminders.scheduler import tick

@pytest.mark.asyncio
async def test_m5_scheduler_idempotency(setup_db):
    bot = AsyncMock(spec=Bot)
    bot.send_message.return_value.message_id = 999
    
    # Insert dummy user
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (101, 101)")
        # Insert a dummy record
        await db.execute(
            "INSERT INTO records (id, user_id, category, title, code) VALUES ('rec_m5_1', 101, 'vehicle', 'Car RC', 'ABC')"
        )
        # Insert 2 pending reminders, one in the past, one in the future
        await db.execute(
            "INSERT INTO reminders (id, record_id, user_id, kind, fire_at, state) VALUES "
            "('rem_m5_1', 'rec_m5_1', 101, 'due', '2000-01-01T09:00:00Z', 'pending'), "
            "('rem_m5_2', 'rec_m5_1', 101, 'due', '2099-01-01T09:00:00Z', 'pending')"
        )
        await db.commit()
        
    # Tick 1: Should send rem1, but not rem2
    await tick(bot)
    assert bot.send_message.call_count == 1
    
    # Tick 2: Should not send anything since rem1 is 'sent'
    await tick(bot)
    assert bot.send_message.call_count == 1
    
    # Manually check DB
    async with get_db() as db:
        async with db.execute("SELECT state FROM reminders WHERE id = 'rem_m5_1'") as cur:
            assert (await cur.fetchone())["state"] == "sent"
        async with db.execute("SELECT state FROM reminders WHERE id = 'rem_m5_2'") as cur:
            assert (await cur.fetchone())["state"] == "pending"
