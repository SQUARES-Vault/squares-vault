import pytest
import ulid
from unittest.mock import AsyncMock
from aiogram.types import Message, CallbackQuery, User, Chat
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, StorageKey
from aiogram.exceptions import TelegramBadRequest

from squares_vault.db.connection import get_db
from squares_vault.bot.handlers.manual import (
    ManualEntry,
    EditEntry,
    on_manual_notes,
    on_man_save,
    on_edit_field_notes,
    on_save_edit_notes,
    on_clear_edit_notes,
    on_edit_cancel_field,
)

def _make_state(chat_id: int, user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)

@pytest.mark.asyncio
async def test_manual_save_with_tables_and_special_chars_no_dates():
    """Verify manual entry handles Markdown tables, unescaped characters, and no dates without crashing or hanging."""
    user_id = 9201
    state = _make_state(user_id, user_id)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    complex_notes = (
        "| Item_Name | Qty | Rate_INR |\n"
        "| :--- | :--- | :--- |\n"
        "| Premium_Widget [Model_A] | 2* | ₹1,500 |\n"
        "| Installation_Fee (Service/Repair) | 1 | ₹500 |\n"
        "| Total_Billed | — | ₹3,500 |\n\n"
        "Important note: Contact support_team@vault.internal *immediately* if defect_found."
    )

    await state.set_state(ManualEntry.notes)
    await state.update_data(
        category="bills & subscriptions",
        title="Custom_Invoice [Service_2026]",
        amount=350000,
        start_date=None,
        end_date=None,
        no_reminders=True,
    )

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg.text = complex_notes
    msg.reply = AsyncMock()

    await on_manual_notes(msg, state)
    assert await state.get_state() == "ManualEntry:confirm"

    # Confirm & Save
    cq = AsyncMock(spec=CallbackQuery)
    cq.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    cq.data = "man_save"
    cq.message = AsyncMock(spec=Message)
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    await on_man_save(cq, state)

    # State cleared
    assert await state.get_state() is None
    # Callback query MUST be answered to stop the loading spinner
    cq.answer.assert_called_once()

    # DB verified
    async with get_db() as db:
        async with db.execute("SELECT title, notes, key_date FROM records WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row["title"] == "Custom_Invoice [Service_2026]"
            assert "Premium_Widget [Model_A]" in row["notes"]
            assert row["key_date"] is None

@pytest.mark.asyncio
async def test_edit_notes_resilience_and_callbacks():
    """Verify edit notes with markdown tables, clear notes, and cancel always answer callback and update DB."""
    user_id = 9202
    state = _make_state(user_id, user_id)
    rec_id = str(ulid.ULID())

    initial_notes = "Original notes with *stars* and _underscores_"
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, notes, status) VALUES (?, ?, ?, ?, 'EDT1', NULL, NULL, ?, 'active')",
            (rec_id, user_id, "Edit_Doc [v1]", "custom", initial_notes)
        )
        await db.commit()

    await state.set_state(EditEntry.menu)
    await state.update_data(
        record_id=rec_id,
        title="Edit_Doc [v1]",
        category="custom",
        amount=None,
        key_date=None,
        notes=initial_notes,
        file_count=0
    )

    # 1. Tap "Edit Notes"
    cq_edit = AsyncMock(spec=CallbackQuery)
    cq_edit.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    cq_edit.data = "edit_field:notes"
    cq_edit.message = AsyncMock(spec=Message)
    cq_edit.message.edit_text = AsyncMock()
    cq_edit.answer = AsyncMock()

    await on_edit_field_notes(cq_edit, state)
    cq_edit.answer.assert_called_once()
    assert await state.get_state() == "EditEntry:edit_notes"

    # 2. Send new notes with a table and markdown characters
    new_table_notes = "| Col_A | Col_B |\n| --- | --- |\n| Val_1 [Tag] | Val_2* |"
    msg_save = AsyncMock(spec=Message)
    msg_save.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg_save.text = new_table_notes
    msg_save.reply = AsyncMock()

    await on_save_edit_notes(msg_save, state)
    assert await state.get_state() == "EditEntry:menu"

    async with get_db() as db:
        async with db.execute("SELECT notes FROM records WHERE id = ?", (rec_id,)) as cur:
            row = await cur.fetchone()
            assert "Val_1 [Tag]" in row["notes"]

    # 3. Clear Notes
    await state.set_state(EditEntry.edit_notes)
    cq_clear = AsyncMock(spec=CallbackQuery)
    cq_clear.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    cq_clear.data = "clear_edit_notes"
    cq_clear.message = AsyncMock(spec=Message)
    cq_clear.message.edit_text = AsyncMock()
    cq_clear.answer = AsyncMock()

    await on_clear_edit_notes(cq_clear, state)
    cq_clear.answer.assert_called_once()

    async with get_db() as db:
        async with db.execute("SELECT notes FROM records WHERE id = ?", (rec_id,)) as cur:
            row = await cur.fetchone()
            assert row["notes"] == ""

    # 4. Cancel button in edit mode
    cq_cancel = AsyncMock(spec=CallbackQuery)
    cq_cancel.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    cq_cancel.data = "edit_cancel_field"
    cq_cancel.message = AsyncMock(spec=Message)
    cq_cancel.message.edit_text = AsyncMock()
    cq_cancel.answer = AsyncMock()

    await on_edit_cancel_field(cq_cancel, state)
    cq_cancel.answer.assert_called_once()

