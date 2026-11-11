"""Search handler with clickable inline results."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter

from squares_vault.search.index import search_records
from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

router = Router(name="search")


from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

class SearchState(StatesGroup):
    query = State()

@router.message(Command("search"))
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


async def cmd_search(message: Message, state: FSMContext) -> None:
    """Prompt user for a search query."""
    await message.reply("🔍 What are you looking for? (e.g. 'Car RC', 'Passport')")
    await state.set_state(SearchState.query)


@router.message(F.text & ~F.text.startswith("/"), SearchState.query)
async def handle_search_query(message: Message, state: FSMContext) -> None:
    """Execute a search query on text messages and show clickable results."""
    query = message.text.strip()
    
    # If they pressed another menu button, clear state and dispatch that menu directly
    if any(btn in query for btn in ["➕ Add", "🔍 Search", "🔍 Find", "📁 Browse", "💬 Ask Your Vault", "📅 Upcoming", "⚙️ Settings"]):
        await state.clear()
        from squares_vault.bot.handlers.base import handle_menu_buttons
        await handle_menu_buttons(message, state)
        return

    if len(query) < 2:
        await message.reply("⚠️ Please enter at least 2 characters to search.")
        return

    await state.clear()

    text, kb = await _render_search_page(message.from_user.id, query, 0)
    if kb:
        try:
            await message.reply(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await message.reply(text, reply_markup=kb)
    else:
        await message.reply(text)


_SEARCH_CACHE: dict[str, str] = {}


async def _render_search_page(user_id: int, query: str, page: int = 0):
    import math
    import hashlib
    from datetime import datetime, timezone, timedelta
    
    results = await search_records(user_id, query, limit=50)
    if not results:
        return "❌ No records found.", None

    # Alphabetical order
    results = sorted(results, key=lambda x: (x.get("title") or "").lower())

    PAGE_SIZE = 8
    total_count = len(results)
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    page_items = results[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    today = datetime.now(timezone.utc).date()

    q_id = hashlib.md5(query.strip().lower().encode()).hexdigest()[:8]
    _SEARCH_CACHE[q_id] = query

    kb_buttons = []
    for idx, r in enumerate(page_items, 1):
        num = page * PAGE_SIZE + idx
        dot = "📄"
        if r.get("key_date"):
            try:
                kd = datetime.strptime(r["key_date"][:10], "%Y-%m-%d").date()
                if kd < today:
                    dot = "🔴"
                elif kd <= today + timedelta(days=30):
                    dot = "🟡"
                else:
                    dot = "🟢"
            except Exception:
                dot = "📄"
        title_full = r['title']
        max_len = 22
        title_btn = title_full if len(title_full) <= max_len else title_full[:max_len - 2] + "…"
        btn_text = f"{num:02d}. {title_btn} ── {dot}"
        kb_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_record:{r['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"search_pg:{q_id}:{page-1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"search_pg:{q_id}:{page+1}"))
    if nav_row:
        kb_buttons.append(nav_row)

    kb_buttons.append([
        InlineKeyboardButton(text="🔍 New Search", callback_data="search_new"),
        InlineKeyboardButton(text="⬅️ Back", callback_data="settings_close")
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    query_esc = _escape_md(query)
    text = (
        f"🔍 **SEARCH RESULTS** • _{query_esc}_ (Page {page + 1}/{total_pages})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Tap a document below to view details:_\n\n"
        f"_Legend: 🟢 Active  •  🟡 Expiring Soon  •  🔴 Expired  •  📄 General_"
    )
    return text, kb


@router.callback_query(F.data == "search_new")
async def on_search_new(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.message.answer("🔍 What are you looking for? (e.g. 'Car RC', 'Passport')")
    await state.set_state(SearchState.query)
    await cq.answer()


@router.callback_query(F.data.startswith("search_pg:"))
async def on_search_page_change(cq: CallbackQuery) -> None:
    parts = cq.data.split(":")
    q_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    
    query = _SEARCH_CACHE.get(q_id, "")
    if not query:
        await cq.answer("Search expired. Please search again.")
        return

    text, kb = await _render_search_page(cq.from_user.id, query, page)
    if kb:
        try:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


async def get_document_lineage(record_id: str, user_id: int) -> list[dict]:
    """Recursively traverse the parent/child renewal graph to get all versions chronologically."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM records 
            WHERE id = ? AND (
                user_id = ? 
                OR id IN (
                    SELECT r2.id FROM records r2 
                    JOIN access_grants ag ON r2.user_id = ag.owner_id 
                    WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                )
            ) AND status != 'soft_deleted'
            """,
            (record_id, user_id, user_id)
        ) as cur:
            curr = await cur.fetchone()

        if not curr:
            return []

        curr = dict(curr)
        visited_ids = {curr["id"]}
        ancestors = []

        # Walk up parent chain
        pid = curr.get("parent_record_id")
        while pid and pid not in visited_ids:
            visited_ids.add(pid)
            async with db.execute(
                """
                SELECT * FROM records 
                WHERE id = ? AND (
                    user_id = ? 
                    OR id IN (
                        SELECT r2.id FROM records r2 
                        JOIN access_grants ag ON r2.user_id = ag.owner_id 
                        WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                    )
                ) AND status != 'soft_deleted'
                """,
                (pid, user_id, user_id)
            ) as cur:
                p_row = await cur.fetchone()
            if not p_row:
                break
            p_dict = dict(p_row)
            ancestors.append(p_dict)
            pid = p_dict.get("parent_record_id")

        ancestors.reverse()  # Oldest ancestor first

        # Walk down child chain
        descendants = []
        queue = [curr["id"]]
        while queue:
            parent = queue.pop(0)
            async with db.execute(
                """
                SELECT * FROM records 
                WHERE parent_record_id = ? AND (
                    user_id = ? 
                    OR id IN (
                        SELECT r2.id FROM records r2 
                        JOIN access_grants ag ON r2.user_id = ag.owner_id 
                        WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                    )
                ) AND status != 'soft_deleted'
                ORDER BY created_at ASC
                """,
                (parent, user_id, user_id)
            ) as cur:
                c_rows = await cur.fetchall()
            for cr in c_rows:
                cd = dict(cr)
                if cd["id"] not in visited_ids:
                    visited_ids.add(cd["id"])
                    descendants.append(cd)
                    queue.append(cd["id"])

        full_chain = ancestors + [curr] + descendants
        full_chain.sort(key=lambda r: str(r.get("key_date") or r.get("created_at") or ""))
        return full_chain


def render_version_timeline_text(lineage: list[dict], current_record_id: str) -> str:
    """Render a visual ASCII/Unicode timeline with cost deltas and version lineage."""
    if not lineage:
        return "No version history found."

    lines = [
        "📈 **DOCUMENT VERSION TIMELINE**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Tracking **{len(lineage)}** sequential version{'s' if len(lineage) > 1 else ''} across renewal history:\n",
    ]

    prev_amt = None
    first_amt = None

    for idx, rec in enumerate(lineage, 1):
        rid = rec["id"]
        title = rec["title"]
        status = rec.get("status", "active")
        amt_minor = rec.get("amount_minor")
        dt_str = rec.get("key_date") or (rec.get("created_at")[:10] if rec.get("created_at") else "—")

        year_str = dt_str[:4] if dt_str and len(dt_str) >= 4 else "—"

        is_current = (rid == current_record_id)
        marker = "⭐ Current Version" if is_current else (f"🟢 {status.title()}" if status == "active" else "⚪ Archived")

        amt_display = f"₹{amt_minor/100:,.2f}" if amt_minor else "—"

        delta_str = ""
        if amt_minor and prev_amt is not None and prev_amt > 0:
            diff = amt_minor - prev_amt
            pct = (diff / prev_amt) * 100
            sign = "+" if diff > 0 else ""
            delta_str = f" ({sign}₹{diff/100:,.2f} / {sign}{pct:.1f}%)"

        if first_amt is None and amt_minor:
            first_amt = amt_minor
        if amt_minor:
            prev_amt = amt_minor

        connector = "┌─" if idx == 1 else ("└─" if idx == len(lineage) else "├─")

        lines.append(f"{connector} **v{idx} ({year_str}): {title}**")
        lines.append(f"   🗓️ Date: `{dt_str}`  •  💰 `{amt_display}`{delta_str}")
        lines.append(f"   Status: {marker}")
        if idx < len(lineage):
            lines.append("   │")

    if len(lineage) > 1 and first_amt and prev_amt and first_amt != prev_amt:
        total_diff = prev_amt - first_amt
        total_pct = (total_diff / first_amt) * 100
        sign = "+" if total_diff > 0 else ""
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📊 **Cumulative Cost Delta:** {sign}₹{total_diff/100:,.2f} ({sign}{total_pct:.1f}%) across all renewals.")

    return "\n".join(lines)


@router.callback_query(F.data.startswith("view_record:"))
async def on_view_record(cq: CallbackQuery, state: FSMContext = None) -> None:
    """Show full record details."""
    parts = cq.data.split(":")
    record_id = parts[1]
    origin = parts[2] if len(parts) > 2 else "search"

    if state and origin != "ask":
        await state.clear()

    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM records 
            WHERE id = ? AND (
                user_id = ? 
                OR id IN (
                    SELECT r2.id FROM records r2 
                    JOIN access_grants ag ON r2.user_id = ag.owner_id 
                    WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                )
            ) AND status != 'soft_deleted'
            """,
            (record_id, cq.from_user.id, cq.from_user.id)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        await cq.answer("Record not found.", show_alert=True)
        return

    row = dict(row)

    def _fmt(iso):
        if not iso:
            return "—"
        try:
            from datetime import date
            d = date.fromisoformat(iso[:10])
            return d.strftime("%d-%m-%Y")
        except Exception:
            return iso

    amt = f"₹{row['amount_minor']/100:.2f}" if row.get('amount_minor') else "—"
    lines = [
        "📄 **DOCUMENT DETAILS**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📁 **Title:** {row['title']}",
        f"🏷️ **Category:** {row['category'].title()}",
        f"💰 **Amount:** {amt}",
        f"📅 **Key Date:** {_fmt(row.get('key_date'))}",
        f"📌 **Date Type:** {(row.get('key_date_type') or '—').title()}",
    ]
    if row.get('notes'):
        lines += [f"📝 **Notes:** {row['notes']}"]

    import json
    if row.get('extra_json'):
        try:
            extras = json.loads(row['extra_json'])
            if extras:
                for k, v in extras.items():
                    if v:
                        lines.append(f"• **{k.replace('_', ' ').title()}:** {v}")
        except Exception:
            pass

    lines += [
        f"🕒 **Added:** {_fmt(row.get('created_at')[:10] if row.get('created_at') else None)}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]

    # Fetch renewal chain & full lineage
    lineage = await get_document_lineage(record_id, cq.from_user.id)
    if len(lineage) > 1:
        lines += [f"🔄 *Version Lineage:* {len(lineage)} versions tracked across renewals"]

    parent_id = row.get('parent_record_id') or ""
    async with get_db() as db:
        async with db.execute(
            "SELECT id, title, created_at, status FROM records WHERE (id = ? AND ? != '') OR parent_record_id = ?",
            (parent_id, parent_id, record_id)
        ) as cur:
            chain_rows = await cur.fetchall()

    for cr in chain_rows:
        if cr['id'] == parent_id:
            lines += [f"🔄 *Renewed from:* {cr['title']} ({_fmt(cr['created_at'][:10])})"]
        elif cr['id'] != record_id:
            lines += [f"🔄 *Renewed by:* {cr['title']} ({_fmt(cr['created_at'][:10])})"]

    kb_buttons = []

    # If lineage > 1, add Version Timeline button at the top of actions
    if len(lineage) > 1:
        kb_buttons.append([
            InlineKeyboardButton(
                text=f"📈 Version Timeline ({len(lineage)} Years)",
                callback_data=f"view_timeline:{record_id}:{origin}"
            )
        ])
    
    # Fetch associated files
    async with get_db() as db:
        async with db.execute(
            "SELECT id, drive_file_id, mime FROM files WHERE record_id = ?",
            (record_id,)
        ) as cur:
            file_rows = await cur.fetchall()
            
    is_owner = (row["user_id"] == cq.from_user.id)

    for idx, f_row in enumerate(file_rows, 1):
        fid_db = f_row['id']
        label = "📎 View Attachment" if len(file_rows) == 1 else f"📎 View Attachment {idx}"
        if is_owner:
            del_label = "🗑️ Delete File" if len(file_rows) == 1 else f"🗑️ Del {idx}"
            kb_buttons.append([
                InlineKeyboardButton(text=label, callback_data=f"get_file:{fid_db}"),
                InlineKeyboardButton(text=del_label, callback_data=f"del_file_cnf:{fid_db}")
            ])
        else:
            kb_buttons.append([InlineKeyboardButton(text=label, callback_data=f"get_file:{fid_db}")])

    # Check if there are pending reminders for this record
    async with get_db() as db:
        async with db.execute(
            "SELECT count(*) as cnt FROM reminders WHERE record_id = ? AND user_id = ? AND state = 'pending'",
            (record_id, cq.from_user.id)
        ) as cur:
            rem_row = await cur.fetchone()
            has_pending_reminders = rem_row["cnt"] > 0 if rem_row else False

    if is_owner:
        kb_buttons.append([
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit_record:{record_id}"),
            InlineKeyboardButton(text="🔄 Renew", callback_data=f"renew_record:{record_id}"),
            InlineKeyboardButton(text="🗑️ Delete", callback_data=f"del_record:{record_id}")
        ])
        if has_pending_reminders:
            kb_buttons.append([InlineKeyboardButton(text="🔕 Cancel Reminders", callback_data=f"cancel_record_rem:{record_id}:{origin}")])
    else:
        lines.insert(2, f"👥 _Shared via Family Access by Member `{row['user_id']}`_")

    if origin == "ask":
        kb_buttons.append([InlineKeyboardButton(text="⬅️ Back to Chat", callback_data="ask_vault_return")])
    elif origin == "upcoming":
        kb_buttons.append([InlineKeyboardButton(text="⬅️ Back to Upcoming", callback_data="menu_upcoming")])
    else:
        kb_buttons.append([InlineKeyboardButton(text="⬅️ Back to results", callback_data="search_back")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    try:
        await cq.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await cq.message.edit_text("\n".join(lines), reply_markup=kb)
    try:
        await cq.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("cancel_record_rem:"))
async def on_cancel_record_rem(cq: CallbackQuery, state: FSMContext = None) -> None:
    """Cancel all pending reminders for a specific record."""
    parts = cq.data.split(":")
    record_id = parts[1]
    origin = parts[2] if len(parts) > 2 else "search"

    async with get_db() as db:
        await db.execute(
            "UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'",
            (record_id, cq.from_user.id)
        )
        await db.commit()
    await cq.answer("🔕 Reminders cancelled for this document.", show_alert=True)
    cq.data = f"view_record:{record_id}:{origin}"
    await on_view_record(cq, state)


@router.callback_query(F.data.startswith("renew_record:"))
async def on_renew_record(cq: CallbackQuery, state: FSMContext) -> None:
    """Start renewal flow for an existing record."""
    record_id = cq.data.split(":", 1)[1]
    from squares_vault.bot.handlers.manual import start_renewal_flow
    await start_renewal_flow(cq, record_id, state)

@router.callback_query(F.data.startswith("get_file:"))
async def on_get_file(cq: CallbackQuery) -> None:
    file_db_id = cq.data.split(":", 1)[1]
    
    async with get_db() as db:
        async with db.execute(
            """
            SELECT f.drive_file_id, f.mime, f.coded_name, r.title, r.user_id as owner_user_id 
            FROM files f 
            JOIN records r ON f.record_id = r.id 
            WHERE f.id = ? AND (
                r.user_id = ? 
                OR r.id IN (
                    SELECT r2.id FROM records r2 
                    JOIN access_grants ag ON r2.user_id = ag.owner_id 
                    WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                )
            )
            """,
            (file_db_id, cq.from_user.id, cq.from_user.id)
        ) as cur:
            row = await cur.fetchone()
            
    if not row:
        await cq.answer("File not found in database.", show_alert=True)
        return
        
    file_id = row['drive_file_id']
    if len(file_id) > 45:
        # Telegram file ID
        try:
            tg_file = await cq.bot.get_file(file_id)
            f_bytes = await cq.bot.download_file(tg_file.file_path)
            raw = f_bytes.getvalue()
            ext = ".jpg"
            if raw[:4] == b"%PDF":
                ext = ".pdf"
            elif raw[:8] == b"\x89PNG\r\n\x1a\n":
                ext = ".png"
            elif "pdf" in (row['mime'] or "").lower():
                ext = ".pdf"
            elif "png" in (row['mime'] or "").lower():
                ext = ".png"
            safe_title = "".join(c for c in row['title'] if c.isalnum() or c in " _-").strip()
            filename = f"{safe_title}{ext}" if safe_title else f"Document{ext}"
            from aiogram.types import BufferedInputFile
            await cq.message.answer_document(
                BufferedInputFile(raw, filename=filename),
                caption="Here is your requested attachment."
            )
        except Exception:
            try:
                await cq.message.answer_document(file_id)
            except Exception:
                await cq.message.answer_photo(file_id)
        await cq.answer()
    else:
        # Google Drive ID
        from squares_vault.drive.api import DriveAPI, DriveAuthError
        from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
        msg = await cq.message.answer("⏳ Fetching securely from Google Drive...")
        owner_id = row["owner_user_id"]
        try:
            drive = DriveAPI(owner_id)
            file_bytes = await drive.download_file(file_id)

            ext = ".jpg"
            if file_bytes[:4] == b"%PDF":
                ext = ".pdf"
            elif file_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                ext = ".png"
            elif file_bytes[:3] == b"\xff\xd8\xff":
                ext = ".jpg"
            elif file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP":
                ext = ".webp"
            elif "pdf" in (row['mime'] or "").lower():
                ext = ".pdf"
            elif "png" in (row['mime'] or "").lower():
                ext = ".png"
            elif row.get("coded_name") and "." in row["coded_name"]:
                ext = "." + row["coded_name"].rsplit(".", 1)[-1].lower()

            safe_title = "".join(c for c in row['title'] if c.isalnum() or c in " _-").strip()
            filename = f"{safe_title}{ext}" if safe_title else f"Document{ext}"

            await cq.message.answer_document(
                BufferedInputFile(file_bytes, filename=filename),
                caption="📎 Here is your requested attachment."
            )
            await msg.delete()
            await cq.answer()

        except DriveAuthError:
            # Google Drive not connected or token expired for document owner
            await msg.delete()
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Reconnect Google Drive", callback_data="drive_reconnect")]
            ])
            await cq.message.answer(
                "⚠️ **Google Drive Connection Required**\n\n"
                "Your Google Drive session has expired or is not connected. "
                "The attachment could not be fetched.\n\n"
                "Tap below to reconnect your Google Drive account to restore access to all your attachments.",
                reply_markup=kb,
                parse_mode="Markdown",
            )
            await cq.answer()
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("Error fetching file")
            try:
                await msg.delete()
            except Exception:
                pass
            await cq.answer(f"Failed to fetch file: {str(e)}", show_alert=True)




