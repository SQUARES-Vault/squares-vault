import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User, Chat
from aiogram.fsm.context import FSMContext

from squares_vault.bot.handlers.base import handle_menu_buttons
from squares_vault.bot.states import AskVault


@pytest.fixture
def dummy_user():
    return User(id=88005, is_bot=False, first_name="Shikhar")


@pytest.fixture
def dummy_chat():
    return Chat(id=88005, type="private")


@pytest.mark.asyncio
async def test_natural_language_query_at_main_menu_routes_to_ask_vault(dummy_user, dummy_chat):
    """When a user sends a query like 'Show last five uploaded docs' from the main menu,
    it must automatically activate AskVault.chatting and delegate to handle_ask_vault_query,
    instead of replying with the old rigid fallback message!
    """
    message = MagicMock(spec=Message)
    message.text = "Show last five uploaded docs"
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.reply = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.set_state = AsyncMock()

    with patch("squares_vault.bot.handlers.admin.get_system_setting", AsyncMock(return_value="1")), \
         patch("squares_vault.bot.handlers.ask.handle_ask_vault_query", AsyncMock()) as mock_ask:
        await handle_menu_buttons(message, state)

        # Must have activated chatting state
        state.set_state.assert_awaited_once_with(AskVault.chatting)
        # Must have invoked ask handler
        mock_ask.assert_awaited_once_with(message, state)
        # Must NOT have replied with the old robotic message
        message.reply.assert_not_called()


@pytest.mark.asyncio
async def test_natural_language_query_when_ai_disabled(dummy_user, dummy_chat):
    """When AI is disabled, the fallback message provides a helpful message with the main keyboard."""
    message = MagicMock(spec=Message)
    message.text = "Show last five uploaded docs"
    message.from_user = dummy_user
    message.chat = dummy_chat
    message.reply = AsyncMock()

    state = AsyncMock(spec=FSMContext)
    state.set_state = AsyncMock()

    with patch("squares_vault.bot.handlers.admin.get_system_setting", AsyncMock(return_value="0")):
        await handle_menu_buttons(message, state)

        state.set_state.assert_not_called()
        message.reply.assert_awaited_once()
        args, kwargs = message.reply.call_args
        assert "You can type any question about your documents" in args[0]
