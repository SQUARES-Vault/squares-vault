"""'Ask Your Vault' & Intelligent Assistant handler.

Provides an isolated, state-driven natural language assistant:
- User taps '💬 Ask Your Vault' or uses /ask to enter chat mode.
- Users can ask arbitrary dynamic questions like:
  - "What is due today" (Immediate deadline check)
  - "What is due between 1 jan 2027 and 31 dec 2027" (Dynamic date filtering)
  - "Give me all data related to my VENUE car" (Entity dossiers)
  - "How much did I spend on vehicle servicing?" (Financial synthesis)
- Gemini evaluates the query against the user's active records and TODAY'S DATE.
- Responses are aesthetic and clean: formatted bullets, bold titles, no raw markdown artifacts.
- Persistent menu buttons (Upcoming, Add, Search, Browse, Settings) immediately interrupt
  and trigger their destination without requiring manual "End Chat".
- Clean uncluttered UI: a single [📎 View Documents (N)] button expands on demand.
- Full context persistence: viewing a document offers [⬅️ Back to Chat] which
  smoothly returns to the chat session without breaking state.
- Every bot response includes an inline [🚪 End Chat] button.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from squares_vault.bot.states import AskVault
from squares_vault.bot.keyboards.main import get_main_keyboard
from squares_vault.ai.gemini import answer_vault_query
from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

router = Router(name="ask_vault")

# Common stop words to ignore when extracting entity keywords
STOP_WORDS = {
    "give", "me", "all", "data", "related", "to", "my", "car", "show", "files",
    "the", "and", "for", "with", "what", "is", "are", "in", "of", "about", "a",
    "an", "details", "documents", "document", "info", "please", "any", "records",
    "between", "from", "when", "does", "which"
}

PLATE_PATTERN = re.compile(
    r"\b([A-Z]{2}\s*[-]?\s*\d{1,2}\s*[-]?\s*[A-Z]{0,3}\s*[-]?\s*\d{4})\b|\b(\d{2}\s*[-]?\s*BH\s*[-]?\s*\d{4}\s*[-]?\s*[A-Z]{1,2})\b",
    re.IGNORECASE,
)


def _expand_tokens_for_cross_records(records: list[dict], substantive_tokens: list[str]) -> set[str]:
    """Find associated vehicle plates and entity aliases to connect cross-document relationships."""
    expanded = set(substantive_tokens)

    # 1. Identify which records match any substantive token directly
    matched_recs = []
    for r in records:
        text = f"{r.get('title') or ''} {r.get('entity_name') or ''} {r.get('notes') or ''} {r.get('extra_json') or ''}".lower()
        if any(tok in text for tok in substantive_tokens):
            matched_recs.append(r)

    # 2. Extract registration plates and identifiers from directly matched records
    discovered_plates = set()
    for r in matched_recs:
        combined = f"{r.get('title') or ''} {r.get('notes') or ''} {r.get('extra_json') or ''}"
        for match_tuple in PLATE_PATTERN.findall(combined):
            match_str = match_tuple[0] or match_tuple[1]
            norm = re.sub(r"[\s\-]", "", match_str).upper()
            if len(norm) >= 7:
                discovered_plates.add(norm)
                # Also add the last 4 digits (e.g. 3113)
                discovered_plates.add(norm[-4:])

    for plate in discovered_plates:
        expanded.add(plate.lower())

    # 3. If query itself directly contained a plate number or 4-digit plate number,
    # find records matching that plate to extract entity name or vehicle title
    for tok in substantive_tokens:
        clean_tok = re.sub(r"[\s\-]", "", tok).upper()
        if len(clean_tok) >= 7 or (len(tok) == 4 and tok.isdigit()):
            for r in records:
                combined = f"{r.get('title') or ''} {r.get('notes') or ''} {r.get('extra_json') or ''}".upper()
                if clean_tok in combined or (tok in combined and tok.isdigit()):
                    if r.get("entity_name"):
                        expanded.add(r["entity_name"].lower())
                    for w in re.findall(r"\w+", r.get("title", "")):
                        if len(w) > 3 and w.lower() not in STOP_WORDS:
                            expanded.add(w.lower())

    return expanded


def _get_end_chat_kb(extra_buttons: Optional[list[list[InlineKeyboardButton]]] = None) -> InlineKeyboardMarkup:
    """Return an inline keyboard with optional action buttons and persistent End Chat."""
    rows = list(extra_buttons) if extra_buttons else []
    rows.append([InlineKeyboardButton(text="🚪 End Chat", callback_data="ask_vault_exit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_date(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "No Date"
    try:
        d = datetime.strptime(iso_str[:10], "%Y-%m-%d").date()
        return d.strftime("%d-%m-%Y")
    except Exception:
        return iso_str


def _fmt_date_match(m: re.Match) -> str:
    iso = m.group(1)
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return f"📅 {d.strftime('%d %b %Y')}"
    except Exception:
        return m.group(0)


def _beautify_answer(text: str) -> str:
    """Clean markdown artifacts, format vehicle codes, and format bullet cards elegantly."""
    # 1. Remove (ID: ...) citation tags first
    cleaned = re.sub(r"\s*\(ID:\s*[^)]+\)", "", text).strip()

    # 2. Monospace license plates in prose (e.g. UP15EB3113 -> `UP15EB3113`)
    def plate_repl(m: re.Match) -> str:
        full = m.group(0).strip()
        start = m.start()
        if start > 0 and text[start - 1] == "`":
            return full
        clean_plate = re.sub(r"[\s\-]", "", full).upper()
        return f"`{clean_plate}`"

    formatted_paragraphs = []
    for para in cleaned.split("\n\n"):
        if not para.strip().startswith(("•", "-", "*")):
            para = PLATE_PATTERN.sub(plate_repl, para)
        formatted_paragraphs.append(para)
    cleaned = "\n\n".join(formatted_paragraphs)

    lines = []
    for line in cleaned.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        # Drop standalone horizontal rules
        if re.match(r"^[\s_\-*─━=—–]+$", stripped):
            continue

        # Check if line is a bullet item
        bullet_match = re.match(r"^([•\-\*]\s*)(.*)$", stripped)
        if bullet_match:
            _, content = bullet_match.group(1), bullet_match.group(2)
            meta_split = re.split(r"\s+[—–-]\s+(?=[📅🗓️💰🏷️])|\s+(?=[📅🗓️]\s*\d{4})", content, maxsplit=1)

            # Separate previous card with blank line for breathing room
            if lines and lines[-1].strip() and not lines[-1].endswith(":"):
                lines.append("")

            if len(meta_split) == 2:
                title_part, meta_part = meta_split[0].strip(), meta_split[1].strip()
                title_clean = title_part.strip("*_ ")
                # Format ISO dates to clean DD Mon YYYY: e.g. 2026-01-05 -> 05 Jan 2026
                meta_clean = re.sub(r"📅\s*(\d{4}-\d{2}-\d{2})", _fmt_date_match, meta_part)
                # Clean meta separators (replace | with •)
                meta_clean = re.sub(r"\s*\|\s*", "  •  ", meta_clean)
                # Capitalize category if present: e.g. 🏷️ vehicle -> 🏷️ Vehicle
                meta_clean = re.sub(r"🏷️\s*([a-z]+)", lambda m: f"🏷️ {m.group(1).title()}", meta_clean)

                lines.append(f"• **{title_clean}**")
                lines.append(f"  {meta_clean}")
                continue
            else:
                title_clean = content.strip("*_ ")
                lines.append(f"• **{title_clean}**")
                continue

        lines.append(line)

    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


def _format_assistant_msg(content: str) -> str:
    """Format AI answer card with geometric divider and clean footer."""
    return (
        f"🤖 **Vault Assistant**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{content}\n\n"
        f"💬 _Ask another question below, or tap End Chat._"
    )


@router.message(F.text.contains("Ask Your Vault"))
@router.message(F.text.contains("Ask Vault"))
@router.message(Command("ask"))
async def cmd_ask_vault_start(message: Message, state: FSMContext) -> None:
    """Enter Ask Your Vault interactive chat mode."""
    from squares_vault.bot.handlers.admin import get_system_setting
    if (await get_system_setting("ai_enabled", "1")) != "1":
        await message.answer(
            "⏸️ **Vault AI Assistant is Temporarily Paused**\n\n"
            "The AI assistant is currently paused by the administrator for maintenance. "
            "Your stored records remain completely secure.\n\n"
            "You can still access and view all your documents using 🔍 **Search** or 📁 **Browse**.",
            parse_mode="Markdown",
        )
        return

    await state.set_state(AskVault.chatting)
    kb = _get_end_chat_kb()

    text = (
        "💬 **Ask Your Vault**\n"
        "──────────────────\n"
        "I am connected to your personal documents, warranties, and expenses. Ask me anything!\n\n"
        "**Try asking:**\n"
        "• _\"What is due today?\"_\n"
        "• _\"Give me all data related to my VENUE car\"_\n"
        "• _\"What is due between 1 Jan 2027 and 31 Dec 2027?\"_\n"
        "• _\"What is going to expire in the next 3 months?\"_\n"
        "• _\"How much did I spend on maintenance / bills?\"_\n\n"
        "💬 _Send your question below, or tap **End Chat** to exit._"
    )
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "ask_vault_exit")
async def on_ask_vault_exit(cq: CallbackQuery, state: FSMContext) -> None:
    """Exit Ask Your Vault mode and return to main menu."""
    await state.clear()
    await cq.answer("Chat ended")
    await cq.message.answer(
        "👋 **Chat mode ended.** You are back in the main menu.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


@router.message(AskVault.chatting, F.text)
async def handle_ask_vault_query(message: Message, state: FSMContext) -> None:
    """Process dynamic questions asked while in Ask Your Vault mode."""
    user_id = message.from_user.id
    raw_query = message.text.strip()
    raw_lower = raw_query.lower()

    # 1. Seamless Menu Navigation: If user tapped ANY persistent menu button, clear state & execute destination!
    is_menu_button = (
        raw_query.startswith(("➕", "🔍", "📁", "💬", "📅", "🗓️", "⚙️"))
        or raw_lower in ("upcoming", "add", "search", "find", "browse", "settings", "ask your vault", "ask vault")
        or any(btn in raw_lower for btn in ("menu_add", "menu_upcoming", "menu_search", "menu_browse", "menu_settings"))
    )
    if is_menu_button:
        await state.clear()
        from squares_vault.bot.handlers.base import handle_menu_buttons
        await handle_menu_buttons(message, state)
        return

    # 2. Slash Commands handling
    if raw_query.startswith("/"):
        await state.clear()
        cmd = raw_query.split()[0].lower()
        if cmd == "/upcoming":
            from squares_vault.bot.handlers.base import _render_upcoming
            await _render_upcoming(message, user_id, months_limit=3, edit=False)
            return
        elif cmd in ("/search", "/find"):
            from squares_vault.bot.handlers.search import cmd_search
            await cmd_search(message, state)
            return
        elif cmd in ("/settings", "/browse"):
            from squares_vault.bot.handlers.base import handle_menu_buttons
            await handle_menu_buttons(message, state)
            return
        elif cmd in ("/ask", "/chat"):
            await cmd_ask_vault_start(message, state)
            return
        elif cmd in ("/cancel", "/stop", "/exit"):
            await message.answer(
                "👋 **Chat mode ended.** You are back in the main menu.",
                reply_markup=get_main_keyboard(),
                parse_mode="Markdown",
            )
            return
        elif cmd in ("/start", "/help"):
            from squares_vault.bot.handlers.base import cmd_start, cmd_help
            if cmd == "/start":
                await cmd_start(message)
            else:
                await cmd_help(message)
            return

    from squares_vault.bot.handlers.admin import get_system_setting
    if (await get_system_setting("ai_enabled", "1")) != "1":
        await message.answer(
            "⏸️ **Vault AI Assistant is Temporarily Paused**\n\n"
            "The AI assistant is currently paused by the administrator for maintenance. "
            "Please try again later or use 🔍 **Search** / 📁 **Browse**.",
            reply_markup=_get_end_chat_kb(),
            parse_mode="Markdown",
        )
        return

    await message.bot.send_chat_action(message.chat.id, "typing")

    # Fetch user's active records (including records shared with user)
    async with get_db() as db:
        async with db.execute(
            """
            SELECT r.id, r.title, r.category, r.subtype, r.key_date, r.key_date_type,
                   r.amount_minor, r.notes, r.tags, r.extra_json, e.display_name as entity_name
            FROM records r
            LEFT JOIN entities e ON r.entity_id = e.id
            WHERE (
                r.user_id = ?
                OR r.id IN (
                    SELECT r2.id FROM records r2
                    JOIN access_grants ag ON r2.user_id = ag.owner_id
                    WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                )
            ) AND r.status != 'soft_deleted'
            ORDER BY r.key_date ASC NULLS LAST, r.created_at DESC
            """,
            (user_id, user_id),
        ) as cur:
            records = [dict(row) for row in await cur.fetchall()]

    if not records:
        kb = _get_end_chat_kb()
        await message.answer(
            "📭 Your vault is empty right now. Upload documents or use **➕ Add** to save records first!",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return

    # Unpack extra metadata (vehicle number, provider, entity)
    for r in records:
        extra = {}
        if r.get("extra_json"):
            try:
                extra = json.loads(r["extra_json"])
            except Exception:
                pass
        r["_extra"] = extra
        if not r.get("entity_name") and extra.get("entity_name"):
            r["entity_name"] = extra["entity_name"]
        r["vehicle_number"] = extra.get("vehicle_number")

    record_map = {r["id"]: r for r in records}

    # Score records by relevance to prioritize top matches in the context
    tokens = [re.sub(r"[^\w]", "", w) for w in raw_lower.split()]
    substantive_tokens = [t for t in tokens if t and len(t) > 2 and t not in STOP_WORDS]
    expanded_tokens = _expand_tokens_for_cross_records(records, substantive_tokens)

    scored_records = []
    for r in records:
        title_l = r["title"].lower()
        entity_l = (r.get("entity_name") or "").lower()
        notes_l = (r.get("notes") or "").lower()
        cat_l = r["category"].lower()
        veh_num_l = (r.get("vehicle_number") or "").lower()
        veh_norm = re.sub(r"[\s\-]", "", veh_num_l)

        score = 0
        for tok in substantive_tokens:
            if tok in title_l:
                score += 4
            if tok in entity_l:
                score += 5
            if tok in veh_num_l or (veh_norm and tok in veh_norm):
                score += 5
            if tok in notes_l:
                score += 2
            if tok in cat_l:
                score += 1

        for tok in (expanded_tokens - set(substantive_tokens)):
            if tok in title_l:
                score += 3
            if tok in entity_l:
                score += 4
            if tok in veh_num_l or (veh_norm and tok in veh_norm):
                score += 4
            if tok in notes_l:
                score += 2

        scored_records.append((score, r))

    scored_records.sort(key=lambda x: x[0], reverse=True)
    matching_records = [r for score, r in scored_records if score > 0]

    # Format context for Gemini
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    context_lines = [f"TODAY'S DATE: {today_str}"]

    # Include up to 50 records in context (prioritizing matched records)
    selected_records = [r for _, r in scored_records[:50]]
    for r in selected_records:
        amt = f"₹{r['amount_minor']/100:.2f}" if r.get("amount_minor") else "None"
        dt = f"{r.get('key_date')} ({r.get('key_date_type')})" if r.get("key_date") else "None"
        notes_clean = f" | Notes: {r['notes'][:120]}" if r.get("notes") else ""
        ent_clean = f" | Entity: {r['entity_name']}" if r.get("entity_name") else ""
        veh_clean = f" | Plate: {r['vehicle_number']}" if r.get("vehicle_number") else ""
        context_lines.append(
            f"- ID: {r['id']} | Title: \"{r['title']}\" | Category: {r['category']} | Date: {dt} | Amount: {amt}{notes_clean}{ent_clean}{veh_clean}"
        )

    context_text = "\n".join(context_lines)

    try:
        raw_answer = await answer_vault_query(user_id, raw_query, context_text)
    except Exception as e:
        log.warning("ask_vault_qa_failed", error=str(e))
        if matching_records:
            items_str = "\n".join(f"• **{r['title']}** ({r['category']})" for r in matching_records[:5])
            raw_answer = f"I found the following matching documents in your vault:\n\n{items_str}"
        else:
            raw_answer = "I couldn't process that question right now. Please try again or rephrase."

    # Extract cited record IDs from AI answer
    cited_ids = re.findall(r"ID:\s*([0-9A-Za-z_-]+)", raw_answer)
    # Beautify answer: remove ID tags, fix bullet points, drop solitary horizontal rules
    clean_answer = _beautify_answer(raw_answer)

    valid_cited_ids = [cid for cid in cited_ids if cid in record_map]
    if not valid_cited_ids and matching_records:
        valid_cited_ids = [r["id"] for r in matching_records[:6]]

    # Deduplicate while preserving order
    matched_ids = list(dict.fromkeys(valid_cited_ids))

    # Persist state data for navigation
    await state.update_data(
        ask_last_query=raw_query,
        ask_last_answer=clean_answer,
        ask_matched_ids=matched_ids,
    )

    # Clean UI Keyboard: Only [📎 View Documents (N)] and [🚪 End Chat]
    extra_buttons = []
    if matched_ids:
        extra_buttons.append([
            InlineKeyboardButton(text=f"📎 View Documents ({len(matched_ids)})", callback_data="ask_view_docs")
        ])

    kb = _get_end_chat_kb(extra_buttons)
    response_msg = _format_assistant_msg(clean_answer)

    try:
        await message.answer(response_msg, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        # Fallback if Markdown parsing encounters special characters
        await message.answer(response_msg, reply_markup=kb)


@router.callback_query(F.data == "ask_view_docs")
async def on_ask_view_docs(cq: CallbackQuery, state: FSMContext) -> None:
    """Expand referenced documents into a clean sub-menu with direct view buttons."""
    data = await state.get_data()
    matched_ids = data.get("ask_matched_ids", [])

    if not matched_ids:
        await cq.answer("No linked documents for this answer.", show_alert=True)
        return

    async with get_db() as db:
        placeholders = ",".join("?" for _ in matched_ids)
        async with db.execute(
            f"SELECT id, title, category, key_date FROM records WHERE id IN ({placeholders})",
            matched_ids,
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    row_map = {r["id"]: r for r in rows}
    # Preserve original order
    ordered_records = [row_map[rid] for rid in matched_ids if rid in row_map]

    doc_buttons = []
    for r in ordered_records[:8]:
        title = r["title"][:28]
        doc_buttons.append([
            InlineKeyboardButton(text=f"📄 {title}", callback_data=f"view_record:{r['id']}:ask")
        ])

    # Navigation buttons
    doc_buttons.append([
        InlineKeyboardButton(text="⬅️ Back to Answer", callback_data="ask_back_to_ans"),
        InlineKeyboardButton(text="🚪 End Chat", callback_data="ask_vault_exit"),
    ])

    text = (
        f"📄 **Referenced Documents ({len(ordered_records)})**\n"
        "──────────────────\n"
        "Tap any document below to view its details, attachments, or renew:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=doc_buttons)

    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data == "ask_back_to_ans")
async def on_ask_back_to_ans(cq: CallbackQuery, state: FSMContext) -> None:
    """Collapse document list back to the AI answer."""
    data = await state.get_data()
    clean_answer = data.get("ask_last_answer", "Here was your answer.")
    matched_ids = data.get("ask_matched_ids", [])

    extra_buttons = []
    if matched_ids:
        extra_buttons.append([
            InlineKeyboardButton(text=f"📎 View Documents ({len(matched_ids)})", callback_data="ask_view_docs")
        ])

    kb = _get_end_chat_kb(extra_buttons)
    response_msg = _format_assistant_msg(clean_answer)

    try:
        await cq.message.edit_text(response_msg, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await cq.message.edit_text(response_msg, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data == "ask_vault_return")
async def on_ask_vault_return(cq: CallbackQuery, state: FSMContext) -> None:
    """Return from document view back to Ask Your Vault interactive chat."""
    await state.set_state(AskVault.chatting)
    data = await state.get_data()
    clean_answer = data.get("ask_last_answer")
    matched_ids = data.get("ask_matched_ids", [])

    if clean_answer:
        extra_buttons = []
        if matched_ids:
            extra_buttons.append([
                InlineKeyboardButton(text=f"📎 View Documents ({len(matched_ids)})", callback_data="ask_view_docs")
            ])
        kb = _get_end_chat_kb(extra_buttons)
        text = _format_assistant_msg(clean_answer)
    else:
        kb = _get_end_chat_kb()
        text = (
            "💬 **Ask Your Vault**\n"
            "──────────────────\n"
            "I am connected to your personal documents and expenses. Ask me anything!\n\n"
            "💬 _Send your question below, or tap **End Chat** below to exit._"
        )

    try:
        await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        await cq.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await cq.answer("Back in chat")
