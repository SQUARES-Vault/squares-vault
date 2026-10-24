import pytest
import ulid
from unittest.mock import AsyncMock, patch
from aiogram.types import Message, CallbackQuery, User, Document, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, StorageKey

from squares_vault.db.connection import get_db
from squares_vault.bot.handlers.upload import handle_document
from squares_vault.bot.handlers.manual import on_add_manual

def _make_state(chat_id: int, user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)

@pytest.mark.asyncio
async def test_upload_supported_docx_is_accepted():
    """Verify Word document (.docx) is accepted for AI reading rather than rejected."""
    user_id = 9301
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg.document = Document(
        file_id="word_file_123",
        file_unique_id="u_w123",
        file_name="Contract_Agreement.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    msg.reply = AsyncMock()

    with patch("squares_vault.bot.handlers.upload.queue_document", new_callable=AsyncMock) as mock_queue:
        mock_queue.return_value = "queue_id_123"
        await handle_document(msg)
        mock_queue.assert_called_once()
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Upload received" in reply_text

@pytest.mark.asyncio
async def test_upload_supported_xlsx_is_accepted():
    """Verify Excel document (.xlsx) is accepted for AI reading."""
    user_id = 9302
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg.document = Document(
        file_id="excel_file_456",
        file_unique_id="u_x456",
        file_name="Monthly_Expenses.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    msg.reply = AsyncMock()

    with patch("squares_vault.bot.handlers.upload.queue_document", new_callable=AsyncMock) as mock_queue:
        mock_queue.return_value = "queue_id_456"
        await handle_document(msg)
        mock_queue.assert_called_once()
        msg.reply.assert_called_once()
        reply_text = msg.reply.call_args[0][0]
        assert "Upload received" in reply_text

@pytest.mark.asyncio
async def test_upload_unsupported_file_creates_draft_and_add_manually_button():
    """Verify an unsupported file offers an Add Manually button without requiring re-upload."""
    user_id = 9303
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg.document = Document(
        file_id="archive_file_789",
        file_unique_id="u_a789",
        file_name="documents_backup.zip",
        mime_type="application/zip"
    )
    msg.reply = AsyncMock()

    await handle_document(msg)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    reply_kw = msg.reply.call_args[1]

    assert "cannot automatically read this file type" in reply_text
    kb: InlineKeyboardMarkup = reply_kw.get("reply_markup")
    assert kb is not None
    add_manual_btn = kb.inline_keyboard[0][0]
    assert "Add Manually" in add_manual_btn.text
    assert add_manual_btn.callback_data.startswith("add_manual_fail:")

    draft_id = add_manual_btn.callback_data.split(":")[-1]

    # Verify draft saved in DB with the uploaded file_id
    async with get_db() as db:
        async with db.execute("SELECT temp_paths FROM drafts WHERE id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert "archive_file_789" in row["temp_paths"]

    # Now verify tapping the button pre-populates the attachment in manual entry
    cq = AsyncMock(spec=CallbackQuery)
    cq.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    cq.data = f"add_manual_fail:{draft_id}"
    cq.message = AsyncMock(spec=Message)
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    state = _make_state(user_id, user_id)
    await on_add_manual(cq, state)

    cq.answer.assert_called_once()
    saved_state = await state.get_data()
    # File is preserved in manual entry state!
    assert saved_state.get("draft_id") == draft_id
    assert saved_state.get("temp_paths") == "archive_file_789" or any(
        f.get("file_id") == "archive_file_789" for f in saved_state.get("all_files", [])
    )
