"""Admin operations handler suite (Owner ID only).

Provides commands:
- /admin_stats: Comprehensive live dashboard (users, storage, queue, key pool, daily/monthly AI quotas)
- /admin_invite [count]: Generate single-use invite codes
- /admin_signups [on|off]: Emergency signup kill-switch
- /admin_ai [on|off]: Emergency AI pipeline kill-switch
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from squares_vault import config
from squares_vault.ai.quota import get_ai_quota_summary
from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

router = Router(name="admin")


async def _ensure_system_settings_table(db) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    await db.commit()


async def get_system_setting(key: str, default: str = "1") -> str:
    """Retrieve runtime system setting from database."""
    async with get_db() as db:
        await _ensure_system_settings_table(db)
        async with db.execute("SELECT value FROM system_settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row["value"] if row else default


async def set_system_setting(key: str, value: str) -> None:
    """Update runtime system setting in database."""
    async with get_db() as db:
        await _ensure_system_settings_table(db)
        await db.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message) -> None:
    """Live admin dashboard with AI limits, key pool health, and system capacity."""
    if not message.from_user or message.from_user.id not in config.settings.admin_user_ids:
        await message.answer("⛔ Unauthorized: Admin access only.")
        return

    async with get_db() as db:
        # 1. Users
        async with db.execute("SELECT COUNT(*) as c FROM users") as cur:
            row = await cur.fetchone()
            users_count = row["c"] if row else 0

        # 2. Invites
        async with db.execute("SELECT COUNT(*) as c FROM invite_codes WHERE used_by IS NULL") as cur:
            row = await cur.fetchone()
            unused_invites = row["c"] if row else 0

        async with db.execute("SELECT COUNT(*) as c FROM invite_codes") as cur:
            row = await cur.fetchone()
            total_invites = row["c"] if row else 0

        # 3. Records & Files
        async with db.execute("SELECT COUNT(*) as c FROM records WHERE status != 'soft_deleted'") as cur:
            row = await cur.fetchone()
            records_count = row["c"] if row else 0

        async with db.execute("SELECT COUNT(*) as c FROM files") as cur:
            row = await cur.fetchone()
            files_count = row["c"] if row else 0

        # 4. Reminders
        async with db.execute("SELECT COUNT(*) as c FROM reminders WHERE state = 'pending'") as cur:
            row = await cur.fetchone()
            pending_reminders = row["c"] if row else 0

        # 5. AI Queue
        async with db.execute("SELECT state, COUNT(*) as c FROM ai_queue GROUP BY state") as cur:
            queue_rows = await cur.fetchall()
            queue_stats = {r["state"]: r["c"] for r in queue_rows}

    queued_count = queue_stats.get("queued", 0)
    processing_count = queue_stats.get("processing", 0)
    failed_count = queue_stats.get("failed", 0)

    # 6. AI Quota Summary & Key Pool Status
    ai_summary = await get_ai_quota_summary()
    signups_on = (await get_system_setting("signups_enabled", "1")) == "1"
    ai_on = (await get_system_setting("ai_enabled", "1")) == "1"

    # Format Key Pool status lines
    key_lines = []
    for k in ai_summary["pool_keys"]:
        status_icon = "🟢" if k["status"] == "active" else "🟡"
        cd_info = f" (cooling down: {k['cooldown_remaining_sec']}s)" if k["status"] == "cooling_down" else ""
        reqs = k["requests_today"]
        key_lines.append(f"  • Key #{k['index']} (`{k['masked']}`): {status_icon} {k['status'].title()}{cd_info} | {reqs} reqs")

    key_section = "\n".join(key_lines) if key_lines else "  • No keys configured"

    users_pct = (users_count / config.settings.max_users * 100.0) if config.settings.max_users else 0.0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    text = (
        f"📊 *SQUARES VAULT – ADMIN DASHBOARD*\n"
        f"─────────────────────────────\n"
        f"👥 *Users & Capacity:*\n"
        f"• Registered: *{users_count} / {config.settings.max_users}* ({users_pct:.1f}%)\n"
        f"• Invite Codes: {unused_invites} unused / {total_invites} total\n\n"
        f"📁 *Vault Storage:*\n"
        f"• Active Records: *{records_count}*\n"
        f"• Linked Files: *{files_count}*\n"
        f"• Pending Reminders: *{pending_reminders}*\n\n"
        f"⚙️ *AI Queue:*\n"
        f"• Queued: {queued_count} | Processing: {processing_count} | Failed: {failed_count}\n\n"
        f"🤖 *Gemini Key Pool ({ai_summary['num_keys']} Accounts Pooled):*\n"
        f"{key_section}\n\n"
        f"📈 *Total AI Limits & Remaining Quota:*\n"
        f"• *Daily Limit:* {ai_summary['total_daily_limit']:,} reqs/day\n"
        f"  └ Used: *{ai_summary['used_today']:,}* | Remaining: *{ai_summary['remaining_today']:,}* ({(100 - ai_summary['pct_used_today']):.1f}% left)\n"
        f"• *Monthly Limit:* {ai_summary['total_monthly_limit']:,} reqs/month ({ai_summary['days_in_month']} days)\n"
        f"  └ Used: *{ai_summary['used_month']:,}* | Remaining: *{ai_summary['remaining_month']:,}* ({(100 - ai_summary['pct_used_month']):.1f}% left)\n\n"
        f"🚦 *System Switches:*\n"
        f"• Signups: {'🟢 Enabled' if signups_on else '🔴 Paused'}\n"
        f"• AI Pipeline: {'🟢 Active' if ai_on else '🔴 Paused'}\n"
        f"─────────────────────────────\n"
        f"🕒 _{now_utc}_"
    )

    await message.answer(text, parse_mode="Markdown")


@router.message(Command("admin_invite"))
async def cmd_admin_invite(message: Message) -> None:
    """Generate single-use alphanumeric invite codes. Usage: /admin_invite [count]."""
    if not message.from_user or message.from_user.id not in config.settings.admin_user_ids:
        await message.answer("⛔ Unauthorized: Admin access only.")
        return

    # Parse count argument
    count = 1
    parts = (message.text or "").split()
    if len(parts) > 1:
        try:
            count = max(1, min(25, int(parts[1])))
        except ValueError:
            await message.answer("⚠️ Please provide a valid integer (1–25). Example: `/admin_invite 5`", parse_mode="Markdown")
            return

    generated_codes = []
    async with get_db() as db:
        for _ in range(count):
            raw_code = f"SV-{secrets.token_hex(3).upper()}"
            code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
            await db.execute(
                "INSERT INTO invite_codes (code_hash, created_at) VALUES (?, CURRENT_TIMESTAMP)",
                (code_hash,),
            )
            generated_codes.append(raw_code)
        await db.commit()

    codes_formatted = "\n".join(f"`{c}`" for c in generated_codes)
    text = (
        f"🎟️ *Generated {count} Single-Use Invite Code(s):*\n\n"
        f"{codes_formatted}\n\n"
        f"_Tap any code to copy. Each code allows 1 user to complete onboarding._"
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(Command("admin_signups"))
async def cmd_admin_signups(message: Message) -> None:
    """Toggle signups kill-switch: /admin_signups [on|off]."""
    if not message.from_user or message.from_user.id not in config.settings.admin_user_ids:
        await message.answer("⛔ Unauthorized: Admin access only.")
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        current = await get_system_setting("signups_enabled", "1")
        status_str = "🟢 Enabled" if current == "1" else "🔴 Disabled"
        await message.answer(f"Usage: `/admin_signups on` or `/admin_signups off`\nCurrent status: {status_str}", parse_mode="Markdown")
        return

    new_val = "1" if parts[1].lower() == "on" else "0"
    await set_system_setting("signups_enabled", new_val)
    status_str = "🟢 ENABLED" if new_val == "1" else "🔴 DISABLED"
    await message.answer(f"✅ User signups are now *{status_str}*.", parse_mode="Markdown")


@router.message(Command("admin_ai"))
async def cmd_admin_ai(message: Message) -> None:
    """Toggle AI pipeline kill-switch: /admin_ai [on|off]."""
    if not message.from_user or message.from_user.id not in config.settings.admin_user_ids:
        await message.answer("⛔ Unauthorized: Admin access only.")
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1].lower() not in ("on", "off"):
        current = await get_system_setting("ai_enabled", "1")
        status_str = "🟢 Enabled" if current == "1" else "🔴 Paused"
        await message.answer(f"Usage: `/admin_ai on` or `/admin_ai off`\nCurrent status: {status_str}", parse_mode="Markdown")
        return

    new_val = "1" if parts[1].lower() == "on" else "0"
    await set_system_setting("ai_enabled", new_val)
    status_str = "🟢 ACTIVE" if new_val == "1" else "🔴 PAUSED"
    await message.answer(f"✅ AI document extraction pipeline is now *{status_str}*.", parse_mode="Markdown")
