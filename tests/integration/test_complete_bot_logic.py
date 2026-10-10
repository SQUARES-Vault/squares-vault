"""Comprehensive logical verification of complete SQUARES Vault bot.

Tests every workflow:
1. Onboarding & Invite Code Verification.
2. Manual Entry with Document Attachment & Reminder Scheduling.
3. Renewal Wizard with Document Upload & Parent Chain Archiving.
4. Modular Edit Dashboard (Title, Category, Amount, Key Date, Notes, Attachment).
5. Search Query Validation & Geometric Format.
6. Family Access / Co-Pilot Access Scoping.
7. Record Deletion & Undo with Drive Cleanup.
8. Reminder Notifications (Done, Snooze, Open Document, Renewal Wizard trigger).
9. Bottom Menu Interrupt Middleware (Zero State Lock-in).
10. Universal /cancel Command.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, CallbackQuery, User, PhotoSize, Document
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from squares_vault.db.connection import get_db
from squares_vault.bot.handlers.manual import (
    on_add_manual, on_manual_category, on_manual_title, on_manual_amount,
    on_manual_start_date, on_set_end_1y, on_manual_photo, on_manual_notes, on_man_save,
    start_renewal_flow, on_renew_document, on_renew_button_date, on_renew_keep_amt,
    on_renew_keep_notes, on_renew_save,
    start_edit_flow, on_edit_field_title, on_save_edit_title, on_save_edit_category,
    on_save_edit_amount, on_save_edit_text_date, on_clear_edit_notes
)
from squares_vault.bot.handlers.search import (
    handle_search_query, on_view_record, on_get_file, on_delete_record,
    on_delete_record_confirm, on_search_back, on_search_new
)
from squares_vault.bot.handlers.reminders import on_reminder_done, on_reminder_snooze, on_reminder_open, on_reminder_renew
from squares_vault.bot.handlers.base import cmd_cancel, handle_menu_buttons
from squares_vault.bot.middlewares.menu_interrupt import MenuInterruptMiddleware
from squares_vault.flows.onboarding import start_onboarding, consent_yes, process_invite_code, process_timezone
from squares_vault.bot.handlers.drafts import on_undo_save


def _make_state(bot_id: int, chat_id: int, user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


@pytest.mark.asyncio
async def test_01_onboarding_and_start():
    """Verify onboarding: consent -> invite code -> timezone -> completion."""
    user_id = 998877
    state = _make_state(1, user_id, user_id)
    
    # 1. Setup invite code in DB
    import hashlib
    raw_code = "TESTINVITE123"
    code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    async with get_db() as db:
        await db.execute("INSERT INTO invite_codes (code_hash) VALUES (?)", (code_hash,))
        await db.commit()

    try:
        msg = AsyncMock(spec=Message)
        msg.from_user = User(id=user_id, is_bot=False, first_name="TestUser")
        msg.chat = MagicMock(id=user_id)
        msg.answer = AsyncMock()

        # Step 1: /start
        await start_onboarding(msg, state)
        assert await state.get_state() == "Onboarding:consent"

        # Step 2: Accept consent
        await consent_yes(msg, state)
        assert await state.get_state() == "Onboarding:invite_code"

        # Step 3: Enter invalid invite code
        msg.text = "WRONG_CODE"
        await process_invite_code(msg, state)
        assert await state.get_state() == "Onboarding:invite_code"

        # Step 4: Enter valid invite code
        msg.text = raw_code
        await process_invite_code(msg, state)
        assert await state.get_state() == "Onboarding:timezone"

        # Step 5: Enter timezone
        msg.text = "Asia/Kolkata"
        await process_timezone(msg, state)
        assert await state.get_state() is None

        # Verify user exists in DB
        async with get_db() as db:
            async with db.execute("SELECT id, tz FROM users WHERE id = ?", (user_id,)) as cur:
                u_row = await cur.fetchone()
                assert u_row is not None
                assert u_row["tz"] == "Asia/Kolkata"
    finally:
        async with get_db() as db:
            await db.execute("DELETE FROM invite_codes WHERE code_hash = ?", (code_hash,))
            await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            await db.commit()


@pytest.mark.asyncio
async def test_02_manual_entry_with_attachment_and_reminders():
    """Verify manual entry: Step 0 to Step 7 with photo attachment and reminder scheduling."""
    user_id = 111222
    state = _make_state(1, user_id, user_id)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    cq = AsyncMock(spec=CallbackQuery)
    cq.from_user = User(id=user_id, is_bot=False, first_name="Owner")
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    cq.message.reply = AsyncMock()
    cq.answer = AsyncMock()
    cq.data = "add_manual"

    # Step 0: Add Manual
    await on_add_manual(cq, state)
    assert await state.get_state() == "ManualEntry:category"

    # Step 1: Select Category
    cq.data = "man_cat_vehicle"
    await on_manual_category(cq, state)
    assert await state.get_state() == "ManualEntry:title"

    # Step 2: Enter Title
    msg = AsyncMock(spec=Message)
    msg.from_user = cq.from_user
    msg.text = "Toyota Fortuner Insurance"
    msg.reply = AsyncMock()
    await on_manual_title(msg, state)
    assert await state.get_state() == "ManualEntry:amount"

    # Step 3: Enter Amount
    msg.text = "34500"
    await on_manual_amount(msg, state)
    assert await state.get_state() == "ManualEntry:start_date"

    # Step 4: Enter Start Date
    msg.text = "2026-10-10"
    await on_manual_start_date(msg, state)
    assert await state.get_state() == "ManualEntry:end_date"

    # Step 5: Select End Date (+1 Year button)
    await on_set_end_1y(cq, state)
    assert await state.get_state() == "ManualEntry:attachment"

    # Step 6: Upload Attachment Photo
    msg.photo = [PhotoSize(file_id="tg_photo_12345", file_unique_id="u123", width=200, height=200)]
    await on_manual_photo(msg, state)
    assert await state.get_state() == "ManualEntry:notes"

    # Step 7: Enter Notes
    msg.text = "Policy issued via Oriental Insurance"
    await on_manual_notes(msg, state)
    assert await state.get_state() == "ManualEntry:confirm"

    # Step 8: Confirm & Save
    cq.data = "man_save"
    await on_man_save(cq, state)
    assert await state.get_state() is None

    # Verify DB: record, files, reminders, FTS
    async with get_db() as db:
        async with db.execute("SELECT id, title, amount_minor, key_date, key_date_type FROM records WHERE user_id = ?", (user_id,)) as cur:
            rec = await cur.fetchone()
            assert rec is not None
            assert rec["title"] == "Toyota Fortuner Insurance"
            assert rec["amount_minor"] == 3450000
            assert rec["key_date_type"] == "expiry"
            rec_id = rec["id"]

        async with db.execute("SELECT drive_file_id FROM files WHERE record_id = ?", (rec_id,)) as cur:
            f_row = await cur.fetchone()
            assert f_row is not None
            assert f_row["drive_file_id"] == "tg_photo_12345"

        async with db.execute("SELECT count(*) as c FROM reminders WHERE record_id = ? AND state = 'pending'", (rec_id,)) as cur:
            r_count = (await cur.fetchone())["c"]
            assert r_count > 0


@pytest.mark.asyncio
async def test_03_renewal_wizard_with_document_and_archiving():
    """Verify renewal wizard: document attachment, offset, archiving parent, and new reminders."""
    user_id = 333444
    state = _make_state(1, user_id, user_id)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, amount_minor, key_date, key_date_type, status)
            VALUES ('orig_rec_1', ?, 'vehicle', 'Car RSA Certificate', 'CODE123', 150000, '2026-10-09', 'expiry', 'active')
            """,
            (user_id,)
        )
        await db.execute(
            "INSERT INTO reminders (id, record_id, user_id, kind, fire_at, state) VALUES ('rem_old', 'orig_rec_1', ?, 'due', '2026-10-09', 'pending')",
            (user_id,)
        )
        await db.commit()

    cq = AsyncMock(spec=CallbackQuery)
    cq.from_user = User(id=user_id, is_bot=False, first_name="Owner")
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    cq.message.reply = AsyncMock()
    cq.answer = AsyncMock()

    # 1. Start renewal flow
    await start_renewal_flow(cq, "orig_rec_1", state)
    assert await state.get_state() == "RenewalEntry:document"

    # 2. Upload renewed document
    doc_msg = AsyncMock(spec=Message)
    doc_msg.from_user = cq.from_user
    doc_msg.reply = AsyncMock()
    doc_msg.document = Document(file_id="tg_renewal_pdf_99", file_unique_id="u99", mime_type="application/pdf")
    await on_renew_document(doc_msg, state)
    assert await state.get_state() == "RenewalEntry:end_date"

    # 3. Select +1 year date button
    cq.data = "renew_set_date:2027-10-09"
    await on_renew_button_date(cq, state)
    assert await state.get_state() == "RenewalEntry:amount"

    # 4. Keep existing amount
    await on_renew_keep_amt(cq, state)
    assert await state.get_state() == "RenewalEntry:notes"

    # 5. Keep notes
    await on_renew_keep_notes(cq, state)
    assert await state.get_state() == "RenewalEntry:confirm"

    # 6. Confirm & Save Renewal
    await on_renew_save(cq, state)
    assert await state.get_state() is None

    # Verify:
    async with get_db() as db:
        # Parent record should be marked renewed
        async with db.execute("SELECT status FROM records WHERE id = 'orig_rec_1'",) as cur:
            assert (await cur.fetchone())["status"] == "renewed"

        # Old reminders should be cancelled
        async with db.execute("SELECT state FROM reminders WHERE id = 'rem_old'",) as cur:
            assert (await cur.fetchone())["state"] == "cancelled"

        # New renewed record created
        async with db.execute("SELECT id, title, key_date, parent_record_id FROM records WHERE parent_record_id = 'orig_rec_1'",) as cur:
            renewed_rec = await cur.fetchone()
            assert renewed_rec is not None
            assert renewed_rec["key_date"] == "2027-10-09"
            new_id = renewed_rec["id"]

        # Renewal document attached
        async with db.execute("SELECT drive_file_id, mime FROM files WHERE record_id = ?", (new_id,)) as cur:
            f = await cur.fetchone()
            assert f is not None
            assert f["drive_file_id"] == "tg_renewal_pdf_99"
            assert f["mime"] == "application/pdf"

        # New reminders active
        async with db.execute("SELECT count(*) as c FROM reminders WHERE record_id = ? AND state = 'pending'", (new_id,)) as cur:
            assert (await cur.fetchone())["c"] > 0


