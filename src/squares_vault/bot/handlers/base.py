"""Base handlers: /start, /help, /menu, and main bottom menu buttons."""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from dateutil.parser import parse

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from squares_vault import config
from squares_vault.bot.keyboards.main import get_main_keyboard
from squares_vault.db.connection import get_db
from squares_vault.i18n.loader import t

log = logging.getLogger(__name__)

router = Router(name="base")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help."""
    await message.answer(t("help_text"))


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    """Show the main keyboard."""
    await message.answer("Menu:", reply_markup=get_main_keyboard())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Universal cancel command to abort any active wizard and return to main menu."""
    await state.clear()
    await message.reply("❌ Action cancelled. Returned to main menu.", reply_markup=get_main_keyboard())


def _escape_md(text: str) -> str:
    """Escape Telegram Markdown special characters."""
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


def _format_geometric_title(title: str, dot: str, target_col: int = 26) -> str:
    """Format a title and dot with connected leader lines so dots align horizontally across mobile and desktop."""
    clean = " ".join((title or "Untitled").split())
    max_len = target_col - 2
    if len(clean) > max_len:
        if " - " in clean:
            first_part = clean.split(" - ")[0].strip()
            if len(first_part) <= max_len:
                clean = first_part
        if len(clean) > max_len:
            clean = clean[:max_len - 1].rstrip() + "…"

    dashes_count = max(2, target_col - len(clean))
    dashes = "─" * dashes_count
    clean_escaped = _escape_md(clean)
    return f"• {clean_escaped} {dashes} {dot}"