@pytest.mark.asyncio
async def test_notes_capacity_preserves_long_notes():
    """Verify notes up to 1500 chars are preserved without being truncated to 250 chars."""
    user_id = 9203
    state = _make_state(user_id, user_id)
    rec_id = str(ulid.ULID())

    long_notes = "Line " + ("ABCDEFG1234567890 " * 50) # ~900 characters
    assert len(long_notes) > 500

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, notes, status) VALUES (?, ?, ?, ?, 'EDT2', NULL, NULL, '', 'active')",
            (rec_id, user_id, "Capacity Test", "custom")
        )
        await db.commit()

    await state.set_state(EditEntry.edit_notes)
    await state.update_data(
        record_id=rec_id,
        title="Capacity Test",
        category="custom",
        amount=None,
        key_date=None,
        file_count=0
    )

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg.text = long_notes
    msg.reply = AsyncMock()

    await on_save_edit_notes(msg, state)

    async with get_db() as db:
        async with db.execute("SELECT notes FROM records WHERE id = ?", (rec_id,)) as cur:
            row = await cur.fetchone()
            assert row is not None
            assert len(row["notes"]) > 500
            assert row["notes"] == long_notes.strip()

@pytest.mark.asyncio
async def test_save_handles_telegram_bad_request_gracefully():
    """Verify that if edit_text throws TelegramBadRequest, the fallback safely executes and cq.answer is still called."""
    user_id = 9204
    state = _make_state(user_id, user_id)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    await state.set_state(ManualEntry.confirm)
    await state.update_data(
        category="bills & subscriptions",
        title="Fallback Test",
        amount=1000,
        start_date=None,
        end_date=None,
        no_reminders=True,
        notes="Some note with problematic *markdown _formatting"
    )

    cq = AsyncMock(spec=CallbackQuery)
    cq.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    cq.data = "man_save"
    cq.message = AsyncMock(spec=Message)

    # First call with parse_mode='Markdown' raises TelegramBadRequest, second call (plain text fallback) succeeds
    bad_req = TelegramBadRequest(method=AsyncMock(), message="Bad Request: can't parse entities")
    cq.message.edit_text = AsyncMock(side_effect=[bad_req, None])
    cq.answer = AsyncMock()

    await on_man_save(cq, state)

    # cq.answer MUST be called to stop loading spinner
    cq.answer.assert_called_once()
    # edit_text was called with fallback
    assert cq.message.edit_text.call_count == 2

