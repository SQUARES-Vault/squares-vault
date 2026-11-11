import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, Chat
from aiogram.fsm.context import FSMContext

from squares_vault.bot.middlewares.menu_interrupt import MenuInterruptMiddleware

@pytest.fixture
def dummy_user():
    return User(id=88005, is_bot=False, first_name="Shikhar")

@pytest.fixture
def dummy_chat():
    return Chat(id=88005, type="private")

@pytest.mark.asyncio
async def test_menu_interrupt_does_not_clear_on_conversational_queries(dummy_user, dummy_chat):
    """Conversational questions containing words like 'added', 'search', 'find', 'address'
    must NEVER clear the user's active FSM chat state!
    """
    middleware = MenuInterruptMiddleware()
    handler = AsyncMock()

    # Queries that previously caused state to be dropped due to loose substring matching
    safe_queries = [
        "What new features are added to bot?",
        "Can you search for my electricity bills?",
        "Please find my passport renewal slip",
        "What is my registered address on the RC?",
        "Show my upcoming deadlines for this month",
        "Tell me about newly added records",
    ]

    for q in safe_queries:
        message = MagicMock(spec=Message)
        message.text = q
        message.from_user = dummy_user
        message.chat = dummy_chat

        state = AsyncMock(spec=FSMContext)
        state.get_state.return_value = "AskVault:chatting"
        state.clear = AsyncMock()

        data = {"state": state}
        await middleware(handler, message, data)

        # state.clear() must NOT be called for conversational questions
        state.clear.assert_not_called()
        handler.assert_called_once()
        handler.reset_mock()


@pytest.mark.asyncio
async def test_menu_interrupt_clears_on_actual_menu_buttons(dummy_user, dummy_chat):
    """Actual bottom keyboard menu buttons or exact commands MUST clear the FSM state."""
    middleware = MenuInterruptMiddleware()
    handler = AsyncMock()

    actual_menu_buttons = [
        "➕ Add",
        "🔍 Search",
        "📁 Browse",
        "💬 Ask Your Vault",
        "📅 Upcoming",
        "⚙️ Settings",
        "add",
        "search",
        "browse",
        "settings",
        "upcoming",
    ]

    for btn in actual_menu_buttons:
        message = MagicMock(spec=Message)
        message.text = btn
        message.from_user = dummy_user
        message.chat = dummy_chat

        state = AsyncMock(spec=FSMContext)
        state.get_state.return_value = "AskVault:chatting"
        state.clear = AsyncMock()

        data = {"state": state}
        await middleware(handler, message, data)

        # state.clear() MUST be called when pressing an actual menu button
        state.clear.assert_called_once()
        handler.assert_called_once()
        handler.reset_mock()
