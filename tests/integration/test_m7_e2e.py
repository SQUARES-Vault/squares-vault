import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, PhotoSize, CallbackQuery

from squares_vault.bot.handlers.upload import handle_photo
from squares_vault.ai.queue import process_queue_item
from squares_vault.bot.handlers.drafts import on_draft_save, on_undo_save
from squares_vault.db.connection import get_db

@pytest.mark.asyncio
async def test_m7_e2e_flow(setup_db, monkeypatch):
    """E2E: Upload photo -> AI queue -> Draft -> Save -> Undo."""
    # 1. Setup user
    user_id = 777
    async with get_db() as db:
        await db.execute("INSERT INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()
        
    # 2. Mock Bot and Message for Photo Upload
    bot_mock = AsyncMock()
    msg_mock = AsyncMock(spec=Message)
    msg_mock.from_user = User(id=user_id, is_bot=False, first_name="Test")
    msg_mock.photo = [PhotoSize(file_id="photo_1", file_unique_id="u1", width=100, height=100)]
    msg_mock.reply = AsyncMock()
    
    await handle_photo(msg_mock)
    msg_mock.reply.assert_called_once()
    
    # Check queue
    async with get_db() as db:
        async with db.execute("SELECT id, draft_id FROM ai_queue WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            queue_id = row["id"]
            draft_id = row["draft_id"]
            
    # 3. Process Queue (Mocking Gemini API)
    from squares_vault.ai.schema import ExtractedRecord
    mock_record = ExtractedRecord(
        category="vehicle",
        title="Test RC",
        vehicle_number="MH12AB1234",
        key_date="2026-01-01",
        key_date_type="expiry"
    )
    monkeypatch.setattr("squares_vault.ai.queue.extract_document_data", AsyncMock(return_value=mock_record))
    
    bot_mock.get_file = AsyncMock()
    bot_mock.download_file = AsyncMock()
    bot_mock.download_file.return_value.getvalue = MagicMock(return_value=b"fake_image_bytes")
    
    # We skip image resizing by mocking process_image
    monkeypatch.setattr("squares_vault.ai.queue.process_image", MagicMock(return_value=b"clean_bytes"))
    
    await process_queue_item(bot_mock, queue_id, user_id, draft_id, "photo_1")
    
    bot_mock.send_message.assert_called_once()
    
    # Check draft state
    async with get_db() as db:
        async with db.execute("SELECT state FROM drafts WHERE id = ?", (draft_id,)) as cur:
            assert (await cur.fetchone())["state"] == "review"
            
    # 4. Save Draft
    cq_mock = AsyncMock(spec=CallbackQuery)
    cq_mock.from_user = User(id=user_id, is_bot=False, first_name="Test")
    cq_mock.data = f"draft:save:{draft_id}"
    cq_mock.message = MagicMock()
    cq_mock.message.edit_reply_markup = AsyncMock()
    cq_mock.message.reply = AsyncMock()
    cq_mock.answer = AsyncMock()
    
    await on_draft_save(cq_mock)
    cq_mock.answer.assert_called_once()
    
    # Check DB Records
    async with get_db() as db:
        async with db.execute("SELECT id, title FROM records WHERE user_id = ?", (user_id,)) as cur:
            record_row = await cur.fetchone()
            assert record_row is not None
            assert record_row["title"] == "Test RC"
            record_id = record_row["id"]
            
        async with db.execute("SELECT count(*) as c FROM reminders WHERE record_id = ?", (record_id,)) as cur:
            # Vehicle schedule generates 5 reminders
            assert (await cur.fetchone())["c"] == 5
            
    # 5. Undo
    cq_undo_mock = AsyncMock(spec=CallbackQuery)
    cq_undo_mock.from_user = User(id=user_id, is_bot=False, first_name="Test")
    cq_undo_mock.data = f"undo:{record_id}"
    cq_undo_mock.message = MagicMock()
    cq_undo_mock.message.edit_reply_markup = AsyncMock()
    cq_undo_mock.message.reply = AsyncMock()
    cq_undo_mock.answer = AsyncMock()
    
    await on_undo_save(cq_undo_mock)
    
    async with get_db() as db:
        async with db.execute("SELECT status FROM records WHERE id = ?", (record_id,)) as cur:
            assert (await cur.fetchone())["status"] == "soft_deleted"
