import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User, Chat, CallbackQuery, Document, PhotoSize
from aiogram.fsm.context import FSMContext

from squares_vault.bot.states import AskVault
from squares_vault.bot.handlers.manual import EditEntry
from squares_vault.db.connection import get_db


@pytest.fixture
def dummy_user():
    return User(id=99001, is_bot=False, first_name="Shikhar")


@pytest.fixture
def dummy_chat():
    return Chat(id=99001, type="private")


@pytest.mark.asyncio
async def test_start_command_in_ask_chat_mode(dummy_user, dummy_chat):
    """Bug Fix #1: /start inside Ask Your Vault chat mode must not crash with ImportError."""
    from squares_vault.bot.handlers.ask import handle_ask_vault_query

    message = MagicMock(spec=Message)
    message.text = "/start"
    message.from_user = dummy_user
    message.chat = dummy_chat

    state = AsyncMock(spec=FSMContext)
    state.clear = AsyncMock()

    with patch("squares_vault.flows.onboarding.start_onboarding", AsyncMock()) as mock_start:
        await handle_ask_vault_query(message, state)
        mock_start.assert_awaited_once_with(message, state)


@pytest.mark.asyncio
async def test_search_markdown_resilience(dummy_user, dummy_chat):
    """Bug Fix #2: Search queries with underscores/asterisks must not crash Telegram Markdown parser."""
    from squares_vault.bot.handlers.search import _render_search_page, handle_search_query

    user_id = dummy_user.id
    query = "car_insurance_rc"

    # Add a mock record
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, status)
            VALUES ('rec_md_test_01', ?, 'vehicle', 'car_insurance_rc', 'CODE_MD', '2026-10-01', 'active')
            ON CONFLICT(id) DO NOTHING
            """,
            (user_id,)
        )
        await db.commit()

    with patch("squares_vault.bot.handlers.search.search_records", AsyncMock(return_value=[{"id": "rec_md_test_01", "title": "car_insurance_rc", "category": "vehicle"}])):
        text, kb = await _render_search_page(user_id, query, 0)
        assert kb is not None
        # Ensure underscores are escaped
        assert r"car\_insurance\_rc" in text

        # Test handler message reply fallback
        message = MagicMock(spec=Message)
        message.text = query
        message.from_user = dummy_user
        message.chat = dummy_chat
        message.reply = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        await handle_search_query(message, state)
        message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_browse_category_markdown_resilience(dummy_user):
    """Bug Fix #2b: Categories with underscores/symbols must not crash browse_cat."""
    from squares_vault.bot.handlers.search import on_browse_category

    user_id = dummy_user.id
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, status)
            VALUES ('rec_cat_md_01', ?, 'medical_bills_insurance', 'Hospital Bill', 'CODE_MED', '2026-10-01', 'active')
            ON CONFLICT(id) DO NOTHING
            """,
            (user_id,)
        )
        await db.commit()

    cq = MagicMock(spec=CallbackQuery)
    cq.from_user = dummy_user
    cq.data = "browse_cat:medical_bills_insurance:0"
    cq.message = MagicMock(spec=Message)
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    await on_browse_category(cq)
    cq.message.edit_text.assert_called_once()
    args, kwargs = cq.message.edit_text.call_args
    # Category display must be properly escaped
    assert r"MEDICAL\_BILLS\_INSURANCE" in args[0]


@pytest.mark.asyncio
async def test_ai_read_notification_special_characters(dummy_user):
    """Bug Fix #3: AI Read Notification with unclosed Markdown entities falls back gracefully."""
    import json
    from squares_vault.ai.queue import process_queue_item
    from aiogram.exceptions import TelegramBadRequest

    user_id = dummy_user.id
    paths_data = json.dumps({"file_id": "file_xyz", "mime": "image/jpeg"})
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO drafts (id, user_id, state, temp_paths, created_at)
            VALUES ('draft_test_spec', ?, 'reading', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET state = 'reading', temp_paths = excluded.temp_paths
            """,
            (user_id, paths_data)
        )
        await db.execute(
            """
            INSERT INTO ai_queue (id, user_id, draft_id)
            VALUES ('ai_item_test_01', ?, 'draft_test_spec')
            ON CONFLICT(id) DO NOTHING
            """,
            (user_id,)
        )
        await db.commit()

    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    msg_mock = AsyncMock(spec=Message)
    msg_mock.edit_text = AsyncMock()
    msg_mock.delete = AsyncMock()
    # First send_message returns msg_mock, second fails with TelegramBadRequest, third succeeds (fallback)
    bot.send_message = AsyncMock(side_effect=[
        msg_mock,  # initial "Scanning text..."
        TelegramBadRequest(method=MagicMock(), message="Bad Request: can't parse entities"),
        MagicMock()   # fallback plain text send
    ])

    from squares_vault.ai.schema import ExtractedRecord
    fake_record = ExtractedRecord(
        category="insurance",
        title="HDFC_Life_Plan*Policy_[123]",
        amount_minor=500000,
        key_date="2027-01-01",
        key_date_type="expiry",
        notes="Special notes with _unescaped_ text*"
    )

    with patch("squares_vault.ai.queue.extract_document_data", AsyncMock(return_value=fake_record)), \
         patch("squares_vault.ops.images.process_image", return_value=b"testbytes"), \
         patch.object(bot, "download_file", AsyncMock(return_value=MagicMock(getvalue=lambda: b"testbytes"))), \
         patch.object(bot, "get_file", AsyncMock(return_value=MagicMock(file_path="mock/path"))):

        await process_queue_item(bot, "ai_item_test_01", user_id, "draft_test_spec", "file_xyz")

        # Must have attempted Markdown first, then fallen back without crashing
        assert bot.send_message.await_count >= 2
        # Draft must NOT be marked failed
        async with get_db() as db:
            async with db.execute("SELECT state FROM drafts WHERE id = 'draft_test_spec'") as cur:
                row = await cur.fetchone()
                assert row["state"] != "failed"


@pytest.mark.asyncio
async def test_emoji_prefixed_questions_in_chat_mode(dummy_user, dummy_chat):
    """Bug Fix #4: Questions starting with emojis (e.g. 📅, 🔍) must not be intercepted as menu buttons."""
    from squares_vault.bot.handlers.ask import handle_ask_vault_query

    message = MagicMock(spec=Message)
    message.text = "📅 What is my next insurance renewal date?"
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.answer = AsyncMock()
    message.bot = MagicMock()
    message.bot.send_chat_action = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock()

    with patch("squares_vault.bot.handlers.base.handle_menu_buttons", AsyncMock()) as mock_menu, \
         patch("squares_vault.bot.handlers.ask.answer_vault_query", AsyncMock(return_value="Your next renewal is on 15 Dec 2026.")) as mock_answer:

        await handle_ask_vault_query(message, state)

        # Must NOT dispatch to menu buttons
        mock_menu.assert_not_called()
        # Must call AI assistant
        mock_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_accepted_in_ask_chat_mode(dummy_user, dummy_chat):
    """Bug Fix #5: Photos or documents uploaded during AskVault.chatting mode must be accepted."""
    from squares_vault.bot.handlers.upload import handle_photo, handle_document

    photo_msg = MagicMock(spec=Message)
    photo_msg.photo = [MagicMock(spec=PhotoSize, file_id="photo_123")]
    photo_msg.from_user = dummy_user
    photo_msg.chat = dummy_chat
    photo_msg.reply = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.clear = AsyncMock()

    with patch("squares_vault.bot.handlers.upload.queue_document", AsyncMock()) as mock_queue:
        await handle_photo(photo_msg, state)

        # State must have been cleared
        state.clear.assert_awaited_once()
        # Photo must have been queued
        mock_queue.assert_awaited_once_with(dummy_user.id, "photo_123", "photo_123", mime="image/jpeg")


@pytest.mark.asyncio
async def test_edit_attachment_supports_office_docs(dummy_user, dummy_chat):
    """Bug Fix #6: Word, Excel, CSV, and Text files must be accepted when editing an attachment."""
    from squares_vault.bot.handlers.manual import on_save_edit_doc

    doc_msg = MagicMock(spec=Message)
    doc_msg.document = MagicMock(
        spec=Document,
        file_id="word_doc_123",
        file_name="Medical_Report.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    doc_msg.from_user = dummy_user
    doc_msg.chat = dummy_chat
    doc_msg.reply = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={"record_id": "rec_edit_doc_test"})
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()

    with patch("squares_vault.bot.handlers.manual._attach_new_file_to_record", AsyncMock()) as mock_attach:
        await on_save_edit_doc(doc_msg, state)

        # Must not reject with error message
        doc_msg.reply.assert_not_called()
        # Must attach new file
        mock_attach.assert_awaited_once()
        args, kwargs = mock_attach.call_args
        assert kwargs.get("filename") == "Medical_Report.docx"