@router.callback_query(F.data.startswith("del_record:"))
async def on_delete_record(cq: CallbackQuery) -> None:
    record_id = cq.data.split(":", 1)[1]
    async with get_db() as db:
        async with db.execute("SELECT title FROM records WHERE id = ? AND user_id = ?", (record_id, cq.from_user.id)) as cur:
            row = await cur.fetchone()
    if not row:
        await cq.answer("Record not found.", show_alert=True)
        return
        
    title = row["title"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Yes, Delete", callback_data=f"del_confirm:{record_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"view_record:{record_id}")]
    ])
    await cq.message.edit_text(
        f"⚠️ **DELETE CONFIRMATION**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Are you sure you want to delete:\n"
        f"📄 **{title}**?\n\n"
        f"This will remove the record, cancel its reminders, and delete its file from Google Drive.",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await cq.answer()


@router.callback_query(F.data.startswith("del_confirm:"))
async def on_delete_record_confirm(cq: CallbackQuery) -> None:
    record_id = cq.data.split(":", 1)[1]
    
    # 1. Fetch drive file IDs to delete from Google Drive
    async with get_db() as db:
        async with db.execute("SELECT drive_file_id FROM files WHERE record_id = ? AND user_id = ?", (record_id, cq.from_user.id)) as cur:
            files = await cur.fetchall()
            
    if files:
        from squares_vault.drive.api import DriveAPI
        drive = DriveAPI(cq.from_user.id)
        for f in files:
            try:
                await drive.delete_file(f["drive_file_id"])
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to delete file from drive: {e}")
                
    # 2. Call the master soft_delete_record with correct (user_id, record_id)
    from squares_vault.db.repositories import soft_delete_record
    await soft_delete_record(cq.from_user.id, record_id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back to Categories", callback_data="search_back")]])
    await cq.message.edit_text("🗑️ Record deleted and files removed from Google Drive.", reply_markup=kb)
    await cq.answer("Deleted")


@router.callback_query(F.data.startswith("del_file_cnf:"))
async def on_delete_file_confirm(cq: CallbackQuery) -> None:
    """Prompt user for confirmation before deleting a single attached file."""
    file_id = cq.data.split(":")[1]

    async with get_db() as db:
        async with db.execute(
            """
            SELECT f.id, f.record_id, f.coded_name, f.drive_file_id, r.title, r.user_id 
            FROM files f
            JOIN records r ON f.record_id = r.id
            WHERE f.id = ? AND r.user_id = ?
            """,
            (file_id, cq.from_user.id)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        await cq.answer("File or record not found.", show_alert=True)
        return

    record_id = row["record_id"]
    filename = row["coded_name"] or "Attachment"
    title = row["title"]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Yes, Delete File", callback_data=f"del_file_exe:{file_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"view_record:{record_id}")]
    ])
    await cq.message.edit_text(
        f"⚠️ **DELETE FILE CONFIRMATION**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Are you sure you want to delete this file attachment?\n\n"
        f"📄 **Document:** {title}\n"
        f"📎 **File:** `{filename}`\n\n"
        f"_This will permanently delete the file from your Google Drive and vault. The document record itself will remain intact._",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await cq.answer()


@router.callback_query(F.data.startswith("del_file_exe:"))
async def on_delete_file_exec(cq: CallbackQuery, state: FSMContext = None) -> None:
    """Execute deletion of a single attached file from Google Drive and database."""
    file_id = cq.data.split(":")[1]

    async with get_db() as db:
        async with db.execute(
            """
            SELECT f.id, f.record_id, f.drive_file_id, r.user_id 
            FROM files f
            JOIN records r ON f.record_id = r.id
            WHERE f.id = ? AND r.user_id = ?
            """,
            (file_id, cq.from_user.id)
        ) as cur:
            row = await cur.fetchone()

    if not row:
        await cq.answer("File not found or permission denied.", show_alert=True)
        return

    record_id = row["record_id"]
    drive_file_id = row["drive_file_id"]

    # Delete from Google Drive if valid Drive ID
    if drive_file_id:
        try:
            from squares_vault.drive.api import DriveAPI
            drive = DriveAPI(cq.from_user.id)
            await drive.delete_file(drive_file_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to delete file from drive: {e}")

    # Delete from files table
    async with get_db() as db:
        await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await db.commit()

    await cq.answer("🗑️ File attachment permanently deleted.", show_alert=True)
    cq.data = f"view_record:{record_id}"
    await on_view_record(cq, state)


@router.callback_query(F.data.startswith("view_timeline:"))
async def on_view_timeline(cq: CallbackQuery, state: FSMContext = None) -> None:
    """Render the full interactive version timeline graph."""
    parts = cq.data.split(":")
    record_id = parts[1]
    origin = parts[2] if len(parts) > 2 else "search"

    lineage = await get_document_lineage(record_id, cq.from_user.id)
    if not lineage:
        await cq.answer("Version lineage not found.", show_alert=True)
        return

    text = render_version_timeline_text(lineage, current_record_id=record_id)

    kb_buttons = []
    # Add jump buttons for other versions in the lineage
    for idx, rec in enumerate(lineage, 1):
        if rec["id"] != record_id:
            year_tag = (rec.get("key_date") or rec.get("created_at") or "")[:4]
            btn_label = f"📄 View {year_tag} ({rec['title'][:16]})"
            kb_buttons.append([InlineKeyboardButton(text=btn_label, callback_data=f"view_record:{rec['id']}:{origin}")])

    kb_buttons.append([InlineKeyboardButton(text="⬅️ Back to Document", callback_data=f"view_record:{record_id}:{origin}")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await cq.answer()


@router.callback_query(F.data == "search_back")
async def on_search_back(cq: CallbackQuery, state: FSMContext = None) -> None:
    """Return to category browse list."""
    if state:
        await state.clear()
    from squares_vault.db.connection import get_db
    user_id = cq.from_user.id
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
            (user_id, user_id)
        ) as cur:
            rows = await cur.fetchall()
            
    kb_buttons = []
    row_buttons = []
    for row in rows:
        cat_name = row['category']
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
    await cq.message.edit_text("📁 **Browse by Category**", reply_markup=kb, parse_mode="Markdown")
    await cq.answer()


@router.callback_query(F.data.startswith("browse_cat:"))
async def on_browse_category(cq: CallbackQuery) -> None:
    parts = cq.data.split(":")
    cat = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    PAGE_SIZE = 8

    async with get_db() as db:
        if cat == "all":
            async with db.execute(
                """
                SELECT COUNT(*) as total FROM records 
                WHERE (user_id = ? OR id IN (
                    SELECT r2.id FROM records r2 
                    JOIN access_grants ag ON r2.user_id = ag.owner_id 
                    WHERE ag.granted_id = ? AND ag.category = 'all'
                )) AND status != 'soft_deleted'
                """,
                (cq.from_user.id, cq.from_user.id)
            ) as cur:
                total_count = (await cur.fetchone())["total"]
                
            async with db.execute(
                """
                SELECT id, category, title, key_date FROM records 
                WHERE (user_id = ? OR id IN (
                    SELECT r2.id FROM records r2 
                    JOIN access_grants ag ON r2.user_id = ag.owner_id 
                    WHERE ag.granted_id = ? AND ag.category = 'all'
                )) AND status != 'soft_deleted' 
                ORDER BY LOWER(title) ASC LIMIT ? OFFSET ?
                """,
                (cq.from_user.id, cq.from_user.id, PAGE_SIZE, page * PAGE_SIZE)
            ) as cur:
                rows = await cur.fetchall()
            cat_display = "All Documents"
        else:
            async with db.execute(
                """
                SELECT COUNT(*) as total FROM records 
                WHERE (
                    (user_id = ? AND category LIKE ?) 
                    OR id IN (
                        SELECT r2.id FROM records r2 
                        JOIN access_grants ag ON r2.user_id = ag.owner_id 
                        WHERE ag.granted_id = ? AND (ag.category = 'all' OR ag.category LIKE ? OR r2.category LIKE '%' || ag.category || '%')
                    )
                ) AND status != 'soft_deleted'
                """,
                (cq.from_user.id, f"%{cat}%", cq.from_user.id, f"%{cat}%")
            ) as cur:
                total_count = (await cur.fetchone())["total"]
                
            async with db.execute(
                """
                SELECT id, category, title, key_date FROM records 
                WHERE (
                    (user_id = ? AND category LIKE ?) 
                    OR id IN (
                        SELECT r2.id FROM records r2 
                        JOIN access_grants ag ON r2.user_id = ag.owner_id 
                        WHERE ag.granted_id = ? AND (ag.category = 'all' OR ag.category LIKE ? OR r2.category LIKE '%' || ag.category || '%')
                    )
                ) AND status != 'soft_deleted' 
                ORDER BY LOWER(title) ASC LIMIT ? OFFSET ?
                """,
                (cq.from_user.id, f"%{cat}%", cq.from_user.id, f"%{cat}%", PAGE_SIZE, page * PAGE_SIZE)
            ) as cur:
                rows = await cur.fetchall()
            cat_display = cat.title()
            
    if not rows and page == 0:
        await cq.message.edit_text(
            f"📁 No records found in {cat_display}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="search_back")]])
        )
        await cq.answer()
        return

    import math
    from datetime import datetime, timezone, timedelta
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    today = datetime.now(timezone.utc).date()

    kb_buttons = []
    for idx, row in enumerate(rows, 1):
        num = page * PAGE_SIZE + idx
        dot = "📄"
        if row["key_date"]:
            try:
                kd = datetime.strptime(row["key_date"][:10], "%Y-%m-%d").date()
                if kd < today:
                    dot = "🔴"
                elif kd <= today + timedelta(days=30):
                    dot = "🟡"
                else:
                    dot = "🟢"
            except Exception:
                dot = "📄"
        title_full = row['title']
        max_len = 22
        title_btn = title_full if len(title_full) <= max_len else title_full[:max_len - 2] + "…"
        btn_text = f"{num:02d}. {title_btn} ── {dot}"
        kb_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_record:{row['id']}")])
        
    # Pagination controls
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"browse_cat:{cat}:{page - 1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"browse_cat:{cat}:{page + 1}"))
    if nav_row:
        kb_buttons.append(nav_row)

    kb_buttons.append([InlineKeyboardButton(text="⬅️ Back to Categories", callback_data="search_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    cat_esc = _escape_md(cat_display.upper())
    msg_text = (
        f"📁 **{cat_esc}** • Page {page + 1}/{total_pages}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Tap a document below to view details:_\n\n"
        f"_Legend: 🟢 Active  •  🟡 Expiring Soon  •  🔴 Expired  •  📄 General_"
    )
    try:
        await cq.message.edit_text(msg_text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await cq.message.edit_text(msg_text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("edit_record:"))
async def on_edit_record(cq: CallbackQuery, state: FSMContext) -> None:
    record_id = cq.data.split(":")[1]
    from squares_vault.bot.handlers.manual import start_edit_flow
    await start_edit_flow(cq, record_id, state)


@router.callback_query(F.data == "drive_reconnect")
async def on_drive_reconnect(cq: CallbackQuery) -> None:
    """Trigger Google Drive reconnect flow from attachment error prompt."""
    await cq.answer()
    from squares_vault.flows.drive_auth import cmd_connect_drive
    await cmd_connect_drive(cq.message, user_id_override=cq.from_user.id)
