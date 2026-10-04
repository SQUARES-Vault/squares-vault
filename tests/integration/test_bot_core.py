"""Integration tests for Bot Core (M1).

Acceptance criteria:
- group messages are ignored (PrivateChatOnlyMiddleware)
- rate limit enforced (RateLimitMiddleware)
- states expire (SqliteStorage)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, User

from squares_vault.bot.middlewares.private_chat import PrivateChatOnlyMiddleware
from squares_vault.bot.middlewares.ratelimit import RateLimitMiddleware, MSG_LIMIT, _message_times, _upload_times
from squares_vault.bot.state import SqliteStorage, STATE_EXPIRY_MINUTES
from squares_vault.db.migrations import run_migrations
from squares_vault.db.connection import get_db


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    """Run migrations on the test database before each test."""
    async with get_db() as db:
        await run_migrations(db)
    yield


@pytest.mark.asyncio
async def test_private_chat_only_middleware():
    """Group messages are ignored."""
    middleware = PrivateChatOnlyMiddleware()
    handler = AsyncMock(return_value="handled")
    
    # Private chat
    chat_private = Chat(id=1, type="private")
    msg_private = Message(message_id=1, date=datetime.now(timezone.utc), chat=chat_private)
    res1 = await middleware(handler, msg_private, {})
    assert res1 == "handled"
    handler.assert_called_once()
    
    # Group chat
    chat_group = Chat(id=2, type="group", title="Group")
    msg_group = Message(message_id=2, date=datetime.now(timezone.utc), chat=chat_group)
    res2 = await middleware(handler, msg_group, {})
    assert res2 is None
    assert handler.call_count == 1  # Not called again


@pytest.mark.asyncio
async def test_rate_limit_middleware():
    """Rate limits are enforced (20 msg/min)."""
    # Reset globals
    _message_times.clear()
    _upload_times.clear()
    
    middleware = RateLimitMiddleware()
    handler = AsyncMock(return_value="handled")
    
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = 123
    msg.photo = None
    msg.document = None
    msg.answer = AsyncMock()
    
    # Send MSG_LIMIT messages
    for _ in range(MSG_LIMIT):
        res = await middleware(handler, msg, {})
        assert res == "handled"
    
    assert handler.call_count == MSG_LIMIT
    
    # Next message should be rate limited
    res_limited = await middleware(handler, msg, {})
    assert res_limited is None
    msg.answer.assert_called_once()
    assert handler.call_count == MSG_LIMIT  # Still MSG_LIMIT


@pytest.mark.asyncio
async def test_state_expiry():
    """FSM states expire after 30 minutes."""
    storage = SqliteStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    
    # Set state
    await storage.set_state(key, "SOME_STATE")
    await storage.set_data(key, {"foo": "bar"})
    
    # Check it exists
    state = await storage.get_state(key)
    assert state == "SOME_STATE"
    data = await storage.get_data(key)
    assert data == {"foo": "bar"}
    
    # Manually backdate the expiry in DB to simulate time passing
    async with get_db() as db:
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=STATE_EXPIRY_MINUTES + 1)).isoformat()
        await db.execute("UPDATE user_state SET expires_at = ?", (old_time,))
        await db.commit()
    
    # Check it expired
    state_expired = await storage.get_state(key)
    assert state_expired is None
    data_expired = await storage.get_data(key)
    assert data_expired == {}
