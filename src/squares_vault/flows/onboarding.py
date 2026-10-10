"""Onboarding flow (M2).

Handles /start for new users, consent, invite code verification, and timezone.
"""

from __future__ import annotations

import dateparser
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from squares_vault import config
from squares_vault.bot.states import Onboarding
from squares_vault.bot.keyboards.main import get_main_keyboard
from squares_vault.db.connection import get_db
from squares_vault.i18n.loader import t

router = Router(name="onboarding")


def _consent_inline_kb() -> InlineKeyboardMarkup:
    portal_url = getattr(config.settings, "portal_url", "https://squares-vault.github.io/squares-vault/")
    privacy_url = getattr(config.settings, "privacy_policy_url", "https://squares-vault.github.io/squares-vault/privacy.html")
    terms_url = getattr(config.settings, "terms_url", "https://squares-vault.github.io/squares-vault/terms.html")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌐 Explore Web Portal ↗", url=portal_url)
            ],
            [
                InlineKeyboardButton(text="📜 Privacy Policy", url=privacy_url),
                InlineKeyboardButton(text="⚖️ Terms of Service", url=terms_url),
            ],
            [
                InlineKeyboardButton(text="✅ Accept & Enter Vault", callback_data="onboard_consent_yes")
            ],
            [
                InlineKeyboardButton(text="❌ Decline", callback_data="onboard_consent_no")
            ],
        ]
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
        portal_url = getattr(config.settings, "portal_url", "https://squares-vault.github.io/squares-vault/")
        await message.answer(
            t("start_welcome", portal_url=portal_url),
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
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
        t("onboarding_consent"),
        reply_markup=_consent_inline_kb(),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "onboard_consent_no")
async def cb_consent_no(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(t("onboarding_rejected"), parse_mode="Markdown")
    await call.answer()


@router.callback_query(F.data == "onboard_consent_yes")
async def cb_consent_yes(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    
    # Log consent (S9 requirement)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO consent_log (user_id, policy_version) VALUES (?, ?)",
            (user_id, config.settings.policy_version)
        )
        await db.commit()
    
    await call.answer()
    await state.set_state(Onboarding.invite_code)
    await call.message.answer(t("onboarding_invite_prompt"), parse_mode="Markdown")


@router.message(Onboarding.consent, F.text.in_([t("btn_consent_no"), "❌ Decline", "❌ No", "No", "decline"]))
async def consent_no(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(t("onboarding_rejected"), reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")


@router.message(Onboarding.consent, F.text.in_([t("btn_consent_yes"), "✅ Accept & Enter Vault", "✅ Accept & Enter Vault (18+)", "Yes", "accept"]))
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
        # Admin bypasses invite code in automated test runs
        await state.set_state(Onboarding.timezone)
        await message.answer(t("onboarding_tz_prompt"), reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    else:
        await state.set_state(Onboarding.invite_code)
        await message.answer(t("onboarding_invite_prompt"), reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")


@router.message(Onboarding.invite_code, F.text)
async def process_invite_code(message: Message, state: FSMContext) -> None:
    import hashlib
    
    code = message.text.strip()
    user_id = message.from_user.id if message.from_user else 0
    if user_id in config.settings.admin_user_ids and code.lower() == "skip":
        await state.set_state(Onboarding.timezone)
        await message.answer(t("onboarding_tz_prompt"), parse_mode="Markdown")
        return
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    
    async with get_db() as db:
        async with db.execute("SELECT id FROM invite_codes WHERE code_hash = ? AND used_by IS NULL", (code_hash,)) as cur:
            row = await cur.fetchone()
            
        if not row:
            await message.answer(t("onboarding_invite_invalid"), parse_mode="Markdown")
            return
            
        await state.update_data(invite_code=row["id"])
        
    await state.set_state(Onboarding.timezone)
    await message.answer(t("onboarding_tz_prompt"), parse_mode="Markdown")


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
            await message.answer(t("onboarding_tz_invalid"), parse_mode="Markdown")
            return

    # Finalize onboarding
    data = await state.get_data()
    invite_id = data.get("invite_code")
    user_id = message.from_user.id
    
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
    await message.answer(t("onboarding_done"), reply_markup=get_main_keyboard(), parse_mode="Markdown")

    # Prompt to connect personal Google Drive
    drive_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Connect Google Drive", callback_data="drive_auth_start")],
        [InlineKeyboardButton(text="⏩ Skip for Now", callback_data="settings_close")]
    ])
    await message.answer(
        "🔐 *Connect Your Google Drive*\n\n"
        "Link your personal Google Drive to keep all your documents strictly under your ownership:\n\n"
        "• *Private Storage:* Uploaded files and backups go straight to your Drive.\n"
        "• *Zero Lock-in:* Access or download your documents anytime.",
        reply_markup=drive_kb,
        parse_mode="Markdown"
    )
