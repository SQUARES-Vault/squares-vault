import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User, Chat

from squares_vault.bot.handlers.ask import (
    handle_ask_vault_query,
    _compute_vault_blindspots,
)
from squares_vault.db.connection import get_db

@pytest.fixture
def dummy_user():
    return User(id=88006, is_bot=False, first_name="Shikhar")

@pytest.fixture
def dummy_chat():
    return Chat(id=88006, type="private")

def test_compute_vault_blindspots_vehicle_missing_puc():
    """If a vehicle has RC and Insurance but no PUC, it must be flagged as a blindspot."""
    records = [
        {
            "id": "rec_v1",
            "title": "Hyundai Venue RC UP15EB3113",
            "category": "vehicle",
            "key_date": "2035-10-10",
            "notes": "Vehicle Registration Certificate",
            "extra_json": '{"vehicle_number": "UP15EB3113"}',
            "_extra": {"vehicle_number": "UP15EB3113"},
            "vehicle_number": "UP15EB3113",
        },
        {
            "id": "rec_v2",
            "title": "Venue Car Insurance Policy 2026",
            "category": "vehicle",
            "key_date": "2027-10-01",
            "notes": "Comprehensive Insurance UP15EB3113",
            "extra_json": '{"vehicle_number": "UP15EB3113"}',
            "_extra": {"vehicle_number": "UP15EB3113"},
            "vehicle_number": "UP15EB3113",
        },
    ]

    blindspots = _compute_vault_blindspots(records)
    assert len(blindspots) >= 1
    assert any("UP15EB3113" in b and "PUC" in b for b in blindspots)


def test_compute_vault_blindspots_passport_6_month_rule():
    """If a passport expires within 6 months, flag international travel restriction."""
    records = [
        {
            "id": "rec_pass_1",
            "title": "Passport Shikhar",
            "category": "ids & government docs",
            "key_date": "2027-01-15",  # Close expiry
            "notes": "Republic of India Passport",
            "extra_json": "{}",
            "_extra": {},
            "vehicle_number": None,
        }
    ]

    blindspots = _compute_vault_blindspots(records)
    assert any("Passport" in b and "6 months" in b for b in blindspots)


@pytest.mark.asyncio
async def test_ask_vault_includes_blindspot_in_context(dummy_user, dummy_chat):
    """When user asks about their vehicle, the relevant blindspot must be passed in context to Gemini."""
    user_id = dummy_user.id
    message = MagicMock(spec=Message)
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.text = "When does my Venue insurance expire?"
    message.bot = MagicMock()
    message.bot.send_chat_action = AsyncMock()
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, key_date, extra_json, status)
            VALUES ('rec_venue_ins', ?, 'vehicle', 'Hyundai Venue Car Insurance', 'CODE_V', '2026-10-01', '2027-10-15', '{"vehicle_number": "UP15EB3113"}', 'active')
            ON CONFLICT(id) DO UPDATE SET title = excluded.title
            """,
            (user_id,)
        )
        await db.commit()

    with patch("squares_vault.bot.handlers.ask.answer_vault_query", new_callable=AsyncMock) as mock_qa:
        mock_qa.return_value = (
            "Your Hyundai Venue Car Insurance expires on 15 Oct 2027.\n\n"
            "💡 Vault Insight: You have active Insurance for UP15EB3113, but no active PUC (Pollution Certificate) on file."
        )

        await handle_ask_vault_query(message, state)

        mock_qa.assert_called_once()
        context_sent = mock_qa.call_args[0][2]

        # Verify blindspot section is present in context
        assert "BLINDSPOT" in context_sent.upper()
        assert "UP15EB3113" in context_sent or "Venue" in context_sent
