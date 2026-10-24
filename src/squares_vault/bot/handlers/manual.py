import re
from datetime import datetime, date
from typing import Optional

import dateparser
from dateutil.relativedelta import relativedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from aiogram.exceptions import TelegramBadRequest

from squares_vault.db.repositories import create_record
from squares_vault.drive.naming import generate_coded_name
from squares_vault.db.connection import get_db

router = Router(name="manual")


def _escape_md(text: str | None) -> str:
    """Escape Telegram Markdown special characters safely."""
    if not text:
        return ""
    # Standard Markdown special chars: _ * ` [ ]
    return re.sub(r"([_*`\[\]])", r"\\\1", str(text))


async def _safe_send_or_edit(target, text: str, *, edit: bool = False, reply_markup=None, parse_mode="Markdown") -> None:
    """Send or edit message with fallback to plain text if Markdown entity parsing fails."""
    try:
        if edit:
            await target.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await target.reply(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        err_msg = str(e).lower()
        if "can't parse entities" in err_msg or "entity" in err_msg or "can't find end" in err_msg:
            if edit:
                await target.edit_text(text, reply_markup=reply_markup, parse_mode=None)
            else:
                await target.reply(text, reply_markup=reply_markup, parse_mode=None)
        else:
            raise


# ─── States ──────────────────────────────────────────────────────────────────

class ManualEntry(StatesGroup):
    category   = State()
    title      = State()
    amount     = State()
    start_date = State()
    end_date   = State()
    attachment = State()
    notes      = State()
    confirm    = State()


class RenewalEntry(StatesGroup):
    document = State()
    end_date = State()
    amount   = State()
    notes    = State()
    confirm  = State()


class EditEntry(StatesGroup):
    menu            = State()
    edit_title      = State()
    edit_category   = State()
    edit_amount     = State()
    edit_date       = State()
    edit_notes      = State()
    edit_attachment = State()


# ─── Date parser ─────────────────────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

def _parse_smart_date(text: str) -> Optional[str]:
    """Parse many natural date formats into YYYY-MM-DD. Returns None on failure."""
    text = text.strip().lower()
    today = date.today()

    # 1. YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass

    # 2. DD-MM-YYYY or DD/MM/YYYY or DD.MM.YYYY
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass

    # 3. DD-MM-YY
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})", text)
    if m:
        try:
            year = 2000 + int(m.group(3))
            return date(year, int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass

    # 4. "25 sep" or "25 sep 2026" or "25-sep-2026"
    m = re.fullmatch(r"(\d{1,2})[\s\-]([a-z]+)(?:[\s\-](\d{4}))?", text)
    if m:
        month_str = m.group(2)
        if month_str in _MONTHS:
            day = int(m.group(1))
            month = _MONTHS[month_str]
            year = int(m.group(3)) if m.group(3) else today.year
            # If date is in the past by more than 1 month, assume next year
            try:
                d = date(year, month, day)
                return d.isoformat()
            except ValueError:
                pass

    # 5. "sep 25" or "sep 25 2026"
    m = re.fullmatch(r"([a-z]+)[\s\-](\d{1,2})(?:[\s\-](\d{4}))?", text)
    if m:
        month_str = m.group(1)
        if month_str in _MONTHS:
            day = int(m.group(2))
            month = _MONTHS[month_str]
            year = int(m.group(3)) if m.group(3) else today.year
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                pass

    parsed = dateparser.parse(text, settings={'RELATIVE_BASE': datetime.now()})
    if parsed:
        return parsed.date().isoformat()

    return None


def _fmt_date(iso: Optional[str]) -> str:
    """Format YYYY-MM-DD → DD-MM-YYYY for display."""
    if not iso:
        return "None"
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%d-%m-%Y")
    except ValueError:
        return iso


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _summary(data: dict) -> str:
    """Build an aesthetic live draft preview."""
    lines = [
        "🧾 *LIVE DRAFT PREVIEW*",
        "──────────────────────"
    ]
    if data.get("category"):
        lines.append(f"📁 *Category:* {_escape_md(data['category'])}")
    if data.get("title"):
        lines.append(f"📄 *Title:* {_escape_md(data['title'])}")
    if data.get("amount"):
        lines.append(f"💰 *Amount:* ₹{data['amount']/100:.2f}")
    if data.get("start_date"):
        lines.append(f"🗓️ *Start Date:* {_fmt_date(data['start_date'])}")
    if data.get("end_date"):
        lines.append(f"⏳ *End Date:* {_fmt_date(data['end_date'])}")
    elif data.get("no_reminders"):
        lines.append("⏳ *End Date:* — (No reminders needed)")
    if data.get("has_attachment") or data.get("temp_paths") or data.get("all_files"):
        files_cnt = len(data.get("all_files", []))
        if files_cnt > 1:
            lines.append(f"📎 *Attachment:* {files_cnt} files attached")
        else:
            lines.append("📎 *Attachment:* Document attached")
    if data.get("notes"):
        lines.append(f"📝 *Notes:* {_escape_md(data['notes'])}")
    lines.append("──────────────────────")
    return "\n".join(lines)


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")]
    ])


