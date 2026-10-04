"""Integration tests for M2 (Onboarding)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, User

from squares_vault.bot.states import Onboarding
from squares_vault.db.connection import get_db
from squares_vault.db.migrations import run_migrations
from squares_vault.flows.onboarding import (
    start_onboarding,
    consent_yes,
    process_invite_code,
    process_timezone
)
from squares_vault.i18n.loader import t


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    """Run migrations on the test database before each test."""
    async with get_db() as db:
        await run_migrations(db)
        
        import hashlib
        code_hash = hashlib.sha256(b"TEST-CODE").hexdigest()
        await db.execute("INSERT OR IGNORE INTO invite_codes (code_hash) VALUES (?)", (code_hash,))
        await db.commit()
    yield


def _mock_msg(user_id=12345, text="") -> Message:
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = user_id
    msg.from_user.username = "testuser"
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_onboarding_flow():
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value={"invite_code": 1})
    
    # 1. /start triggers consent
    msg = _mock_msg(user_id=555, text="/start")
    await start_onboarding(msg, state)
    state.set_state.assert_called_with(Onboarding.consent)
    from unittest.mock import ANY
    msg.answer.assert_called_with(t("onboarding_consent"), reply_markup=ANY)
    
    # 2. Consent YES -> wait invite code
    msg = _mock_msg(user_id=555, text=t("btn_consent_yes"))
    await consent_yes(msg, state)
    
    # Check DB for consent
    async with get_db() as db:
        async with db.execute("SELECT policy_version FROM consent_log WHERE user_id=555") as cur:
            row = await cur.fetchone()
            assert row is not None
            assert row["policy_version"] == "1.0"
            
    state.set_state.assert_called_with(Onboarding.invite_code)
    
    # 3. Invite code -> wait timezone
    msg = _mock_msg(user_id=555, text="TEST-CODE")
    await process_invite_code(msg, state)
    state.update_data.assert_called_with(invite_code=1)
    state.set_state.assert_called_with(Onboarding.timezone)
    
    # 4. Timezone -> Complete
    msg = _mock_msg(user_id=555, text="skip")
    await process_timezone(msg, state)
    
    # Check user in DB
    async with get_db() as db:
        async with db.execute("SELECT tz FROM users WHERE id=555") as cur:
            user = await cur.fetchone()
            assert user is not None
            assert user["tz"] == "Asia/Kolkata"
            
        # Check invite marked used
        async with db.execute("SELECT used_by FROM invite_codes WHERE id=1") as cur:
            invite = await cur.fetchone()
            assert invite["used_by"] == 555


@pytest.mark.asyncio
async def test_onboarding_admin_bypass():
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    
    # Use admin ID from conftest (999999999)
    msg = _mock_msg(user_id=999999999, text=t("btn_consent_yes"))
    await consent_yes(msg, state)
    
    # Should skip invite and go straight to timezone
    state.set_state.assert_called_with(Onboarding.timezone)
