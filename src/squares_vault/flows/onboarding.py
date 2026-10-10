"""Onboarding flow (M2).

Handles /start for new users, consent, invite code verification, and timezone.
"""

from __future__ import annotations

import dateparser
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

from squares_vault import config
from squares_vault.bot.states import Onboarding
from squares_vault.bot.keyboards.main import get_main_keyboard
from squares_vault.db.connection import get_db
from squares_vault.i18n.loader import t

router = Router(name="onboarding")


def _consent_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_consent_yes"))],
            [KeyboardButton(text=t("btn_consent_no"))],
        ],
        resize_keyboard=True,
    )


@router.message(CommandStart())
async def start_onboarding(message: Message, state: FSMContext) -> None:
    """Handle /start command. Check if user exists, else start onboarding."""
    if not message.from_user:
        return

    user_id = message.from_user.id
    
    async with get_db() as db:
        async with db.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    
    if row:
        # User already exists
        await state.clear()
        await message.answer(t("start_welcome"), reply_markup=get_main_keyboard())
        return

    # Check if signups are enabled
    from squares_vault.bot.handlers.admin import get_system_setting
    signups_on = (await get_system_setting("signups_enabled", "1")) == "1"
    if not signups_on and user_id not in config.settings.admin_user_ids:
        await message.answer("⚠️ New user registrations are temporarily paused by the administrator.")
        return

    # New user: start consent flow
    await state.set_state(Onboarding.consent)
    await message.answer(
        t("onboarding_consent", url=config.settings.privacy_policy_url),
        reply_markup=_consent_kb(),
        parse_mode="Markdown",
    )


@router.message(Onboarding.consent, F.text == t("btn_consent_no"))
async def consent_no(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(t("onboarding_rejected"), reply_markup=ReplyKeyboardRemove())


@router.message(Onboarding.consent, F.text == t("btn_consent_yes"))
async def consent_yes(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    
    # Log consent (S9 requirement)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO consent_log (user_id, policy_version) VALUES (?, ?)",
            (user_id, config.settings.policy_version)
        )
        await db.commit()
    
    if user_id in config.settings.admin_user_ids:
        # Admin bypasses invite code
        await state.set_state(Onboarding.timezone)
        await message.answer(t("onboarding_tz_prompt"), reply_markup=ReplyKeyboardRemove())
    else:
        await state.set_state(Onboarding.invite_code)
        await message.answer(t("onboarding_invite_prompt"), reply_markup=ReplyKeyboardRemove())


@router.message(Onboarding.invite_code, F.text)
async def process_invite_code(message: Message, state: FSMContext) -> None:
    import hashlib
    
    code = message.text.strip()
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    
    async with get_db() as db:
        async with db.execute("SELECT id FROM invite_codes WHERE code_hash = ? AND used_by IS NULL", (code_hash,)) as cur:
            row = await cur.fetchone()
            
        if not row:
            await message.answer(t("onboarding_invite_invalid"))
            return
            
        # Code is valid, mark as used temporarily in state so it can't be stolen?
        # Better to mark it used at final step. Pass it to next state.
        await state.update_data(invite_code=row["id"])
        
    await state.set_state(Onboarding.timezone)
    await message.answer(t("onboarding_tz_prompt"))


@router.message(Onboarding.timezone, F.text)
async def process_timezone(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    tz = config.settings.default_tz
    
    if text.lower() != "skip":
        valid = False
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(text)
            tz = text
            valid = True
        except Exception:
            try:
                import zoneinfo
                available = {z.lower(): z for z in zoneinfo.available_timezones()}
                if text.lower() in available:
                    tz = available[text.lower()]
                    valid = True
            except Exception:
                pass

        if not valid:
            # Fallback check with dateparser
            parsed = dateparser.parse(f"now in {text}")
            if parsed and parsed.tzinfo:
                tz = text
                valid = True

        if not valid:
            await message.answer(t("onboarding_tz_invalid"))
            return

    # Finalize onboarding
    data = await state.get_data()
    invite_id = data.get("invite_code")
    user_id = message.from_user.id
    username = message.from_user.username or ""
    
    async with get_db() as db:
        # Check hard cap (100 users) if not admin
        if user_id not in config.settings.admin_user_ids:
            async with db.execute("SELECT COUNT(*) as c FROM users") as cur:
                count_row = await cur.fetchone()
                if count_row and count_row["c"] >= config.settings.max_users:
                    await message.answer("User limit reached.")
                    await state.clear()
                    return

        # Insert user
        await db.execute(
            """
            INSERT INTO users (id, chat_id, tz)
            VALUES (?, ?, ?)
            """,
            (user_id, message.chat.id, tz)
        )
        
        # Mark invite used
        if invite_id:
            await db.execute(
                "UPDATE invite_codes SET used_by = ? WHERE id = ?",
                (user_id, invite_id)
            )
            
        await db.commit()

    await state.clear()
    await message.answer(t("onboarding_done"), reply_markup=get_main_keyboard())
