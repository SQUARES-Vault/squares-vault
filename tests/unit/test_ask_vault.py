"""Unit tests for 'Ask Your Vault' dynamic assistant and navigation flow."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, User, Chat

from squares_vault.bot.states import AskVault
from squares_vault.bot.handlers.ask import (
    cmd_ask_vault_start,
    on_ask_vault_exit,
    handle_ask_vault_query,
    on_ask_view_docs,
    on_ask_back_to_ans,
    on_ask_vault_return,
)
from squares_vault.db.connection import get_db


@pytest.fixture
def dummy_user():
    return User(id=1155857416, is_bot=False, first_name="Shikhar")


@pytest.fixture
def dummy_chat():
    return Chat(id=1155857416, type="private")


@pytest.mark.asyncio
async def test_cmd_ask_vault_start(dummy_user, dummy_chat):
    message = MagicMock(spec=Message)
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.text = "💬 Ask Your Vault"
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    await cmd_ask_vault_start(message, state)

    state.set_state.assert_called_once_with(AskVault.chatting)
    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Ask Your Vault" in args[0]
    kb = kwargs["reply_markup"]
    assert any(btn.callback_data == "ask_vault_exit" for row in kb.inline_keyboard for btn in row)


@pytest.mark.asyncio
async def test_on_ask_vault_exit(dummy_user, dummy_chat):
    cq = MagicMock(spec=CallbackQuery)
    cq.from_user = dummy_user
    cq.message = MagicMock()
    cq.message.answer = AsyncMock()
    cq.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    await on_ask_vault_exit(cq, state)

    state.clear.assert_called_once()
    cq.answer.assert_called_once()
    cq.message.answer.assert_called_once()
    args, _ = cq.message.answer.call_args
    assert "Chat mode ended" in args[0]


@pytest.mark.asyncio
async def test_handle_ask_vault_dynamic_qa_with_docs(dummy_user, dummy_chat):
    message = MagicMock(spec=Message)
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.text = "What is due between 1 jan 2027 and 31 dec 2027?"
    message.bot = MagicMock()
    message.bot.send_chat_action = AsyncMock()
    message.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)

    # Insert test record into DB
    async with get_db() as db:
        await db.execute(
            "INSERT INTO users (id, chat_id) VALUES (1155857416, 1155857416) ON CONFLICT(id) DO NOTHING"
        )
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, key_date, key_date_type, status)
            VALUES ('rec_mygate_2027', 1155857416, 'other', 'MyGate Invitation', 'CODEGATE1', '2027-11-18', 'expiry', 'active')
            ON CONFLICT(id) DO UPDATE SET key_date = excluded.key_date
            """
        )
        await db.commit()

    with patch("squares_vault.bot.handlers.ask.answer_vault_query", new_callable=AsyncMock) as mock_qa:
        mock_qa.return_value = "Your MyGate Invitation (ID: rec_mygate_2027) expires on 2027-11-18."

        await handle_ask_vault_query(message, state)

        mock_qa.assert_called_once()
        message.answer.assert_called_once()
        args, kwargs = message.answer.call_args
        reply_text = args[0]

        # Verify internal ID was cleaned from user-facing text
        assert "ID: rec_mygate_2027" not in reply_text
        assert "MyGate Invitation" in reply_text
        assert "2027-11-18" in reply_text

        # Verify state update saved matched IDs
        state.update_data.assert_called_once()
        update_args = state.update_data.call_args[1]
        assert update_args["ask_matched_ids"] == ["rec_mygate_2027"]

        # Verify clean keyboard (only View Documents button and End Chat button)
        kb = kwargs["reply_markup"]
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert callbacks == ["ask_view_docs", "ask_vault_exit"]


@pytest.mark.asyncio
async def test_on_ask_view_docs(dummy_user, dummy_chat):
    cq = MagicMock(spec=CallbackQuery)
    cq.from_user = dummy_user
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={
        "ask_matched_ids": ["rec_mygate_2027"],
        "ask_last_answer": "Your MyGate Invitation expires on 2027-11-18."
    })

    await on_ask_view_docs(cq, state)

    cq.message.edit_text.assert_called_once()
    args, kwargs = cq.message.edit_text.call_args
    assert "Referenced Documents" in args[0]
    kb = kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    # Document view button MUST include :ask origin
    assert "view_record:rec_mygate_2027:ask" in callbacks
    assert "ask_back_to_ans" in callbacks
    assert "ask_vault_exit" in callbacks


@pytest.mark.asyncio
async def test_on_ask_back_to_ans(dummy_user, dummy_chat):
    cq = MagicMock(spec=CallbackQuery)
    cq.from_user = dummy_user
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={
        "ask_matched_ids": ["rec_mygate_2027"],
        "ask_last_answer": "Your MyGate Invitation expires on 2027-11-18."
    })

    await on_ask_back_to_ans(cq, state)

    cq.message.edit_text.assert_called_once()
    args, kwargs = cq.message.edit_text.call_args
    assert "Your MyGate Invitation expires on 2027-11-18." in args[0]
    kb = kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert callbacks == ["ask_view_docs", "ask_vault_exit"]


@pytest.mark.asyncio
async def test_on_ask_vault_return(dummy_user, dummy_chat):
    cq = MagicMock(spec=CallbackQuery)
    cq.from_user = dummy_user
    cq.message = MagicMock()
    cq.message.edit_text = AsyncMock()
    cq.answer = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={
        "ask_matched_ids": ["rec_mygate_2027"],
        "ask_last_answer": "Your MyGate Invitation expires on 2027-11-18."
    })

    await on_ask_vault_return(cq, state)

    state.set_state.assert_called_once_with(AskVault.chatting)
    cq.message.edit_text.assert_called_once()
    args, kwargs = cq.message.edit_text.call_args
    assert "Your MyGate Invitation expires on 2027-11-18." in args[0]
    kb = kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert callbacks == ["ask_view_docs", "ask_vault_exit"]


@pytest.mark.asyncio
async def test_handle_ask_vault_menu_button_interrupt(dummy_user, dummy_chat):
    message = MagicMock(spec=Message)
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.text = "📅 Upcoming"

    state = AsyncMock(spec=FSMContext)

    with patch("squares_vault.bot.handlers.base.handle_menu_buttons", new_callable=AsyncMock) as mock_menu:
        await handle_ask_vault_query(message, state)

        state.clear.assert_called_once()
        mock_menu.assert_called_once_with(message, state)