# ─── Step 0: Category ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "add_manual")
@router.callback_query(F.data.startswith("add_manual_fail:"))
@router.callback_query(F.data.startswith("draft:edit:"))
async def on_add_manual(cq: CallbackQuery, state: FSMContext) -> None:
    user_id = cq.from_user.id
    async with get_db() as db:
        async with db.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cur:
            exists = await cur.fetchone()
    if not exists:
        await cq.answer("Please send /start to set up your Vault first.", show_alert=True)
        return

    draft_id = None
    draft_data = {}
    if ":" in cq.data:
        draft_id = cq.data.split(":")[-1]

    await state.clear()
    if draft_id:
        import json
        async with get_db() as db:
            async with db.execute("SELECT ai_json, temp_paths FROM drafts WHERE id = ?", (draft_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    if row["ai_json"]:
                        parsed = json.loads(row["ai_json"])
                        is_end_type = (parsed.get("key_date_type") or "").lower() in {"expiry", "due", "renewal", "end"}
                        start_val = parsed.get("purchase_date") if is_end_type else parsed.get("key_date")
                        end_val = parsed.get("key_date") if is_end_type else None

                        # Maps ai schema fields to manual entry state
                        draft_data = {
                            "category": parsed.get("category"),
                            "title": parsed.get("title"),
                            "amount": parsed.get("amount_minor"),
                            "start_date": start_val,
                            "end_date": end_val,
                            "notes": parsed.get("notes")
                        }

                    if row["temp_paths"]:
                        files_to_attach = []
                        primary_mime = "image/jpeg"
                        try:
                            temp_info = json.loads(row["temp_paths"])
                            if isinstance(temp_info, dict) and "files" in temp_info:
                                files_to_attach = temp_info["files"]
                                primary_mime = temp_info.get("mime", "image/jpeg")
                            elif isinstance(temp_info, dict) and "file_id" in temp_info:
                                primary_mime = temp_info.get("mime", "image/jpeg")
                                files_to_attach = [{"file_id": temp_info["file_id"], "mime": primary_mime}]
                            elif isinstance(temp_info, str):
                                files_to_attach = [{"file_id": temp_info, "mime": "image/jpeg"}]
                        except Exception:
                            files_to_attach = [{"file_id": str(row["temp_paths"]), "mime": "image/jpeg"}]

                        if files_to_attach:
                            draft_data["all_files"] = files_to_attach
                            draft_data["temp_paths"] = files_to_attach[0]["file_id"]
                            draft_data["temp_mime"] = files_to_attach[0].get("mime", primary_mime)
                            draft_data["has_attachment"] = True

                    import logging
                    logging.getLogger(__name__).info(f"Loaded draft_data for edit: {draft_data}")
        await state.update_data(draft_id=draft_id, **draft_data)

    kb_buttons = [
        [InlineKeyboardButton(text="🚗 Vehicle",   callback_data="man_cat_vehicle"),
         InlineKeyboardButton(text="🛒 Purchases", callback_data="man_cat_warranty & purchases")],
        [InlineKeyboardButton(text="👨‍👩‍👧 People",   callback_data="man_cat_people & occasions"),
         InlineKeyboardButton(text="📄 Bills",     callback_data="man_cat_bills & subscriptions")],
        [InlineKeyboardButton(text="🛡️ Insurance", callback_data="man_cat_insurance"),
         InlineKeyboardButton(text="🪪 IDs",        callback_data="man_cat_ids & government docs")],
        [InlineKeyboardButton(text="⚕️ Health",    callback_data="man_cat_health"),
         InlineKeyboardButton(text="🏠 Home",       callback_data="man_cat_home")],
        [InlineKeyboardButton(text="🏷️ Custom",    callback_data="man_cat_custom")]
    ]
    if draft_data.get("category"):
        cat_disp = draft_data["category"]
        kb_buttons.insert(0, [InlineKeyboardButton(text=f"⏭️ Keep: {cat_disp}", callback_data=f"man_cat_{cat_disp}")])
        
    kb_buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    # In case we clicked an inline button from Draft, we don't want to edit the draft message entirely if we can avoid it, but wait: 
    # Draft is a summary. Let's just edit it.
    await cq.message.edit_text("*Step 1/6 – Category*\n\nSelect a category:", reply_markup=kb, parse_mode="Markdown")
    await state.set_state(ManualEntry.category)
    await cq.answer()


# ─── Step 1: Title ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("man_cat_"), ManualEntry.category)
async def on_manual_category(cq: CallbackQuery, state: FSMContext) -> None:
    category = cq.data.replace("man_cat_", "")
    await state.update_data(category=category)
    data = await state.get_data()
    
    import logging
    logging.getLogger(__name__).info(f"on_manual_category state data: {data}")
    
    kb_buttons = []
    text_prefix = ""
    if data.get("title"):
        title_disp = data["title"][:30] + "..." if len(data["title"]) > 30 else data["title"]
        kb_buttons.append([InlineKeyboardButton(text=f"⏭️ Keep: {title_disp}", callback_data="man_keep_title")])
        text_prefix = f"💡 *AI found:* `{data['title']}`\n_Tap 'Keep' below to use this, or type a new one._\n\n"
        
    kb_buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    await cq.message.edit_text(
        f"*Step 2/6 – Title*\n\n"
        f"Category: *{category}*\n\n"
        f"{text_prefix}"
        f"What is the title of this record?\n_e.g. 'Car RC', 'Electricity Bill', 'Passport'_",
        reply_markup=kb, parse_mode="Markdown"
    )
    await state.set_state(ManualEntry.title)
    await cq.answer()

@router.callback_query(F.data == "man_keep_title", ManualEntry.title)
async def on_keep_title(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _ask_amount(cq.message, data, edit=True)
    await state.set_state(ManualEntry.amount)
    await cq.answer()


# ─── Step 2: Amount ───────────────────────────────────────────────────────────

async def _ask_amount(msg, data: dict, *, edit: bool = False) -> None:
    kb_buttons = []
    text_prefix = ""
    if data.get("amount"):
        amt_disp = f"{data['amount']/100:.2f}"
        kb_buttons.append([InlineKeyboardButton(text=f"⏭️ Keep: ₹{amt_disp}", callback_data="man_keep_amount")])
        text_prefix = f"💡 *AI found:* `₹{amt_disp}`\n_Tap 'Keep' below, or type a new amount._\n\n"
        
    kb_buttons.append([InlineKeyboardButton(text="⏭️ Skip", callback_data="man_skip_amount"),
                       InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    text = (
        f"{_summary(data)}\n\n"
        f"*Step 3/6 – Amount* _(optional)_\n\n"
        f"{text_prefix}"
        f"Enter total amount in ₹ (e.g. 7340) or tap Skip:"
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")

@router.message(ManualEntry.title)
async def on_manual_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    await state.update_data(title=title)
    data = await state.get_data()
    await _ask_amount(message, data, edit=False)
    await state.set_state(ManualEntry.amount)


@router.callback_query(F.data == "man_skip_amount", ManualEntry.amount)
async def on_skip_amount(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(amount=None)
    data = await state.get_data()
    await _ask_start_date(cq.message, data, edit=True)
    await state.set_state(ManualEntry.start_date)
    await cq.answer()


@router.callback_query(F.data == "man_keep_amount", ManualEntry.amount)
async def on_keep_amount(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _ask_start_date(cq.message, data, edit=True)
    await state.set_state(ManualEntry.start_date)
    await cq.answer()

@router.message(ManualEntry.amount)
async def on_manual_amount(message: Message, state: FSMContext) -> None:
    text = message.text.strip().replace(",", "")
    try:
        amt = int(float(text) * 100)
        await state.update_data(amount=amt)
        data = await state.get_data()
        await _ask_start_date(message, data, edit=False)
        await state.set_state(ManualEntry.start_date)
    except ValueError:
        await message.reply("Please enter a valid number (e.g. `7340`) or tap **Skip** above.", parse_mode="Markdown")


# ─── Step 3: Start / Key Date ────────────────────────────────────────────────

async def _ask_start_date(msg, data: dict, *, edit: bool) -> None:
    kb_buttons = []
    text_prefix = ""
    if data.get("start_date"):
        kb_buttons.append([InlineKeyboardButton(text=f"⏭️ Keep: {data['start_date']}", callback_data="man_keep_start")])
        text_prefix = f"💡 *AI found:* `{data['start_date']}`\n_Tap 'Keep' below, or type a new date._\n\n"
        
    kb_buttons.append([InlineKeyboardButton(text="📅 Today", callback_data="man_set_start_today")])
    kb_buttons.append([InlineKeyboardButton(text="⏭️ Skip", callback_data="man_skip_start"),
                       InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    text = (
        f"{_summary(data)}\n\n"
        f"*Step 4/6 – Start / Key Date* _(optional)_\n\n"
        f"{text_prefix}"
        f"Enter date in any format: `25 sep`, `25-09-2026`, `2026-09-25`\n"
        f"This is used for sorting and context."
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "man_keep_start", ManualEntry.start_date)
async def on_keep_start(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _ask_end_date(cq.message, data, edit=True)
    await state.set_state(ManualEntry.end_date)
    await cq.answer()

@router.callback_query(F.data == "man_skip_start", ManualEntry.start_date)
async def on_skip_start(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(start_date=None)
    data = await state.get_data()
    await _ask_end_date(cq.message, data, edit=True)
    await state.set_state(ManualEntry.end_date)
    await cq.answer()

@router.callback_query(F.data == "man_set_start_today", ManualEntry.start_date)
async def on_set_start_today(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(start_date=date.today().isoformat())
    data = await state.get_data()
    await _ask_end_date(cq.message, data, edit=True)
    await state.set_state(ManualEntry.end_date)
    await cq.answer()

@router.message(ManualEntry.start_date)
async def on_manual_start_date(message: Message, state: FSMContext) -> None:
    parsed = _parse_smart_date(message.text)
    if not parsed:
        await message.reply(
            "❓ Couldn't understand that date. Try formats like:\n"
            "`25 sep`, `25-09-2026`, `25/09/2026`, `2026-09-25`\n\n"
            "Or tap **Skip** above.",
            parse_mode="Markdown"
        )
        return
    await state.update_data(start_date=parsed)
    data = await state.get_data()
    await _ask_end_date(message, data, edit=False)
    await state.set_state(ManualEntry.end_date)


# ─── Step 4: End / Expiry Date ───────────────────────────────────────────────

async def _ask_end_date(msg, data: dict, *, edit: bool) -> None:
    kb_buttons = []
    text_prefix = ""
    if data.get("end_date"):
        kb_buttons.append([InlineKeyboardButton(text=f"⏭️ Keep: {data['end_date']}", callback_data="man_keep_end")])
        text_prefix = f"💡 *AI found:* `{data['end_date']}`\n_Tap 'Keep' below, or type a new date._\n\n"
        
    kb_buttons.append([
        InlineKeyboardButton(text="+6 Months", callback_data="man_set_end_6m"),
        InlineKeyboardButton(text="+1 Year", callback_data="man_set_end_1y"),
        InlineKeyboardButton(text="+2 Years", callback_data="man_set_end_2y")
    ])
    kb_buttons.append([
        InlineKeyboardButton(text="🔕 No Reminders Needed", callback_data="man_no_reminders"),
        InlineKeyboardButton(text="⏭️ Skip", callback_data="man_skip_end")
    ])
    kb_buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    text = (
        f"{_summary(data)}\n\n"
        f"*Step 5/6 – End / Expiry Date* _(optional)_\n\n"
        f"{text_prefix}"
        f"Enter expiry or end date (same formats). "
        f"This is when reminders will be sent."
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "man_keep_end", ManualEntry.end_date)
async def on_keep_end(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _ask_attachment(cq.message, data, edit=True)
    await state.set_state(ManualEntry.attachment)
    await cq.answer()

@router.callback_query(F.data == "man_skip_end", ManualEntry.end_date)
async def on_skip_end(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(end_date=None)
    data = await state.get_data()
    await _ask_attachment(cq.message, data, edit=True)
    await state.set_state(ManualEntry.attachment)
    await cq.answer()

@router.callback_query(F.data == "man_no_reminders", ManualEntry.end_date)
async def on_man_no_reminders(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(end_date=None, no_reminders=True)
    data = await state.get_data()
    await _ask_attachment(cq.message, data, edit=True)
    await state.set_state(ManualEntry.attachment)
    await cq.answer()

async def _handle_end_offset(cq: CallbackQuery, state: FSMContext, months: int = 0, years: int = 0) -> None:
    data = await state.get_data()
    start = data.get("start_date")
    base_date = date.fromisoformat(start) if start else date.today()
    new_date = base_date + relativedelta(months=months, years=years)
    await state.update_data(end_date=new_date.isoformat(), no_reminders=False)
    data = await state.get_data()
    await _ask_attachment(cq.message, data, edit=True)
    await state.set_state(ManualEntry.attachment)
    await cq.answer()

@router.callback_query(F.data == "man_set_end_6m", ManualEntry.end_date)
async def on_set_end_6m(cq: CallbackQuery, state: FSMContext) -> None:
    await _handle_end_offset(cq, state, months=6)

@router.callback_query(F.data == "man_set_end_1y", ManualEntry.end_date)
async def on_set_end_1y(cq: CallbackQuery, state: FSMContext) -> None:
    await _handle_end_offset(cq, state, years=1)

@router.callback_query(F.data == "man_set_end_2y", ManualEntry.end_date)
async def on_set_end_2y(cq: CallbackQuery, state: FSMContext) -> None:
    await _handle_end_offset(cq, state, years=2)


@router.message(ManualEntry.end_date)
async def on_manual_end_date(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip().lower()
    if txt in ["none", "no", "skip", "no reminder", "no reminders", "no reminder needed", "no reminders needed", "n/a", "-"]:
        await state.update_data(end_date=None, no_reminders=True)
        data = await state.get_data()
        await _ask_attachment(message, data, edit=False)
        await state.set_state(ManualEntry.attachment)
        return

    parsed = _parse_smart_date(message.text)
    if not parsed:
        await message.reply(
            "❓ Couldn't understand that date. Try `25 sep`, `25-09-2026`, `2026-09-25`\n\nOr tap **No Reminders Needed** or **Skip**.",
            parse_mode="Markdown"
        )
        return
    await state.update_data(end_date=parsed, no_reminders=False)
    data = await state.get_data()
    await _ask_attachment(message, data, edit=False)
    await state.set_state(ManualEntry.attachment)


# ─── Step 5: Attachment ──────────────────────────────────────────────────────

async def _ask_attachment(msg, data: dict, *, edit: bool) -> None:
    has_existing = bool(data.get("has_attachment") or data.get("temp_paths") or data.get("all_files"))
    if has_existing:
        files_count = len(data.get("all_files", [])) or 1
        count_label = f" ({files_count} file{'s' if files_count > 1 else ''})" if files_count > 1 else ""
        kb_buttons = [
            [InlineKeyboardButton(text=f"✅ Keep Uploaded Document{count_label}", callback_data="man_keep_attach")],
            [InlineKeyboardButton(text="🗑️ Remove Attachment", callback_data="man_remove_attach")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")]
        ]
        text_body = (
            f"💡 *Uploaded document is attached!*{count_label}\n\n"
            f"Tap **Keep Uploaded Document** to keep it, send a new photo or PDF to replace it, or tap **Remove Attachment**."
        )
    else:
        kb_buttons = [
            [InlineKeyboardButton(text="⏭️ Skip", callback_data="man_skip_attach")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")]
        ]
        text_body = f"📸 Send a photo or PDF to attach the document to your Vault, or tap **Skip**:"

    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    text = (
        f"{_summary(data)}\n\n"
        f"*Step 6/7 – Attachment* _(optional)_\n\n"
        f"{text_body}"
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "man_keep_attach", ManualEntry.attachment)
async def on_keep_attachment(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _ask_notes(cq.message, data, edit=True)
    await state.set_state(ManualEntry.notes)
    await cq.answer()


@router.callback_query(F.data == "man_remove_attach", ManualEntry.attachment)
async def on_remove_attachment(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(temp_paths=None, temp_mime=None, all_files=[], has_attachment=False)
    data = await state.get_data()
    await _ask_notes(cq.message, data, edit=True)
    await state.set_state(ManualEntry.notes)
    await cq.answer("Attachment removed.")


@router.callback_query(F.data == "man_skip_attach", ManualEntry.attachment)
async def on_skip_attachment(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _ask_notes(cq.message, data, edit=True)
    await state.set_state(ManualEntry.notes)
    await cq.answer()


@router.message(ManualEntry.attachment, F.photo)
async def on_manual_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(
        temp_paths=file_id,
        temp_mime="image/jpeg",
        all_files=[{"file_id": file_id, "mime": "image/jpeg"}],
        has_attachment=True
    )
    data = await state.get_data()
    await message.reply("📎 Photo attached!")
    await _ask_notes(message, data, edit=False)
    await state.set_state(ManualEntry.notes)


@router.message(ManualEntry.attachment, F.document)
async def on_manual_document(message: Message, state: FSMContext) -> None:
    mime = message.document.mime_type or "application/octet-stream"
    if mime not in ["application/pdf", "image/jpeg", "image/png"]:
        await message.reply("❌ Please send a PDF or image (JPEG/PNG), or tap Skip above.")
        return
    file_id = message.document.file_id
    await state.update_data(
        temp_paths=file_id,
        temp_mime=mime,
        all_files=[{"file_id": file_id, "mime": mime}],
        has_attachment=True
    )
    data = await state.get_data()
    await message.reply("📎 Document attached!")
    await _ask_notes(message, data, edit=False)
    await state.set_state(ManualEntry.notes)

# ─── Step 5: Notes ───────────────────────────────────────────────────────────

async def _ask_notes(msg, data: dict, *, edit: bool) -> None:
    kb_buttons = []
    text_prefix = ""
    if data.get("notes"):
        note_disp = data["notes"][:30] + "..." if len(data["notes"]) > 30 else data["notes"]
        kb_buttons.append([InlineKeyboardButton(text=f"⏭️ Keep: {note_disp}", callback_data="man_keep_notes")])
        text_prefix = f"💡 *AI found:* `{note_disp}`\n_Tap 'Keep' below, or type a new note._\n\n"
        
    kb_buttons.append([InlineKeyboardButton(text="⏭️ Skip", callback_data="man_skip_notes"),
                       InlineKeyboardButton(text="❌ Cancel", callback_data="man_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    
    text = (
        f"{_summary(data)}\n\n"
        f"*Step 7/7 – Notes* _(optional)_\n\n"
        f"{text_prefix}"
        f"Add a one-line note for this record, or tap Skip:"
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "man_keep_notes", ManualEntry.notes)
async def on_keep_notes(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _show_confirm(cq.message, data, edit=True)
    await state.set_state(ManualEntry.confirm)
    await cq.answer()

@router.callback_query(F.data == "man_skip_notes", ManualEntry.notes)
async def on_skip_notes(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(notes=None)
    data = await state.get_data()
    await _show_confirm(cq.message, data, edit=True)
    await state.set_state(ManualEntry.confirm)
    await cq.answer()


@router.message(ManualEntry.notes)
async def on_manual_notes(message: Message, state: FSMContext) -> None:
    notes = message.text.strip()[:2000]
    await state.update_data(notes=notes)
    data = await state.get_data()
    await _show_confirm(message, data, edit=False)
    await state.set_state(ManualEntry.confirm)


# ─── Step 6: Confirm ─────────────────────────────────────────────────────────

async def _show_confirm(msg, data: dict, *, edit: bool) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Save",    callback_data="man_save"),
         InlineKeyboardButton(text="❌ Cancel",  callback_data="man_cancel")],
        [InlineKeyboardButton(text="⬅️ Back to Category", callback_data="man_restart")],
    ])
    text = f"{_summary(data)}\n\n_Confirm and save?_"
    await _safe_send_or_edit(msg, text, edit=edit, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "man_restart")
async def on_man_restart(cq: CallbackQuery, state: FSMContext) -> None:
    """Go back to start of manual entry (Category), keeping all entered data."""
    await state.set_state(ManualEntry.category)
    data = await state.get_data()
    
    # Re-trigger add_manual display
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Vehicle",   callback_data="man_cat_vehicle"),
         InlineKeyboardButton(text="🛍️ Purchases", callback_data="man_cat_warranty & purchases")],
        [InlineKeyboardButton(text="👨‍👩‍👧 People",   callback_data="man_cat_people & occasions"),
         InlineKeyboardButton(text="🧾 Bills",     callback_data="man_cat_bills & subscriptions")],
        [InlineKeyboardButton(text="🛡️ Insurance", callback_data="man_cat_insurance"),
         InlineKeyboardButton(text="🪪 IDs",        callback_data="man_cat_ids & government docs")],
        [InlineKeyboardButton(text="🏥 Health",    callback_data="man_cat_health"),
         InlineKeyboardButton(text="🏠 Home",       callback_data="man_cat_home")],
        [InlineKeyboardButton(text="🛠️ Custom",    callback_data="man_cat_custom")],
        [InlineKeyboardButton(text="❌ Cancel",     callback_data="man_cancel")],
    ])
    text = f"{_summary(data)}\n\n*Step 1/6 ➔ Category*\n\nSelect a category:"
    await _safe_send_or_edit(cq.message, text, edit=True, reply_markup=kb, parse_mode="Markdown")
    try:
        await cq.answer()
    except Exception:
        pass


@router.callback_query(F.data == "man_save", ManualEntry.confirm)
async def on_man_save(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    category  = data["category"]
    title     = data["title"]
    amount    = data.get("amount")
    start_dt  = data.get("start_date")
    end_dt    = data.get("end_date")
    notes     = data.get("notes")
    draft_id  = data.get("draft_id")

    # Determine key_date and key_date_type
    key_date      = end_dt or start_dt
    key_date_type = "expiry" if end_dt else ("event" if start_dt else None)

    # Gather all attached files
    files_to_attach = data.get("all_files") or []
    temp_paths = data.get("temp_paths")
    mime = data.get("temp_mime") or "image/jpeg"

    if not files_to_attach and temp_paths:
        files_to_attach = [{"file_id": temp_paths, "mime": mime}]
    elif not files_to_attach and draft_id and data.get("has_attachment") is not False:
        async with get_db() as db:
            async with db.execute("SELECT temp_paths FROM drafts WHERE id = ?", (draft_id,)) as cur:
                row = await cur.fetchone()
                if row and row["temp_paths"]:
                    try:
                        import json
                        temp_data = json.loads(row["temp_paths"])
                        if isinstance(temp_data, dict) and "files" in temp_data:
                            files_to_attach = temp_data["files"]
                            mime = temp_data.get("mime", "image/jpeg")
                        elif isinstance(temp_data, dict) and "file_id" in temp_data:
                            mime = temp_data.get("mime", "image/jpeg")
                            files_to_attach = [{"file_id": temp_data["file_id"], "mime": mime}]
                        elif isinstance(temp_data, str):
                            files_to_attach = [{"file_id": temp_data, "mime": "image/jpeg"}]
                    except Exception:
                        files_to_attach = [{"file_id": str(row["temp_paths"]), "mime": "image/jpeg"}]

    primary_mime = files_to_attach[0].get("mime", mime) if files_to_attach else mime
    ext = ".pdf" if "pdf" in primary_mime.lower() else (".png" if "png" in primary_mime.lower() else (".jpg" if files_to_attach else ".txt"))
    code = generate_coded_name(ext)

    edit_mode = data.get("edit_mode")
    existing_record_id = data.get("record_id")

    if edit_mode and existing_record_id:
        from squares_vault.db.repositories import update_record
        await update_record(
            record_id=existing_record_id,
            user_id=cq.from_user.id,
            title=title,
            category=category,
            amount_minor=amount,
            key_date=key_date,
            key_date_type=key_date_type,
            notes=notes
        )
        record_db_id = existing_record_id
        async with get_db() as db:
            await db.execute(
                "UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'",
                (record_db_id, cq.from_user.id)
            )
            await db.commit()
    else:
        parent_rec = data.get("parent_record_id")
        record_db_id = await create_record(
            user_id          = cq.from_user.id,
            category         = category,
            title            = title,
            code             = code,
            amount_minor     = amount,
            key_date         = key_date,
            key_date_type    = key_date_type,
            notes            = notes,
            parent_record_id = parent_rec
        )
        if parent_rec:
            async with get_db() as db:
                await db.execute(
                    "UPDATE records SET status = 'renewed' WHERE id = ? AND user_id = ?",
                    (parent_rec, cq.from_user.id)
                )
                await db.execute(
                    "UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'",
                    (parent_rec, cq.from_user.id)
                )
                await db.commit()

    # Attach all files
    if files_to_attach:
        from squares_vault.db.repositories import add_file
        for idx, item in enumerate(files_to_attach):
            f_id = item["file_id"]
            f_mime = item.get("mime", primary_mime)
            f_ext = ".pdf" if "pdf" in f_mime.lower() else (".png" if "png" in f_mime.lower() else ".jpg")
            f_code = code if idx == 0 else generate_coded_name(f_ext)
            await add_file(
                record_id=record_db_id,
                user_id=cq.from_user.id,
                drive_file_id=f_id,
                coded_name=f_code,
                mime=f_mime,
                size=0
            )

    if draft_id:
        async with get_db() as db:
            await db.execute("UPDATE drafts SET state = 'saved' WHERE id = ?", (draft_id,))
            await db.commit()

    amt_str = f"₹{amount/100:.2f}" if amount else "—"
    clean_title = _escape_md(title)
    clean_cat = _escape_md(category)
    clean_notes = _escape_md(notes) if notes else None
    lines = [
        f"✅ *Saved!*",
        f"",
        f"📌 *{clean_title}*",
        f"Category: {clean_cat}",
        f"Amount: {amt_str}",
        f"Start date: {_fmt_date(start_dt)}",
        f"End/Expiry: {'— (No reminders needed)' if (data.get('no_reminders') or not end_dt) else _fmt_date(end_dt)}",
    ]
    if clean_notes:
        lines.append(f"Notes: {clean_notes}")
    if files_to_attach:
        count_txt = f"{len(files_to_attach)} document{'s' if len(files_to_attach) > 1 else ''}"
        lines.append(f"📎 Attached ({count_txt})")

    # Set Reminders if it's an expiry
    reminders_set = False
    if key_date_type == "expiry" and key_date and not data.get("no_reminders"):
        try:
            from datetime import datetime, timezone
            from squares_vault.reminders.schedule import generate_reminders
            dt_obj = datetime.strptime(key_date, "%Y-%m-%d")
            reminders = generate_reminders(record_db_id, cq.from_user.id, category, dt_obj)
            now_iso = datetime.now(timezone.utc).isoformat() + "Z"
            has_future = False
            async with get_db() as db:
                for rm in reminders:
                    st = "skipped" if rm["fire_at"] < now_iso else "pending"
                    if st == "pending":
                        has_future = True
                    await db.execute(
                        "INSERT INTO reminders (id, record_id, user_id, kind, offset_days, fire_at, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (rm["id"], record_db_id, cq.from_user.id, rm["kind"], rm["offset_days"], rm["fire_at"], st)
                    )
                await db.commit()
            reminders_set = has_future
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to generate reminders in manual: {e}")

    try:
        if reminders_set:
            lines.append("\n🔔 *Reminders have been set.*")
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📅 Add to Phone Calendar", callback_data=f"send_ics:{record_db_id}")]])
            await _safe_send_or_edit(cq.message, "\n".join(lines), edit=True, reply_markup=kb, parse_mode="Markdown")
        else:
            await _safe_send_or_edit(cq.message, "\n".join(lines), edit=True, parse_mode="Markdown")
    finally:
        await state.clear()
        try:
            await cq.answer("Saved!")
        except Exception:
            pass


@router.callback_query(F.data == "man_cancel")
async def on_manual_cancel(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await cq.message.edit_text("❌ Manual entry cancelled.")
    except Exception:
        pass
    await cq.answer()


# ═════════════════════════════════════════════════════════════════════════════
# ─── RENEWAL WIZARD (RenewalEntry) ───────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

async def start_renewal_flow(cq: CallbackQuery, record_id: str, state: FSMContext) -> None:
    """Entry point when user clicks Renew on any record card."""
    async with get_db() as db:
        async with db.execute("SELECT * FROM records WHERE id = ? AND user_id = ?", (record_id, cq.from_user.id)) as cur:
            row = await cur.fetchone()
    if not row:
        await cq.answer("Record not found.", show_alert=True)
        return

    row = dict(row)
    await state.clear()
    await state.update_data(
        parent_record_id=record_id,
        title=row["title"],
        category=row["category"],
        old_amount=row["amount_minor"],
        amount=row["amount_minor"],
        old_key_date=row["key_date"],
        notes=row["notes"] or "",
        temp_paths=None,
        temp_mime=None
    )
    await state.set_state(RenewalEntry.document)

    amt_str = f"₹{row['amount_minor']/100:.2f}" if row.get("amount_minor") else "—"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Renew Without Document", callback_data="renew_skip_doc")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="renew_cancel")]
    ])
    text = (
        f"🔄 **RENEW DOCUMENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 **Original:** {row['title']}\n"
        f"🏷️ **Category:** {row['category'].title()}\n"
        f"📅 **Previous Expiry:** {_fmt_date(row.get('key_date'))}\n"
        f"💰 **Previous Amount:** {amt_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📸 **Send the renewed document or invoice** (photo or PDF) to attach it to this renewal.\n\n"
        f"_Or tap below to enter the renewal details without a document:_"
    )
    await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await cq.answer("Starting renewal...")


@router.callback_query(F.data == "renew_skip_doc", RenewalEntry.document)
async def on_renew_skip_doc(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _ask_renew_end_date(cq.message, data, edit=True)
    await state.set_state(RenewalEntry.end_date)
    await cq.answer()


@router.message(RenewalEntry.document, F.photo)
async def on_renew_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await state.update_data(temp_paths=file_id, temp_mime="image/jpeg", has_attachment=True)
    data = await state.get_data()
    await message.reply("📎 Renewal photo received!")
    await _ask_renew_end_date(message, data, edit=False)
    await state.set_state(RenewalEntry.end_date)


@router.message(RenewalEntry.document, F.document)
async def on_renew_document(message: Message, state: FSMContext) -> None:
    mime = message.document.mime_type or "application/octet-stream"
    if mime not in ["application/pdf", "image/jpeg", "image/png"]:
        await message.reply("❌ Please send a PDF or image, or tap 'Renew Without Document' above.")
        return
    file_id = message.document.file_id
    await state.update_data(temp_paths=file_id, temp_mime=mime, has_attachment=True)
    data = await state.get_data()
    await message.reply("📎 Renewal document received!")
    await _ask_renew_end_date(message, data, edit=False)
    await state.set_state(RenewalEntry.end_date)


async def _ask_renew_end_date(msg, data: dict, *, edit: bool) -> None:
    old_kd = data.get("old_key_date")
    base_dt = date.fromisoformat(old_kd[:10]) if old_kd else date.today()
    if base_dt < date.today():
        base_dt = date.today()

    d_6m = (base_dt + relativedelta(months=6)).isoformat()
    d_1y = (base_dt + relativedelta(years=1)).isoformat()
    d_2y = (base_dt + relativedelta(years=2)).isoformat()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"+1 Year ({_fmt_date(d_1y)})", callback_data=f"renew_set_date:{d_1y}")],
        [InlineKeyboardButton(text=f"+6 Months ({_fmt_date(d_6m)})", callback_data=f"renew_set_date:{d_6m}"),
         InlineKeyboardButton(text=f"+2 Years ({_fmt_date(d_2y)})", callback_data=f"renew_set_date:{d_2y}")],
        [InlineKeyboardButton(text="🔕 No Reminders Needed", callback_data="renew_no_reminders")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="renew_cancel")]
    ])
    prev_str = f"`{_fmt_date(old_kd)}`" if old_kd else "None"
    text = (
        f"🔄 **RENEW: Step 2/4 – New Expiry Date**\n\n"
        f"Previous expiry was: {prev_str}\n\n"
        f"_Tap a smart date extension above, or type the new expiry date in chat (e.g. `25-10-2027`):_"
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("renew_set_date:"), RenewalEntry.end_date)
async def on_renew_button_date(cq: CallbackQuery, state: FSMContext) -> None:
    iso_date = cq.data.split(":", 1)[1]
    await state.update_data(end_date=iso_date, no_reminders=False)
    data = await state.get_data()
    await _ask_renew_amount(cq.message, data, edit=True)
    await state.set_state(RenewalEntry.amount)
    await cq.answer()


@router.callback_query(F.data == "renew_no_reminders", RenewalEntry.end_date)
async def on_renew_no_reminders(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(end_date=None, no_reminders=True)
    data = await state.get_data()
    await _ask_renew_amount(cq.message, data, edit=True)
    await state.set_state(RenewalEntry.amount)
    await cq.answer()


@router.message(RenewalEntry.end_date)
async def on_renew_text_date(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip().lower()
    if txt in ["none", "no", "skip", "no reminder", "no reminders", "no reminder needed", "no reminders needed", "n/a", "-"]:
        await state.update_data(end_date=None, no_reminders=True)
        data = await state.get_data()
        await _ask_renew_amount(message, data, edit=False)
        await state.set_state(RenewalEntry.amount)
        return

    parsed = _parse_smart_date(message.text)
    if not parsed:
        await message.reply(
            "❓ Couldn't understand that date. Try formats like `25 sep`, `25-09-2027`, `2027-09-25`.\nOr tap one of the extension buttons above or 'No Reminders Needed'.",
            parse_mode="Markdown"
        )
        return
    await state.update_data(end_date=parsed, no_reminders=False)
    data = await state.get_data()
    await _ask_renew_amount(message, data, edit=False)
    await state.set_state(RenewalEntry.amount)


async def _ask_renew_amount(msg, data: dict, *, edit: bool) -> None:
    old_amt = data.get("old_amount")
    kb_buttons = []
    if old_amt:
        kb_buttons.append([InlineKeyboardButton(text=f"⏭️ Keep: ₹{old_amt/100:.2f}", callback_data="renew_keep_amt")])
    kb_buttons.append([InlineKeyboardButton(text="⏭️ Skip (No Amount)", callback_data="renew_skip_amt"),
                       InlineKeyboardButton(text="❌ Cancel", callback_data="renew_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    old_amt_str = f"`₹{old_amt/100:.2f}`" if old_amt else "None"
    text = (
        f"🔄 **RENEW: Step 3/4 – Renewal Cost**\n\n"
        f"Previous amount was: {old_amt_str}\n\n"
        f"_Tap 'Keep' above, or type the new amount in ₹ (e.g. `950`):_"
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "renew_keep_amt", RenewalEntry.amount)
async def on_renew_keep_amt(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(amount=data.get("old_amount"))
    await _ask_renew_notes(cq.message, await state.get_data(), edit=True)
    await state.set_state(RenewalEntry.notes)
    await cq.answer()


@router.callback_query(F.data == "renew_skip_amt", RenewalEntry.amount)
async def on_renew_skip_amt(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(amount=None)
    data = await state.get_data()
    await _ask_renew_notes(cq.message, data, edit=True)
    await state.set_state(RenewalEntry.notes)
    await cq.answer()


@router.message(RenewalEntry.amount)
async def on_renew_text_amt(message: Message, state: FSMContext) -> None:
    text = message.text.strip().replace(",", "")
    try:
        amt = int(float(text) * 100)
        await state.update_data(amount=amt)
        data = await state.get_data()
        await _ask_renew_notes(message, data, edit=False)
        await state.set_state(RenewalEntry.notes)
    except ValueError:
        await message.reply("Please enter a valid amount (e.g. `950`) or tap **Skip** above.", parse_mode="Markdown")


async def _ask_renew_notes(msg, data: dict, *, edit: bool) -> None:
    old_notes = data.get("notes")
    kb_buttons = []
    if old_notes:
        disp_n = old_notes[:25] + "..." if len(old_notes) > 25 else old_notes
        kb_buttons.append([InlineKeyboardButton(text=f"⏭️ Keep: {disp_n}", callback_data="renew_keep_notes")])
    kb_buttons.append([InlineKeyboardButton(text="⏭️ Skip", callback_data="renew_skip_notes"),
                       InlineKeyboardButton(text="❌ Cancel", callback_data="renew_cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    text = (
        f"🔄 **RENEW: Step 4/4 – Notes** (optional)\n\n"
        f"_Add a note for this renewal (e.g. 'Renewed with 10% discount'), or tap Skip:_"
    )
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "renew_keep_notes", RenewalEntry.notes)
async def on_renew_keep_notes(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await _show_renew_confirm(cq.message, data, edit=True)
    await state.set_state(RenewalEntry.confirm)
    await cq.answer()


@router.callback_query(F.data == "renew_skip_notes", RenewalEntry.notes)
async def on_renew_skip_notes(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(notes=None)
    data = await state.get_data()
    await _show_renew_confirm(cq.message, data, edit=True)
    await state.set_state(RenewalEntry.confirm)
    await cq.answer()


@router.message(RenewalEntry.notes)
async def on_renew_text_notes(message: Message, state: FSMContext) -> None:
    notes = message.text.strip()[:250]
    await state.update_data(notes=notes)
    data = await state.get_data()
    await _show_renew_confirm(message, data, edit=False)
    await state.set_state(RenewalEntry.confirm)


async def _show_renew_confirm(msg, data: dict, *, edit: bool) -> None:
    amt_str = f"₹{data['amount']/100:.2f}" if data.get("amount") else "—"
    attach_str = "✅ Yes (New file attached)" if data.get("temp_paths") else "— (No new file)"
    exp_display = "— (No reminders needed)" if (data.get("no_reminders") or not data.get("end_date")) else _fmt_date(data.get("end_date"))
    lines = [
        "🧾 **RENEWAL CONFIRMATION**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📄 **Document:** {data['title']}",
        f"🏷️ **Category:** {data['category'].title()}",
        f"💰 **Renewal Cost:** {amt_str}",
        f"⏳ **New Expiry:** {exp_display}",
        f"📎 **Attachment:** {attach_str}",
    ]
    if data.get("notes"):
        lines.append(f"📝 **Notes:** {data['notes']}")
    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "_Confirm to save renewal and archive the previous record._"
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm & Save Renewal", callback_data="renew_save")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="renew_cancel")]
    ])
    if edit:
        await msg.edit_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.reply("\n".join(lines), reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "renew_save", RenewalEntry.confirm)
async def on_renew_save(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    parent_id = data["parent_record_id"]
    category = data["category"]
    title = data["title"]
    amount = data.get("amount")
    end_date = data.get("end_date")
    notes = data.get("notes")
    temp_paths = data.get("temp_paths")
    temp_mime = data.get("temp_mime") or "image/jpeg"

    if temp_paths:
        ext = ".pdf" if "pdf" in temp_mime.lower() else (".png" if "png" in temp_mime.lower() else ".jpg")
        code = generate_coded_name(ext)
    else:
        code = generate_coded_name(".txt")

    # 1. Create new record
    record_db_id = await create_record(
        user_id          = cq.from_user.id,
        category         = category,
        title            = title,
        code             = code,
        amount_minor     = amount,
        key_date         = end_date,
        key_date_type    = "expiry" if end_date else None,
        notes            = notes,
        parent_record_id = parent_id
    )

    # 2. Archive parent record & cancel old pending reminders
    async with get_db() as db:
        await db.execute("UPDATE records SET status = 'renewed' WHERE id = ? AND user_id = ?", (parent_id, cq.from_user.id))
        await db.execute("UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'", (parent_id, cq.from_user.id))
        await db.commit()

    # 3. Attach file if uploaded
    if temp_paths:
        from squares_vault.db.repositories import add_file
        await add_file(
            record_id=record_db_id,
            user_id=cq.from_user.id,
            drive_file_id=temp_paths,
            coded_name=code,
            mime=temp_mime,
            size=0
        )

    # 4. Generate reminders for new expiry
    reminders_set = False
    if end_date and not data.get("no_reminders"):
        try:
            from datetime import datetime, timezone
            from squares_vault.reminders.schedule import generate_reminders
            dt_obj = datetime.strptime(end_date[:10], "%Y-%m-%d")
            reminders = generate_reminders(record_db_id, cq.from_user.id, category, dt_obj)
            now_iso = datetime.now(timezone.utc).isoformat() + "Z"
            has_future = False
            async with get_db() as db:
                for rm in reminders:
                    st = "skipped" if rm["fire_at"] < now_iso else "pending"
                    if st == "pending":
                        has_future = True
                    await db.execute(
                        "INSERT INTO reminders (id, record_id, user_id, kind, offset_days, fire_at, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (rm["id"], record_db_id, cq.from_user.id, rm["kind"], rm["offset_days"], rm["fire_at"], st)
                    )
                await db.commit()
            reminders_set = has_future
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to generate reminders in renew: {e}")

    await state.clear()
    amt_str = f"₹{amount/100:.2f}" if amount else "—"
    saved_exp = "— (No reminders needed)" if (data.get("no_reminders") or not end_date) else _fmt_date(end_date)
    lines = [
        f"✅ *{title}* renewed successfully!",
        f"",
        f"🏷️ Category: {category.title()}",
        f"💰 Renewal Cost: {amt_str}",
        f"⏳ New Expiry: {saved_exp}",
    ]
    if notes:
        lines.append(f"📝 Notes: {notes}")
    if temp_paths:
        lines.append("📎 Renewal document attached.")

    if reminders_set:
        lines.append("\n🔔 *Reminders have been set for the new expiry date.*")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Add to Phone Calendar", callback_data=f"send_ics:{record_db_id}")],
            [InlineKeyboardButton(text="📄 View Renewed Record", callback_data=f"view_record:{record_db_id}")]
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📄 View Renewed Record", callback_data=f"view_record:{record_db_id}")]])

    await cq.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")
    await cq.answer("Renewed successfully!")


@router.callback_query(F.data == "renew_cancel")
async def on_renew_cancel(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    pid = data.get("parent_record_id")
    await state.clear()
    if pid:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📄 Return to Document", callback_data=f"view_record:{pid}")]])
        await cq.message.edit_text("❌ Renewal cancelled.", reply_markup=kb)
    else:
        await cq.message.edit_text("❌ Renewal cancelled.")
    await cq.answer()


# ═════════════════════════════════════════════════════════════════════════════
# ─── INTERACTIVE EDIT DASHBOARD (EditEntry) ──────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

async def start_edit_flow(cq: CallbackQuery, record_id: str, state: FSMContext) -> None:
    """Entry point when user clicks Edit on any record card."""
    async with get_db() as db:
        async with db.execute("SELECT * FROM records WHERE id = ? AND user_id = ?", (record_id, cq.from_user.id)) as cur:
            row = await cur.fetchone()
        async with db.execute("SELECT count(*) as c FROM files WHERE record_id = ?", (record_id,)) as cur:
            f_count = (await cur.fetchone())["c"]

    if not row:
        await cq.answer("Record not found.", show_alert=True)
        return

    await state.clear()
    await state.update_data(
        record_id=record_id,
        title=row["title"],
        category=row["category"],
        amount=row["amount_minor"],
        key_date=row["key_date"],
        notes=row["notes"] or "",
        file_count=f_count
    )
    await state.set_state(EditEntry.menu)
    await _render_edit_dashboard(cq.message, await state.get_data(), edit=True)
    await cq.answer()


async def _render_edit_dashboard(msg, data: dict, *, edit: bool) -> None:
    rid = data["record_id"]
    amt_str = f"₹{data['amount']/100:.2f}" if data.get("amount") else "—"
    fc = data.get("file_count", 0)
    attach_str = f"{fc} file(s) attached" if fc > 0 else "None"

    clean_title = _escape_md(data.get("title") or "Untitled")
    clean_cat = _escape_md((data.get("category") or "").title())
    notes_val = _escape_md(data.get("notes")) if data.get("notes") else "—"

    text = (
        f"✏️ **EDIT DOCUMENT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 **Title:** {clean_title}\n"
        f"🏷️ **Category:** {clean_cat}\n"
        f"💰 **Amount:** {amt_str}\n"
        f"📅 **Key Date:** {_fmt_date(data.get('key_date'))}\n"
        f"📝 **Notes:** {notes_val}\n"
        f"📎 **Attachment:** {attach_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Tap a field below to change it:_"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Title", callback_data="edit_field:title"),
         InlineKeyboardButton(text="🏷️ Category", callback_data="edit_field:category")],
        [InlineKeyboardButton(text="💰 Amount", callback_data="edit_field:amount"),
         InlineKeyboardButton(text="📅 Key Date", callback_data="edit_field:date")],
        [InlineKeyboardButton(text="📝 Notes", callback_data="edit_field:notes"),
         InlineKeyboardButton(text="📎 Add File", callback_data="edit_field:attachment")],
        [InlineKeyboardButton(text="✅ Done (Back to Document)", callback_data=f"view_record:{rid}")]
    ])
    await _safe_send_or_edit(msg, text, edit=edit, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "edit_cancel_field")
async def on_edit_cancel_field(cq: CallbackQuery, state: FSMContext) -> None:
    try:
        await state.set_state(EditEntry.menu)
        data = await state.get_data()
        await _render_edit_dashboard(cq.message, data, edit=True)
    finally:
        try:
            await cq.answer()
        except Exception:
            pass


@router.callback_query(F.data == "edit_field:title", EditEntry.menu)
async def on_edit_field_title(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="edit_cancel_field")]])
    await cq.message.edit_text(
        f"📄 **Edit Title**\n\nCurrent: `{data['title']}`\n\n_Send the new title in chat:_",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await state.set_state(EditEntry.edit_title)
    await cq.answer()


@router.message(EditEntry.edit_title)
async def on_save_edit_title(message: Message, state: FSMContext) -> None:
    new_title = message.text.strip()
    if not new_title:
        await message.reply("Title cannot be empty. Please send a valid title:")
        return
    data = await state.get_data()
    rid = data["record_id"]
    from squares_vault.db.repositories import update_record
    await update_record(
        record_id=rid,
        user_id=message.from_user.id,
        title=new_title,
        category=data["category"],
        amount_minor=data.get("amount"),
        key_date=data.get("key_date"),
        notes=data.get("notes")
    )
    await state.update_data(title=new_title)
    await state.set_state(EditEntry.menu)
    await _render_edit_dashboard(message, await state.get_data(), edit=False)


@router.callback_query(F.data == "edit_field:category", EditEntry.menu)
async def on_edit_field_category(cq: CallbackQuery, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Vehicle",   callback_data="set_edit_cat:vehicle"),
         InlineKeyboardButton(text="🛍️ Purchases", callback_data="set_edit_cat:warranty & purchases")],
        [InlineKeyboardButton(text="👨‍👩‍👧 People",   callback_data="set_edit_cat:people & occasions"),
         InlineKeyboardButton(text="🧾 Bills",     callback_data="set_edit_cat:bills & subscriptions")],
        [InlineKeyboardButton(text="🛡️ Insurance", callback_data="set_edit_cat:insurance"),
         InlineKeyboardButton(text="🪪 IDs",        callback_data="set_edit_cat:ids & government docs")],
        [InlineKeyboardButton(text="🏥 Health",    callback_data="set_edit_cat:health"),
         InlineKeyboardButton(text="🏠 Home",       callback_data="set_edit_cat:home")],
        [InlineKeyboardButton(text="🛠️ Custom",    callback_data="set_edit_cat:custom")],
        [InlineKeyboardButton(text="❌ Cancel",     callback_data="edit_cancel_field")],
    ])
    await cq.message.edit_text("🏷️ **Select New Category:**", reply_markup=kb, parse_mode="Markdown")
    await state.set_state(EditEntry.edit_category)
    await cq.answer()


@router.callback_query(F.data.startswith("set_edit_cat:"), EditEntry.edit_category)
async def on_save_edit_category(cq: CallbackQuery, state: FSMContext) -> None:
    new_cat = cq.data.split(":", 1)[1]
    data = await state.get_data()
    rid = data["record_id"]
    from squares_vault.db.repositories import update_record
    await update_record(
        record_id=rid,
        user_id=cq.from_user.id,
        title=data["title"],
        category=new_cat,
        amount_minor=data.get("amount"),
        key_date=data.get("key_date"),
        notes=data.get("notes")
    )
    await state.update_data(category=new_cat)
    await state.set_state(EditEntry.menu)
    await _render_edit_dashboard(cq.message, await state.get_data(), edit=True)
    await cq.answer("Category updated!")


@router.callback_query(F.data == "edit_field:amount", EditEntry.menu)
async def on_edit_field_amount(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    amt_str = f"`₹{data['amount']/100:.2f}`" if data.get("amount") else "None"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Clear Amount", callback_data="clear_edit_amount")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="edit_cancel_field")]
    ])
    await cq.message.edit_text(
        f"💰 **Edit Amount**\n\nCurrent: {amt_str}\n\n_Type the new amount in ₹ (e.g. `1250`) or tap Clear:_ ",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await state.set_state(EditEntry.edit_amount)
    await cq.answer()


@router.callback_query(F.data == "clear_edit_amount", EditEntry.edit_amount)
async def on_clear_edit_amount(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rid = data["record_id"]
    from squares_vault.db.repositories import update_record
    await update_record(
        record_id=rid,
        user_id=cq.from_user.id,
        title=data["title"],
        category=data["category"],
        amount_minor=None,
        key_date=data.get("key_date"),
        notes=data.get("notes")
    )
    await state.update_data(amount=None)
    await state.set_state(EditEntry.menu)
    await _render_edit_dashboard(cq.message, await state.get_data(), edit=True)
    await cq.answer("Amount cleared!")


@router.message(EditEntry.edit_amount)
async def on_save_edit_amount(message: Message, state: FSMContext) -> None:
    text = message.text.strip().replace(",", "")
    try:
        amt = int(float(text) * 100)
    except ValueError:
        await message.reply("Please enter a valid number (e.g. `1250`) or tap Cancel above.")
        return
    data = await state.get_data()
    rid = data["record_id"]
    from squares_vault.db.repositories import update_record
    await update_record(
        record_id=rid,
        user_id=message.from_user.id,
        title=data["title"],
        category=data["category"],
        amount_minor=amt,
        key_date=data.get("key_date"),
        notes=data.get("notes")
    )
    await state.update_data(amount=amt)
    await state.set_state(EditEntry.menu)
    await _render_edit_dashboard(message, await state.get_data(), edit=False)


@router.callback_query(F.data == "edit_field:date", EditEntry.menu)
async def on_edit_field_date(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    date_str = f"`{_fmt_date(data.get('key_date'))}`" if data.get("key_date") else "None"
    today_iso = date.today().isoformat()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Today", callback_data=f"set_edit_date:{today_iso}"),
         InlineKeyboardButton(text="🗑️ Clear Date", callback_data="clear_edit_date")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="edit_cancel_field")]
    ])
    await cq.message.edit_text(
        f"📅 **Edit Key Date**\n\nCurrent: {date_str}\n\n_Type the new date (e.g. `25 sep`, `25-10-2027`), or tap Today / Clear:_ ",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await state.set_state(EditEntry.edit_date)
    await cq.answer()


@router.callback_query(F.data == "clear_edit_date", EditEntry.edit_date)
async def on_clear_edit_date(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    rid = data["record_id"]
    from squares_vault.db.repositories import update_record
    await update_record(
        record_id=rid,
        user_id=cq.from_user.id,
        title=data["title"],
        category=data["category"],
        amount_minor=data.get("amount"),
        key_date=None,
        notes=data.get("notes")
    )
    # Cancel pending reminders
    async with get_db() as db:
        await db.execute("UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'", (rid, cq.from_user.id))
        await db.commit()
    await state.update_data(key_date=None)
    await state.set_state(EditEntry.menu)
    await _render_edit_dashboard(cq.message, await state.get_data(), edit=True)
    await cq.answer("Date cleared & reminders cancelled!")


@router.callback_query(F.data.startswith("set_edit_date:"), EditEntry.edit_date)
async def on_set_edit_button_date(cq: CallbackQuery, state: FSMContext) -> None:
    iso = cq.data.split(":", 1)[1]
    await _apply_new_edit_date(cq, state, iso, is_msg=False)


@router.message(EditEntry.edit_date)
async def on_save_edit_text_date(message: Message, state: FSMContext) -> None:
    parsed = _parse_smart_date(message.text)
    if not parsed:
        await message.reply("❓ Couldn't parse that date. Try `25 sep`, `25-09-2027`, `2027-09-25`:")
        return
    await _apply_new_edit_date(message, state, parsed, is_msg=True)


async def _apply_new_edit_date(target, state: FSMContext, new_date: str, is_msg: bool) -> None:
    data = await state.get_data()
    rid = data["record_id"]
    category = data["category"]
    user_id = target.from_user.id
    from squares_vault.db.repositories import update_record
    await update_record(
        record_id=rid,
        user_id=user_id,
        title=data["title"],
        category=category,
        amount_minor=data.get("amount"),
        key_date=new_date,
        key_date_type="expiry",
        notes=data.get("notes")
    )
    # Reschedule reminders
    async with get_db() as db:
        await db.execute("UPDATE reminders SET state = 'cancelled' WHERE record_id = ? AND user_id = ? AND state = 'pending'", (rid, user_id))
        try:
            from datetime import datetime, timezone
            from squares_vault.reminders.schedule import generate_reminders
            dt_obj = datetime.strptime(new_date[:10], "%Y-%m-%d")
            reminders = generate_reminders(rid, user_id, category, dt_obj)
            now_iso = datetime.now(timezone.utc).isoformat() + "Z"
            for rm in reminders:
                st = "skipped" if rm["fire_at"] < now_iso else "pending"
                await db.execute(
                    "INSERT INTO reminders (id, record_id, user_id, kind, offset_days, fire_at, state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rm["id"], rid, user_id, rm["kind"], rm["offset_days"], rm["fire_at"], st)
                )
        except Exception:
            pass
        await db.commit()

    await state.update_data(key_date=new_date)
    await state.set_state(EditEntry.menu)
    msg_obj = target if is_msg else target.message
    await _render_edit_dashboard(msg_obj, await state.get_data(), edit=not is_msg)
    if not is_msg:
        await target.answer("Date and reminders updated!")


@router.callback_query(F.data == "edit_field:notes", EditEntry.menu)
async def on_edit_field_notes(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    raw_notes = data.get("notes") or ""
    clean_notes = _escape_md(raw_notes[:100] + ("…" if len(raw_notes) > 100 else "")) if raw_notes else "None"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Clear Notes", callback_data="clear_edit_notes")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="edit_cancel_field")]
    ])
    try:
        await _safe_send_or_edit(
            cq.message,
            f"📝 **Edit Notes**\n\nCurrent: {clean_notes}\n\n_Send new notes in chat, or tap Clear:_",
            edit=True,
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await state.set_state(EditEntry.edit_notes)
    finally:
        try:
            await cq.answer()
        except Exception:
            pass


@router.callback_query(F.data == "clear_edit_notes", EditEntry.edit_notes)
async def on_clear_edit_notes(cq: CallbackQuery, state: FSMContext) -> None:
    try:
        data = await state.get_data()
        rid = data["record_id"]
        from squares_vault.db.repositories import update_record
        await update_record(
            record_id=rid,
            user_id=cq.from_user.id,
            title=data["title"],
            category=data["category"],
            amount_minor=data.get("amount"),
            key_date=data.get("key_date"),
            notes=""
        )
        await state.update_data(notes="")
        await state.set_state(EditEntry.menu)
        await _render_edit_dashboard(cq.message, await state.get_data(), edit=True)
    finally:
        try:
            await cq.answer("Notes cleared!")
        except Exception:
            pass


@router.message(EditEntry.edit_notes)
async def on_save_edit_notes(message: Message, state: FSMContext) -> None:
    new_notes = message.text.strip()[:2000]
    data = await state.get_data()
    rid = data["record_id"]
    from squares_vault.db.repositories import update_record
    await update_record(
        record_id=rid,
        user_id=message.from_user.id,
        title=data["title"],
        category=data["category"],
        amount_minor=data.get("amount"),
        key_date=data.get("key_date"),
        notes=new_notes
    )
    await state.update_data(notes=new_notes)
    await state.set_state(EditEntry.menu)
    await _render_edit_dashboard(message, await state.get_data(), edit=False)


@router.callback_query(F.data == "edit_field:attachment", EditEntry.menu)
async def on_edit_field_attachment(cq: CallbackQuery, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="edit_cancel_field")]])
    await cq.message.edit_text(
        "📎 **Add / Replace Attachment**\n\n📸 _Send a photo or PDF document to attach it to this record in Google Drive:_",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await state.set_state(EditEntry.edit_attachment)
    await cq.answer()


@router.message(EditEntry.edit_attachment, F.photo)
async def on_save_edit_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    await _attach_new_file_to_record(message, state, file_id, "image/jpeg", is_msg=True)


@router.message(EditEntry.edit_attachment, F.document)
async def on_save_edit_doc(message: Message, state: FSMContext) -> None:
    mime = message.document.mime_type or "application/octet-stream"
    if mime not in ["application/pdf", "image/jpeg", "image/png"]:
        await message.reply("❌ Please send a PDF or image (JPEG/PNG), or tap Cancel.")
        return
    file_id = message.document.file_id
    await _attach_new_file_to_record(message, state, file_id, mime, is_msg=True)


async def _attach_new_file_to_record(message: Message, state: FSMContext, file_id: str, mime: str, is_msg: bool) -> None:
    data = await state.get_data()
    rid = data["record_id"]
    ext = ".pdf" if "pdf" in mime.lower() else (".png" if "png" in mime.lower() else ".jpg")
    code = generate_coded_name(ext)

    import ulid as _ulid
    async with get_db() as db:
        await db.execute(
            "INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name, mime, size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (str(_ulid.ULID()), rid, message.from_user.id, file_id, code, mime, 0)
        )
        async with db.execute("SELECT count(*) as c FROM files WHERE record_id = ?", (rid,)) as cur:
            f_count = (await cur.fetchone())["c"]
        await db.commit()

    await state.update_data(file_count=f_count)
    await state.set_state(EditEntry.menu)
    await message.reply("📎 Attachment saved and queued for Google Drive sync!")
    await _render_edit_dashboard(message, await state.get_data(), edit=False)

