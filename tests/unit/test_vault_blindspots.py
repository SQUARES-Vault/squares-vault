import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import Message, User, Chat
from aiogram.fsm.context import FSMContext

from squares_vault.bot.handlers.ask import _compute_vault_blindspots, handle_ask_vault_query
from squares_vault.db.connection import get_db

@pytest.fixture
def dummy_user():
    return User(id=88006, is_bot=False, first_name="Shikhar")

@pytest.fixture
def dummy_chat():
    return Chat(id=88006, type="private")

def test_compute_vault_blindspots_vehicle_missing_puc():
    """If a vehicle has RC and Insurance but no active PUC, flag the missing PUC."""
    records = [
        {
            "id": "rec_v1",
            "title": "Hyundai Venue RC Smart Card",
            "category": "vehicle",
            "vehicle_number": "UP15EB3113",
            "entity_name": "Hyundai Venue",
            "status": "active",
            "notes": "Registration certificate"
        },
        {
            "id": "rec_v2",
            "title": "Hyundai Venue Comprehensive Insurance",
            "category": "vehicle",
            "vehicle_number": "UP15EB3113",
            "entity_name": "Hyundai Venue",
            "status": "active",
            "key_date": "2027-10-15",
            "key_date_type": "expiry",
            "notes": "Policy active"
        }
    ]

    blindspots = _compute_vault_blindspots(records)
    assert len(blindspots) >= 1
    bs_text = " ".join(blindspots)
    assert "UP15EB3113" in bs_text or "Hyundai Venue" in bs_text
    assert "PUC" in bs_text or "Pollution" in bs_text


def test_compute_vault_blindspots_passport_near_expiry():
    """If a passport has less than 6 months validity, flag international travel restriction."""
    near_date = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
    records = [
        {
            "id": "rec_pass",
            "title": "Indian Passport - Shikhar",
            "category": "ids & government docs",
            "status": "active",
            "key_date": near_date,
            "key_date_type": "expiry",
            "notes": "Passport booklet"
        }
    ]

    blindspots = _compute_vault_blindspots(records)
    assert len(blindspots) >= 1
    bs_text = " ".join(blindspots)
    assert "Passport" in bs_text
    assert "6 months" in bs_text or "travel" in bs_text


@pytest.mark.asyncio
async def test_ask_vault_feeds_blindspots_to_gemini_context(dummy_user, dummy_chat):
    """When asking about a vehicle, Gemini's context must receive the entity blindspot."""
    user_id = dummy_user.id
    message = MagicMock(spec=Message)
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.text = "When is my Venue car insurance expiring?"
    message.bot = MagicMock()
    message.bot.send_chat_action = AsyncMock()
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, key_date, key_date_type, status, extra_json)
            VALUES ('rec_venue_ins', ?, 'vehicle', 'Hyundai Venue Insurance', 'CODE_V_INS', '2026-10-01', '2027-10-15', 'expiry', 'active', '{"vehicle_number": "UP15EB3113", "entity_name": "Hyundai Venue"}')
            ON CONFLICT(id) DO UPDATE SET title = excluded.title
            """,
            (user_id,)
        )
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, key_date, key_date_type, status, extra_json)
            VALUES ('rec_venue_rc', ?, 'vehicle', 'Hyundai Venue RC', 'CODE_V_RC', '2026-10-01', '2035-10-15', 'expiry', 'active', '{"vehicle_number": "UP15EB3113", "entity_name": "Hyundai Venue"}')
            ON CONFLICT(id) DO UPDATE SET title = excluded.title
            """,
            (user_id,)
        )
        await db.commit()

    with patch("squares_vault.bot.handlers.ask.answer_vault_query", new_callable=AsyncMock) as mock_qa:
        mock_qa.return_value = (
            "Your Hyundai Venue insurance expires on 15 Oct 2027 (ID: rec_venue_ins).\n\n"
            "💡 Vault Insight: Your vehicle UP15EB3113 has active Insurance and RC, but no active Pollution Certificate (PUC) on file."
        )

        await handle_ask_vault_query(message, state)

        mock_qa.assert_called_once()
        context_sent = mock_qa.call_args[0][2]

        assert "VAULT HEALTH & BLINDSPOTS" in context_sent
        assert "PUC" in context_sent or "Pollution" in context_sent
