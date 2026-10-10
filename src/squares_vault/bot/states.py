"""FSM states for the bot."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    """States for the /start onboarding flow."""
    consent = State()
    invite_code = State()
    timezone = State()


class FamilyAccess(StatesGroup):
    """States for Family / Co-Pilot folder sharing."""
    waiting_for_grant = State()


class AskVault(StatesGroup):
    """States for the interactive 'Ask Your Vault' chat mode."""
    chatting = State()
