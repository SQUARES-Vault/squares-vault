import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import CallbackQuery, User, Message, Chat

from squares_vault.bot.handlers.search import (
    get_document_lineage,
    render_version_timeline_text,
    on_view_timeline,
)
from squares_vault.db.connection import get_db

@pytest.fixture
def dummy_user():
    return User(id=88004, is_bot=False, first_name="Shikhar")

@pytest.fixture
def dummy_chat():
    return Chat(id=88004, type="private")

@pytest.mark.asyncio
async def test_version_chain_traversal_multi_year(dummy_user):
    """Test recursive traversal of a 3-year renewal chain: 2024 -> 2025 -> 2026."""
    user_id = dummy_user.id

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        
        # 2024 root
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, key_date, amount_minor, status)
            VALUES ('rec_yr_2024', ?, 'vehicle', 'Car Policy 2024', 'CODE_24', '2024-10-01 10:00:00', '2024-10-01', 1420000, 'expired')
            ON CONFLICT(id) DO UPDATE SET title = excluded.title
            """,
            (user_id,)
        )
        # 2025 middle (renewed from 2024)
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, key_date, amount_minor, parent_record_id, status)
            VALUES ('rec_yr_2025', ?, 'vehicle', 'Car Policy 2025', 'CODE_25', '2025-10-01 10:00:00', '2025-10-01', 1550000, 'rec_yr_2024', 'expired')
            ON CONFLICT(id) DO UPDATE SET title = excluded.title, parent_record_id = excluded.parent_record_id
            """,
            (user_id,)
        )
        # 2026 latest (renewed from 2025)
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, key_date, amount_minor, parent_record_id, status)
            VALUES ('rec_yr_2026', ?, 'vehicle', 'Car Policy 2026', 'CODE_26', '2026-10-01 10:00:00', '2026-10-01', 1695000, 'rec_yr_2025', 'active')
            ON CONFLICT(id) DO UPDATE SET title = excluded.title, parent_record_id = excluded.parent_record_id
            """,
            (user_id,)
        )
        await db.commit()

    # Query from middle node (2025)
    lineage = await get_document_lineage("rec_yr_2025", user_id)
    assert len(lineage) == 3
    assert [r["id"] for r in lineage] == ["rec_yr_2024", "rec_yr_2025", "rec_yr_2026"]

    # Query from leaf node (2026)
    lineage_leaf = await get_document_lineage("rec_yr_2026", user_id)
    assert len(lineage_leaf) == 3
    assert [r["id"] for r in lineage_leaf] == ["rec_yr_2024", "rec_yr_2025", "rec_yr_2026"]

    # Query from root node (2024)
    lineage_root = await get_document_lineage("rec_yr_2024", user_id)
    assert len(lineage_root) == 3
    assert [r["id"] for r in lineage_root] == ["rec_yr_2024", "rec_yr_2025", "rec_yr_2026"]


def test_render_version_timeline_text():
    """Verify ASCII/Unicode timeline rendering, cost differences and percentage deltas."""
    lineage = [
        {"id": "r1", "title": "Car Policy 2024", "created_at": "2024-10-01", "key_date": "2024-10-01", "amount_minor": 1420000, "status": "expired"},
        {"id": "r2", "title": "Car Policy 2025", "created_at": "2025-10-01", "key_date": "2025-10-01", "amount_minor": 1550000, "status": "expired"},
        {"id": "r3", "title": "Car Policy 2026", "created_at": "2026-10-01", "key_date": "2026-10-01", "amount_minor": 1695000, "status": "active"},
    ]

    text = render_version_timeline_text(lineage, current_record_id="r3")

    assert "DOCUMENT VERSION TIMELINE" in text
    assert "Car Policy 2024" in text
    assert "Car Policy 2025" in text
    assert "Car Policy 2026" in text
    assert "₹14,200.00" in text
    assert "₹15,500.00" in text
    assert "₹16,950.00" in text
    # Verify calculated deltas
    assert "+₹1,300.00" in text  # 15500 - 14200
    assert "+₹1,450.00" in text  # 16950 - 15500
    assert "⭐ Current Version" in text


@pytest.mark.asyncio
async def test_on_view_timeline(dummy_user):
    """Callback view_timeline:{record_id}:{origin} renders the timeline and back button."""
    user_id = dummy_user.id
    record_id = "rec_yr_2026"

    cq = MagicMock(spec=CallbackQuery)
    cq.from_user = dummy_user
    cq.data = f"view_timeline:{record_id}:search"
    cq.message = MagicMock(spec=Message)
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    await on_view_timeline(cq)

    cq.message.edit_text.assert_called_once()
    rendered_text = cq.message.edit_text.call_args[0][0]
    assert "TIMELINE" in rendered_text.upper()
    kb = cq.message.edit_text.call_args[1]["reply_markup"]
    assert any("Back to Document" in btn.text for row in kb.inline_keyboard for btn in row)
