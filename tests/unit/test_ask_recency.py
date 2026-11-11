import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User, Chat

from squares_vault.bot.states import AskVault
from squares_vault.bot.handlers.ask import handle_ask_vault_query
from squares_vault.db.connection import get_db

@pytest.fixture
def dummy_user():
    return User(id=88001, is_bot=False, first_name="Shikhar")

@pytest.fixture
def dummy_chat():
    return Chat(id=88001, type="private")

@pytest.mark.asyncio
async def test_ask_last_5_documents_uploaded_recency(dummy_user, dummy_chat):
    """When user asks for 'last 5 documents that I uploaded', the bot must:
    1. Detect recency intent.
    2. Select the newest documents sorted by created_at DESC.
    3. Include Added/Uploaded dates in the context sent to Gemini.
    """
    user_id = 88001
    message = MagicMock(spec=Message)
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.text = "give me last 5 documents that i uploaded"
    message.bot = MagicMock()
    message.bot.send_chat_action = AsyncMock()
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        
        # Insert 7 records with distinct created_at timestamps
        records_to_insert = [
            ("rec_old_1", "Old Electric Bill 2024", "bills & subscriptions", "2024-01-10 10:00:00", "2024-01-20"),
            ("rec_old_2", "Gym Contract 2024", "health", "2024-02-10 10:00:00", "2025-02-10"),
            ("rec_recent_1", "Car Insurance 2026", "vehicle", "2026-10-01 10:00:00", "2027-10-01"),
            ("rec_recent_2", "Apartment Rent Agreement", "home", "2026-10-05 10:00:00", "2027-10-05"),
            ("rec_recent_3", "Health Policy Star", "insurance", "2026-10-10 10:00:00", "2027-10-10"),
            ("rec_recent_4", "Passport Slip", "ids & government docs", "2026-10-15 10:00:00", None),
            ("rec_recent_5", "WiFi Broadband Invoice", "bills & subscriptions", "2026-10-20 10:00:00", "2026-10-25"),
        ]
        for rid, title, cat, created_at, key_date in records_to_insert:
            await db.execute(
                """
                INSERT INTO records (id, user_id, category, title, code, created_at, key_date, key_date_type, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'expiry', 'active')
                ON CONFLICT(id) DO UPDATE SET created_at = excluded.created_at, key_date = excluded.key_date
                """,
                (rid, user_id, cat, title, f"CODE_{rid}", created_at, key_date)
            )
        await db.commit()

    with patch("squares_vault.bot.handlers.ask.answer_vault_query", new_callable=AsyncMock) as mock_qa:
        mock_qa.return_value = (
            "Here are the last 5 documents you uploaded:\n"
            "• **WiFi Broadband Invoice** (ID: rec_recent_5)\n"
            "• **Passport Slip** (ID: rec_recent_4)\n"
            "• **Health Policy Star** (ID: rec_recent_3)\n"
            "• **Apartment Rent Agreement** (ID: rec_recent_2)\n"
            "• **Car Insurance 2026** (ID: rec_recent_1)"
        )

        await handle_ask_vault_query(message, state)

        mock_qa.assert_called_once()
        call_args = mock_qa.call_args[0]
        context_sent = call_args[2]

        # Verify context includes 'Added:' / 'Uploaded:' timestamp
        assert "Added:" in context_sent or "Uploaded:" in context_sent
        # The 5 most recent uploads MUST be present in the top context
        assert "rec_recent_5" in context_sent
        assert "rec_recent_4" in context_sent
        assert "rec_recent_3" in context_sent
        assert "rec_recent_2" in context_sent
        assert "rec_recent_1" in context_sent

@pytest.mark.asyncio
async def test_ask_deep_dive_passes_full_notes(dummy_user, dummy_chat):
    """Verify notes up to 1000+ chars are not chopped at 120 chars when passed to AI."""
    user_id = 88002
    message = MagicMock(spec=Message)
    message.from_user = User(id=user_id, is_bot=False, first_name="DeepDiver")
    message.chat = Chat(id=user_id, type="private")
    message.text = "Give me a deep dive into my Oriental Car Insurance policy clauses"
    message.bot = MagicMock()
    message.bot.send_chat_action = AsyncMock()
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    long_detailed_notes = (
        "- Zero Depreciation cover included\n"
        "- Engine protector clause: active up to 5 years\n"
        "- Consumables cover: nuts, bolts, lubricants included\n"
        "- Return to Invoice (RTI) add-on selected\n"
        "- Deductible: ₹1,000 standard compulsory\n"
        "- Roadside Assistance: 24/7 towing up to 100km\n"
        "- Claim history: No Claim Bonus (NCB) at 50% discount"
    )

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, notes, status)
            VALUES ('rec_deep_1', ?, 'vehicle', 'Oriental Car Insurance', 'CODE_ORIENTAL', '2026-10-20', ?, 'active')
            ON CONFLICT(id) DO UPDATE SET notes = excluded.notes
            """,
            (user_id, long_detailed_notes)
        )
        await db.commit()

    with patch("squares_vault.bot.handlers.ask.answer_vault_query", new_callable=AsyncMock) as mock_qa:
        mock_qa.return_value = "Detailed breakdown of Oriental Car Insurance: Zero Dep, Engine protector, NCB 50%."

        await handle_ask_vault_query(message, state)

        mock_qa.assert_called_once()
        context_sent = mock_qa.call_args[0][2]

        # Crucial check: all detailed clauses must be in the context, not truncated at 120 chars!
        assert "Zero Depreciation cover" in context_sent
        assert "Engine protector clause" in context_sent
        assert "Roadside Assistance" in context_sent
        assert "No Claim Bonus (NCB) at 50%" in context_sent