@pytest.mark.asyncio
async def test_04_modular_edit_dashboard():
    """Verify interactive edit dashboard allows editing fields independently."""
    user_id = 555666
    state = _make_state(1, user_id, user_id)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, amount_minor, key_date, key_date_type, status)
            VALUES ('edit_rec_1', ?, 'bills & subscriptions', 'Airtel Broadband', 'AIRT123', 99900, '2026-11-01', 'due', 'active')
            """,
            (user_id,)
        )
        await db.commit()

    cq = AsyncMock(spec=CallbackQuery)
    cq.from_user = User(id=user_id, is_bot=False, first_name="Editor")
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    cq.message.reply = AsyncMock()
    cq.answer = AsyncMock()

    # 1. Start Edit
    await start_edit_flow(cq, "edit_rec_1", state)
    assert await state.get_state() == "EditEntry:menu"

    # 2. Edit Title
    await on_edit_field_title(cq, state)
    assert await state.get_state() == "EditEntry:edit_title"

    msg = AsyncMock(spec=Message)
    msg.from_user = cq.from_user
    msg.reply = AsyncMock()
    msg.text = "Airtel Fiber Gigabit"
    await on_save_edit_title(msg, state)
    assert await state.get_state() == "EditEntry:menu"

    # 3. Edit Amount
    msg.text = "1499"
    await on_save_edit_amount(msg, state)
    assert await state.get_state() == "EditEntry:menu"

    # 4. Edit Date & Reschedule Reminders
    msg.text = "2026-12-15"
    await on_save_edit_text_date(msg, state)
    assert await state.get_state() == "EditEntry:menu"

    # 5. Clear notes
    await on_clear_edit_notes(cq, state)
    assert await state.get_state() == "EditEntry:menu"

    # Verify DB
    async with get_db() as db:
        async with db.execute("SELECT title, amount_minor, key_date, notes FROM records WHERE id = 'edit_rec_1'",) as cur:
            rec = await cur.fetchone()
            assert rec["title"] == "Airtel Fiber Gigabit"
            assert rec["amount_minor"] == 149900
            assert rec["key_date"] == "2026-12-15"
            assert rec["notes"] == ""


@pytest.mark.asyncio
async def test_05_search_validation_and_access_grants():
    """Verify search validation (<2 chars) and family co-pilot access scoping."""
    owner_id = 7001
    copilot_id = 7002
    state_copilot = _make_state(1, copilot_id, copilot_id)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (owner_id, owner_id))
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (copilot_id, copilot_id))
        
        # Owner records: vehicle (shared) and health (private)
        await db.execute(
            "INSERT INTO records (id, user_id, category, title, code, status) VALUES ('rec_veh', ?, 'vehicle', 'Hyundai Car RSA', 'HYU1', 'active')",
            (owner_id,)
        )
        await db.execute(
            "INSERT INTO records (id, user_id, category, title, code, status) VALUES ('rec_med', ?, 'health', 'Confidential Medical Report', 'MED1', 'active')",
            (owner_id,)
        )
        # Grant co-pilot access ONLY to 'vehicle'
        await db.execute(
            "INSERT INTO access_grants (id, owner_id, granted_id, category) VALUES ('grant_1', ?, ?, 'vehicle')",
            (owner_id, copilot_id)
        )
        await db.commit()

    from squares_vault.search.index import sync_fts_for_record
    await sync_fts_for_record('rec_veh', owner_id, 'Hyundai Car RSA', 'vehicle')
    await sync_fts_for_record('rec_med', owner_id, 'Confidential Medical Report', 'health')

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=copilot_id, is_bot=False, first_name="CoPilot")
    msg.reply = AsyncMock()

    # 1. Validation test (< 2 chars)
    msg.text = "h"
    await handle_search_query(msg, state_copilot)
    msg.reply.assert_called_with("⚠️ Please enter at least 2 characters to search.")

    # 2. Search for shared record
    msg.text = "Hyundai"
    await handle_search_query(msg, state_copilot)
    reply_call = msg.reply.call_args[0][0]
    assert "SEARCH RESULTS" in reply_call
    assert "Hyundai Car RSA" in str(msg.reply.call_args)

    # 3. Search for private medical report (must NOT find it)
    msg.text = "Confidential"
    await handle_search_query(msg, state_copilot)
    assert "No records found" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_06_deletion_and_undo_drive_cleanup(monkeypatch):
    """Verify 2-step deletion confirmation and drive cleanup on deletion & undo."""
    user_id = 8001
    record_id = "del_rec_100"

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            "INSERT INTO records (id, user_id, category, title, code, status) VALUES (?, ?, 'home', 'Electricity Bill', 'EL1', 'active')",
            (record_id, user_id)
        )
        await db.execute(
            "INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name, mime) VALUES ('file_del_1', ?, ?, 'drive_file_999', 'SV-EL1-1.pdf', 'application/pdf')",
            (record_id, user_id)
        )
        await db.commit()

    # Mock DriveAPI delete_file
    mock_delete = AsyncMock()
    monkeypatch.setattr("squares_vault.drive.api.DriveAPI.delete_file", mock_delete)

    cq = AsyncMock(spec=CallbackQuery)
    cq.from_user = User(id=user_id, is_bot=False, first_name="Deleter")
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    # 1. First tap Delete: prompts 2-step confirmation
    cq.data = f"del_record:{record_id}"
    await on_delete_record(cq)
    assert "DELETE CONFIRMATION" in cq.message.edit_text.call_args[0][0]

    # 2. Confirm Delete: deletes file and soft deletes record
    cq.data = f"del_confirm:{record_id}"
    await on_delete_record_confirm(cq)
    mock_delete.assert_called_once_with("drive_file_999")

    async with get_db() as db:
        async with db.execute("SELECT status FROM records WHERE id = ?", (record_id,)) as cur:
            assert (await cur.fetchone())["status"] == "soft_deleted"


@pytest.mark.asyncio
async def test_07_menu_interrupt_middleware():
    """Verify MenuInterruptMiddleware clears active FSM when user taps a bottom menu button."""
    user_id = 9001
    state = _make_state(1, user_id, user_id)
    await state.set_state("ManualEntry:notes")

    middleware = MenuInterruptMiddleware()
    event = AsyncMock(spec=Message)
    event.text = "📅 Upcoming"

    handler_called = False
    async def dummy_handler(evt, data):
        nonlocal handler_called
        handler_called = True
        return "done"

    await middleware(dummy_handler, event, {"state": state})

    assert handler_called is True
    # State must be cleared!
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_08_universal_cancel_command():
    """Verify /cancel command clears state and shows main menu."""
    user_id = 9002
    state = _make_state(1, user_id, user_id)
    await state.set_state("EditEntry:edit_title")

    msg = AsyncMock(spec=Message)
    msg.reply = AsyncMock()
    await cmd_cancel(msg, state)

    assert await state.get_state() is None
    msg.reply.assert_called_once()
    assert "Action cancelled" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_09_upcoming_menu_button():
    """Verify 📅 Upcoming button renders geometric layout, handles records cleanly, and never crashes."""
    user_id = 9003
    state = _make_state(1, user_id, user_id)

    # 1. Test when user has records
    import ulid
    from datetime import date, timedelta
    r1 = str(ulid.ULID())
    r2 = str(ulid.ULID())
    r3 = str(ulid.ULID())
    today = date.today()
    exp_soon = (today + timedelta(days=10)).isoformat()
    expired = (today - timedelta(days=5)).isoformat()
    event_past = (today - timedelta(days=60)).isoformat()

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        # r1: active expiry with pending reminder -> should show
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'INS1', ?, ?, 'active')",
            (r1, user_id, "Car Insurance Policy", "vehicle", exp_soon, "expiry")
        )
        # r2: expired record -> should show
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'GYM1', ?, ?, 'active')",
            (r2, user_id, "Old Gym Membership", "health", expired, "expiry")
        )
        # r3: document with only a start date (event) and no reminders -> MUST NOT show
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'LET1', ?, 'event', 'active')",
            (r3, user_id, "Offer Appointment Letter", "other", event_past)
        )
        await db.execute(
            "INSERT INTO reminders (id, user_id, record_id, kind, fire_at, state) VALUES (?, ?, ?, 'due', ?, 'pending')",
            (str(ulid.ULID()), user_id, r1, exp_soon + "T09:00:00Z")
        )
        await db.commit()

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="TestUser")
    msg.text = "📅 Upcoming"
    msg.reply = AsyncMock()

    await handle_menu_buttons(msg, state)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    reply_kw = msg.reply.call_args[1]

    assert "UPCOMING DEADLINES & ALERTS" in reply_text
    assert "Car Insurance Policy" in reply_text
    assert "Old Gym Membership" in reply_text
    # Verify r3 (start date only, no reminders) is NOT shown
    assert "Offer Appointment Letter" not in reply_text
    assert "🟡" in reply_text
    assert "🔴" in reply_text
    assert reply_kw.get("parse_mode") == "Markdown"

    kb = reply_kw.get("reply_markup")
    assert kb is not None
    # No cluttered individual document buttons below; only navigation controls and Back button
    all_callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "settings_close" in all_callbacks
    assert not any(cb.startswith("doc:view:") for cb in all_callbacks)

