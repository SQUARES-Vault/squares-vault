import pytest
import ulid
from datetime import date, timedelta
from unittest.mock import AsyncMock
from aiogram.types import Message, User
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, StorageKey

from squares_vault.db.connection import get_db
from squares_vault.bot.handlers.base import handle_menu_buttons, _render_upcoming

def _make_state(chat_id: int, user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=chat_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)

@pytest.mark.asyncio
async def test_upcoming_excludes_non_expiry_and_no_reminders():
    """Documents with no end/expiry date and no pending reminders MUST NOT appear in Upcoming,
    even if key_date falls within the 3-month window."""
    user_id = 9101
    today = date.today()
    recent_date = (today + timedelta(days=2)).isoformat()
    expiry_date = (today + timedelta(days=15)).isoformat()

    r_expiry = str(ulid.ULID())
    r_purchase = str(ulid.ULID())
    r_appointment = str(ulid.ULID())

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        
        # 1. Document with expiry date -> SHOULD appear
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'EXP1', ?, 'expiry', 'active')",
            (r_expiry, user_id, "Passport Renewal Document", "ids & government docs", expiry_date)
        )
        
        # 2. Document with a purchase/issue date within the window but key_date_type='purchase', no reminders -> MUST NOT appear
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'PUR1', ?, 'purchase', 'active')",
            (r_purchase, user_id, "Cash Memo Groceries", "bills & subscriptions", recent_date)
        )

        # 3. Document with appointment/event date but key_date_type='event', no reminders -> MUST NOT appear
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'EVT1', ?, 'event', 'active')",
            (r_appointment, user_id, "Passport Appointment Slot", "ids & government docs", recent_date)
        )
        await db.commit()

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Test")
    msg.reply = AsyncMock()

    await _render_upcoming(msg, user_id, months_limit=3, edit=False)

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]

    # Expiry record MUST be visible
    assert "Passport Renewal" in reply_text
    # Non-expiry records without reminders MUST NOT be visible
    assert "Cash Memo Groceries" not in reply_text
    assert "Passport Appointment Slot" not in reply_text
    assert "Date: —" not in reply_text
    assert "Alerts: _None pending_" not in reply_text

@pytest.mark.asyncio
async def test_upcoming_shows_non_expiry_only_if_pending_reminder_exists():
    """If a document has a pending reminder, it SHOULD appear under its alert date,
    even if key_date_type is not 'expiry'."""
    user_id = 9102
    today = date.today()
    rem_date = (today + timedelta(days=5)).isoformat()

    r_event_with_rem = str(ulid.ULID())
    rem_id = str(ulid.ULID())

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'EVR1', ?, 'event', 'active')",
            (r_event_with_rem, user_id, "Doctor Health Checkup", "health", (today + timedelta(days=5)).isoformat())
        )
        await db.execute(
            "INSERT INTO reminders (id, user_id, record_id, kind, fire_at, state) VALUES (?, ?, ?, 'due', ?, 'pending')",
            (rem_id, user_id, r_event_with_rem, rem_date + "T10:00:00Z")
        )
        await db.commit()

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Test")
    msg.reply = AsyncMock()

    await _render_upcoming(msg, user_id, months_limit=3, edit=False)

    reply_text = msg.reply.call_args[0][0]
    assert "Doctor Health Checkup" in reply_text
    assert "Next Alert" in reply_text

@pytest.mark.asyncio
async def test_upcoming_all_non_expiry_shows_empty_state():
    """If user only has non-expiry records and no reminders, empty state message is rendered."""
    user_id = 9103
    today = date.today()

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            "INSERT INTO records (id, user_id, title, category, code, key_date, key_date_type, status) VALUES (?, ?, ?, ?, 'MEM1', ?, 'purchase', 'active')",
            (str(ulid.ULID()), user_id, "Only A Cash Memo", "bills & subscriptions", today.isoformat())
        )
        await db.commit()

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Test")
    msg.reply = AsyncMock()

    await _render_upcoming(msg, user_id, months_limit=3, edit=False)

    reply_text = msg.reply.call_args[0][0]
    assert "No deadlines or reminders found" in reply_text
    assert "Only A Cash Memo" not in reply_text
