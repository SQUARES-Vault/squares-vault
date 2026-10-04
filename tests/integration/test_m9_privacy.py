import pytest
from squares_vault.db.connection import get_db

@pytest.mark.asyncio
async def test_m9_deletemydata(setup_db, monkeypatch):
    """Test full deletion leaves 0 rows and 1 tombstone."""
    user_id = 109
    
    # Insert a lot of stuff
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute("INSERT INTO oauth_tokens (user_id, refresh_token_enc, dek_wrapped) VALUES (?, ?, ?)", (user_id, b"xx", b"yy"))
        await db.execute("INSERT INTO records (id, user_id, category, title, code) VALUES ('rec_m9_1', ?, 'vehicle', 'Car', 'M9A')", (user_id,))
        await db.execute("INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name) VALUES ('file_m9_1', 'rec_m9_1', ?, 'xyz', 'abc')", (user_id,))
        await db.execute("INSERT INTO reminders (id, record_id, user_id, kind, fire_at) VALUES ('rem_m9_1', 'rec_m9_1', ?, 'due', '2026-01-01')", (user_id,))
        await db.execute("INSERT INTO ai_usage (user_id, day, count) VALUES (?, '2026-01-01', 5)", (user_id,))
        await db.commit()
        
    # Simulate handler logic
    from aiogram.types import Message, User
    from unittest.mock import AsyncMock
    from squares_vault.bot.handlers.privacy import process_delete_my_data
    
    msg_mock = AsyncMock(spec=Message)
    msg_mock.from_user = User(id=user_id, is_bot=False, first_name="Test")
    msg_mock.reply = AsyncMock()
    
    await process_delete_my_data(msg_mock)
    
    msg_mock.reply.assert_called_once()
    
    # Assert 0 rows
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) as c FROM users WHERE id = ?", (user_id,)) as cur:
            assert (await cur.fetchone())["c"] == 0
        async with db.execute("SELECT COUNT(*) as c FROM records WHERE user_id = ?", (user_id,)) as cur:
            assert (await cur.fetchone())["c"] == 0
            
        # Assert tombstone
        async with db.execute("SELECT COUNT(*) as c FROM tombstones") as cur:
            assert (await cur.fetchone())["c"] == 1