@router.message(F.text)
async def handle_menu_buttons(message: Message, state: FSMContext) -> None:
    """Handle bottom menu buttons."""
    try:
        text = message.text or ""
        if "Add" in text:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Add Manually", callback_data="add_manual")]
            ])
            await message.reply(
                "📸 Send me a photo or PDF of a document (e.g. driving license, warranty bill) to add it to your Vault, or enter it manually.",
                reply_markup=kb,
            )
        elif "Find" in text or "Search" in text:
            from squares_vault.bot.handlers.search import cmd_search
            await cmd_search(message, state)
        elif "Browse" in text:
            user_id = message.from_user.id
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT category, COUNT(*) as c FROM records 
                    WHERE (
                        user_id = ? 
                        OR id IN (
                            SELECT r2.id FROM records r2 
                            JOIN access_grants ag ON r2.user_id = ag.owner_id 
                            WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                        )
                    ) AND status != 'soft_deleted' 
                    GROUP BY category ORDER BY c DESC LIMIT 6
                    """,
                    (user_id, user_id),
                ) as cur:
                    rows = await cur.fetchall()

            kb_buttons = []
            row_buttons = []
            for i, row in enumerate(rows):
                cat_name = row["category"]
                disp_name = cat_name if len(cat_name) < 15 else cat_name[:12] + "..."
                row_buttons.append(InlineKeyboardButton(text=f"📁 {disp_name}", callback_data=f"browse_cat:{cat_name}:0"))
                if len(row_buttons) == 2:
                    kb_buttons.append(row_buttons)
                    row_buttons = []
            if row_buttons:
                kb_buttons.append(row_buttons)

            kb_buttons.append([InlineKeyboardButton(text="📚 All Documents", callback_data="browse_cat:all:0")])
            kb_buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="settings_close")])
            kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
            await message.reply("📁 **Browse by Category**", reply_markup=kb, parse_mode="Markdown")
        elif "Ask" in text:
            from squares_vault.bot.handlers.ask import cmd_ask_vault_start
            await cmd_ask_vault_start(message, state)
        elif "Upcoming" in text:
            await _render_upcoming(message, message.from_user.id, months_limit=3, edit=False)
        elif "Settings" in text:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Storage Stats", callback_data="settings_storage")],
                [InlineKeyboardButton(text="🔗 Connections", callback_data="settings_connections")],
                [InlineKeyboardButton(text="👥 Family / Co-Pilot Access", callback_data="settings_access")],
                [InlineKeyboardButton(text="💾 Export Data", callback_data="settings_export")],
                [InlineKeyboardButton(text="🌐 Official Web Portal ↗", url=config.settings.portal_url)],
                [InlineKeyboardButton(text="📜 Privacy Policy & Terms", url=config.settings.privacy_policy_url)],
                [InlineKeyboardButton(text="🚨 Delete Account", callback_data="settings_delete")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="settings_close")],
            ])
            await message.reply(
                "⚙️ **Settings & Privacy**\n\nManage your data, export backups, or delete your account.",
                reply_markup=kb,
                parse_mode="Markdown",
            )
        else:
            await message.reply(
                "If you want to find something please use the 🔍 Search button below. To add something, please use the ➕ Add button."
            )
    except Exception as e:
        log.exception("Error in handle_menu_buttons: %s", e)
        await message.reply(
            "⚠️ An error occurred while processing your request. Please try again.",
            reply_markup=get_main_keyboard(),
        )


async def _render_upcoming(target, user_id: int, months_limit: int | None = 3, *, edit: bool = False) -> None:
    """Render upcoming deadlines and reminders, defaulting to next 3 months or all."""
    if months_limit == 3:
        filter_sql = """
            AND (
                (r.key_date_type IN ('expiry', 'due') AND r.key_date IS NOT NULL AND r.key_date != '' 
                 AND r.key_date >= date('now', '-10 days') AND r.key_date <= date('now', '+3 months'))
                OR EXISTS (
                    SELECT 1 FROM reminders rem2 
                    WHERE rem2.record_id = r.id AND rem2.state = 'pending' 
                      AND rem2.fire_at <= datetime('now', '+3 months')
                      AND (r.key_date IS NULL OR r.key_date = '' OR r.key_date >= date('now', '-10 days'))
                )
            )
        """
        title_header = "📅 **UPCOMING DEADLINES & ALERTS (Next 3 Months)**"
        empty_text = "_No deadlines or reminders found in the next 3 months._"
    else:
        filter_sql = """
            AND (
                (r.key_date_type IN ('expiry', 'due') AND r.key_date IS NOT NULL AND r.key_date != '' 
                 AND r.key_date >= date('now', '-10 days'))
                OR EXISTS (
                    SELECT 1 FROM reminders rem2 
                    WHERE rem2.record_id = r.id AND rem2.state = 'pending'
                      AND (r.key_date IS NULL OR r.key_date = '' OR r.key_date >= date('now', '-10 days'))
                )
            )
        """
        title_header = "📅 **ALL DEADLINES & ALERTS**"
        empty_text = "_No deadlines or reminders found._"

    query = f"""
        SELECT 
            r.id as record_id, 
            r.title, 
            r.category, 
            r.key_date, 
            r.key_date_type,
            r.parent_record_id,
            rem.kind, 
            rem.fire_at, 
            rem.state as reminder_state
        FROM records r
        LEFT JOIN reminders rem ON rem.record_id = r.id AND rem.state = 'pending'
        WHERE (r.user_id = ? OR r.id IN (
            SELECT r2.id FROM records r2 
            JOIN access_grants ag ON r2.user_id = ag.owner_id 
            WHERE ag.granted_id = ? AND ag.category = 'all'
        ))
          AND r.status NOT IN ('soft_deleted', 'renewed', 'done')
          {filter_sql}
        ORDER BY 
            CASE WHEN r.key_date IS NULL OR r.key_date = '' THEN 1 ELSE 0 END,
            r.key_date ASC,
            rem.fire_at ASC
    """
    async with get_db() as db:
        async with db.execute(query, (user_id, user_id)) as cur:
            rows = await cur.fetchall()

    today = datetime.now(timezone.utc).date()
    grouped = OrderedDict()
    for row in rows:
        rid = row["record_id"]
        if rid not in grouped:
            grouped[rid] = {
                "title": row["title"],
                "category": row["category"],
                "key_date": row["key_date"],
                "key_date_type": row["key_date_type"],
                "parent_record_id": row["parent_record_id"],
                "items": [],
            }
        if row["kind"] and row["reminder_state"] == "pending":
            grouped[rid]["items"].append(row)

    # Check if there are any records with pending reminders to show cancel option
    async with get_db() as db:
        async with db.execute(
            """
            SELECT count(DISTINCT r.id) as cnt
            FROM records r
            JOIN reminders rem ON rem.record_id = r.id AND rem.state = 'pending'
            WHERE (r.user_id = ? OR r.id IN (
                SELECT r2.id FROM records r2 
                JOIN access_grants ag ON r2.user_id = ag.owner_id 
                WHERE ag.granted_id = ? AND ag.category = 'all'
            ))
              AND r.status NOT IN ('soft_deleted', 'renewed', 'done')
            """,
            (user_id, user_id)
        ) as cur:
            cnt_row = await cur.fetchone()
            has_pending_any = cnt_row["cnt"] > 0 if cnt_row else False

    kb_rows = []
    if months_limit == 3:
        kb_rows.append([InlineKeyboardButton(text="📋 View All Deadlines & Reminders", callback_data="upcoming_view:all")])
    else:
        kb_rows.append([InlineKeyboardButton(text="📅 Show Next 3 Months Only", callback_data="upcoming_view:3m")])

    if has_pending_any:
        kb_rows.append([InlineKeyboardButton(text="🔕 Cancel / Remove a Reminder", callback_data="upcoming_cancel_menu")])

    kb_rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="settings_close")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    if not grouped:
        text_out = (
            f"{title_header}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{empty_text}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        msg_lines = [
            title_header,
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        ]
        max_items = 12 if months_limit == 3 else 30
        for idx, (rid, data) in enumerate(grouped.items(), 1):
            if idx > max_items:
                break

            is_expiry = data["key_date_type"] in ("expiry", "due") and bool(data["key_date"])
            is_renewed = bool(data.get("parent_record_id"))

            dot = "📄"
            if is_expiry:
                try:
                    kd = datetime.strptime(data["key_date"][:10], "%Y-%m-%d").date()
                    if kd < today:
                        dot = "🔴"
                    elif kd <= today + timedelta(days=30):
                        dot = "🟡"
                    else:
                        dot = "🟢"
                except Exception:
                    dot = "📄"
            elif data["items"]:
                dot = "🟡"

            raw_title = data["title"] or "Untitled"
            clean_title = _escape_md(raw_title)
            clean_cat = _escape_md(data["category"].title())
            items = data["items"]

            if is_renewed and data.get("key_date"):
                try:
                    due_str = parse(data["key_date"][:10]).strftime("%b %d, %Y")
                except Exception:
                    due_str = data["key_date"]
                date_label = "Renewed to"
            elif is_expiry:
                try:
                    due_str = parse(data["key_date"][:10]).strftime("%b %d, %Y")
                except Exception:
                    due_str = data["key_date"]
                date_label = "Due"
            elif items:
                try:
                    due_str = parse(items[0]["fire_at"]).strftime("%b %d, %Y")
                except Exception:
                    due_str = "Scheduled"
                date_label = "Next Alert"
            else:
                due_str = "—"
                date_label = "Date"

            rem_list = []
            seen_alerts = set()
            for it in items:
                try:
                    f_dt = parse(it["fire_at"]).strftime("%b %d")
                    kind_label = (
                        "Due date"
                        if it["kind"] == "due"
                        else ("Overdue alert" if it["kind"] == "overdue" else "Early alert")
                    )
                    alert_str = f"{f_dt} ({kind_label})"
                    if alert_str not in seen_alerts:
                        seen_alerts.add(alert_str)
                        rem_list.append(alert_str)
                except Exception:
                    val = it["fire_at"][:10]
                    if val not in seen_alerts:
                        seen_alerts.add(val)
                        rem_list.append(val)

            rem_display = ", ".join(rem_list) if rem_list else "None pending"
            msg_lines.append(_format_geometric_title(raw_title, dot, target_col=26))
            msg_lines.append(f"  🗓️ *{date_label}:* `{due_str}`  •  _{clean_cat}_")
            msg_lines.append(f"  ⏰ *Alerts:* {rem_display}\n")

        msg_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        msg_lines.append("_Legend: 🟢 Active  •  🟡 Expiring Soon  •  🔴 Expired_")
        text_out = "\n".join(msg_lines)

    if edit and isinstance(target, CallbackQuery):
        await target.message.edit_text(text_out, parse_mode="Markdown", reply_markup=kb)
    elif isinstance(target, CallbackQuery):
        await target.message.reply(text_out, parse_mode="Markdown", reply_markup=kb)
    else:
        await target.reply(text_out, parse_mode="Markdown", reply_markup=kb)


async def _render_cancel_reminders_menu(cq: CallbackQuery, user_id: int) -> None:
    """Render list of records with active pending reminders to cancel, showing end dates clearly."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT 
                r.id, 
                r.title, 
                r.category, 
                r.key_date,
                rem.kind,
                rem.fire_at
            FROM records r
            JOIN reminders rem ON rem.record_id = r.id AND rem.state = 'pending'
            WHERE (r.user_id = ? OR r.id IN (
                SELECT r2.id FROM records r2 
                JOIN access_grants ag ON r2.user_id = ag.owner_id 
                WHERE ag.granted_id = ? AND ag.category = 'all'
            ))
              AND r.status NOT IN ('soft_deleted', 'renewed', 'done')
            ORDER BY r.key_date ASC, rem.fire_at ASC
            """,
            (user_id, user_id)
        ) as cur:
            rows = await cur.fetchall()

    grouped = OrderedDict()
    for row in rows:
        rid = row["id"]
        if rid not in grouped:
            grouped[rid] = {
                "id": rid,
                "title": row["title"],
                "category": row["category"],
                "key_date": row["key_date"],
                "alerts": [],
            }
        f_dt_str = row["fire_at"]
        try:
            f_dt_short = parse(f_dt_str).strftime("%b %d")
        except Exception:
            f_dt_short = f_dt_str[:10]
        k_label = "Due date" if row["kind"] == "due" else ("Overdue" if row["kind"] == "overdue" else "Early")
        alert_item = f"{f_dt_short} ({k_label})"
        if alert_item not in grouped[rid]["alerts"]:
            grouped[rid]["alerts"].append(alert_item)

    if not grouped:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back to Upcoming", callback_data="upcoming_view:3m")]
        ])
        await cq.message.edit_text(
            "🔕 **CANCEL REMINDERS**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "_No documents currently have pending reminders._\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    msg_lines = [
        "🔕 **CANCEL / REMOVE REMINDERS**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Select a document below to cancel all its scheduled reminders:\n"
    ]

    kb_buttons = []
    for r in list(grouped.values())[:10]:
        t_raw = r["title"] or "Untitled"
        kd = r["key_date"]
        if kd:
            try:
                date_str = parse(kd[:10]).strftime("%b %d, %Y")
                btn_date = parse(kd[:10]).strftime("%b %d, '%y")
            except Exception:
                date_str = kd
                btn_date = kd
        else:
            date_str = "None"
            btn_date = "No End Date"

        alerts_summary = ", ".join(r["alerts"][:3]) if r["alerts"] else "None"
        msg_lines.append(f"• *{_escape_md(t_raw)}*")
        msg_lines.append(f"  ⏳ *End Date:* `{date_str}`")
        msg_lines.append(f"  ⏰ *Pending Alerts:* {alerts_summary}\n")

        # Format button text with title and clear end date
        t_short = t_raw[:14] + "…" if len(t_raw) > 14 else t_raw
        kb_buttons.append([
            InlineKeyboardButton(
                text=f"🔕 {t_short} (End: {btn_date})",
                callback_data=f"upcoming_do_cancel:{r['id']}"
            )
        ])

    msg_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    msg_lines.append("_Tap a button below to cancel that document's reminders:_")

    kb_buttons.append([InlineKeyboardButton(text="⬅️ Back to Upcoming", callback_data="upcoming_view:3m")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    await cq.message.edit_text(
        "\n".join(msg_lines),
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "upcoming_view:all")
async def on_upcoming_view_all(cq: CallbackQuery) -> None:
    await cq.answer()
    await _render_upcoming(cq, cq.from_user.id, months_limit=None, edit=True)


@router.callback_query(F.data == "upcoming_view:3m")
async def on_upcoming_view_3m(cq: CallbackQuery) -> None:
    await cq.answer()
    await _render_upcoming(cq, cq.from_user.id, months_limit=3, edit=True)


@router.callback_query(F.data == "upcoming_cancel_menu")
async def on_upcoming_cancel_menu(cq: CallbackQuery) -> None:
    await cq.answer()
    await _render_cancel_reminders_menu(cq, cq.from_user.id)


@router.callback_query(F.data.startswith("upcoming_do_cancel:"))
async def on_upcoming_do_cancel(cq: CallbackQuery) -> None:
    record_id = cq.data.split(":", 1)[1]
    async with get_db() as db:
        await db.execute(
            "UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'",
            (record_id, cq.from_user.id)
        )
        await db.commit()
    await cq.answer("🔕 Reminders cancelled for this document.", show_alert=True)
    await _render_upcoming(cq, cq.from_user.id, months_limit=3, edit=True)


