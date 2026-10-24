"""Gemini API implementation – robust JSON extraction with retry."""

from __future__ import annotations

import json
import base64
import asyncio
import aiohttp
from typing import Optional

from squares_vault import config
from squares_vault.logging_setup import get_logger
from squares_vault.ai.schema import ExtractedRecord
from squares_vault.ai.validate import validate_and_enrich

log = get_logger(__name__)

SYSTEM_PROMPT = """You are a highly secure document extraction engine for SQUARES Vault.
Your job is to read user-provided documents (images or PDFs) and extract structured data.
Rules:
- Do NOT hallucinate data. If a field is missing, leave it null.
- Dates MUST be in YYYY-MM-DD format.
- Classify the document into exactly one of these categories:
  vehicle | warranty & purchases | people & occasions | bills & subscriptions |
  insurance | ids & government docs | health | home | custom
- Extract all amounts as numbers in the SMALLEST currency unit (paise for INR).
- In the notes field, extract ONLY a brief summary of the main items/services and their quantities formatted strictly as a clean newline-separated bulleted list (e.g., '\n- 4x AC service\n- 1x Washing machine repair'). Do NOT include detailed tax breakdowns, IGST, CGST, or complex invoice metadata.
"""


class AIProviderError(Exception):
    pass


async def _get_user_custom_key(user_id: int) -> Optional[str]:
    from squares_vault.db.connection import get_db
    from squares_vault.security.crypto import decrypt_secret, load_master_key

    async with get_db() as db:
        async with db.execute(
            "SELECT api_key_enc, dek_wrapped FROM user_keys WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                master_key = load_master_key(config.settings.master_key_path)
                return decrypt_secret(master_key, row["api_key_enc"], row["dek_wrapped"]).decode("utf-8")
    return None


def _detect_mime(file_bytes: bytes) -> str:
    """Detect mime type from file header bytes."""
    if file_bytes[:4] == b"%PDF":
        return "application/pdf"
    if file_bytes[:3] in (b"\xff\xd8\xff",):
        return "image/jpeg"
    if file_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # Default to jpeg for processed images
    return "image/jpeg"


async def extract_document_data(
    user_id: int, file_bytes: bytes | list[bytes], mime_type: Optional[str] = None
) -> ExtractedRecord:
    """Send document (single or multi-page) to Gemini and parse structured output.

    Rotates seamlessly across pooled API keys if HTTP 429 / quota limits are hit.
    Falls back to offline heuristic parser if all keys are exhausted.
    """
    from squares_vault.ai.pool import get_key_pool
    from squares_vault.ai.heuristic import parse_offline_document

    user_custom_key = await _get_user_custom_key(user_id)
    schema_dict = ExtractedRecord.model_json_schema()

    parts = [
        {
            "text": (
                f"Extract all details from this document (which may have multiple pages/images) and return a JSON object "
                f"matching this schema exactly:\n{json.dumps(schema_dict)}\n\n"
                f"Important: put ALL line items, quantities, amounts across all pages in the 'notes' field."
            )
        }
    ]

    from squares_vault.ops.office import extract_text_from_document

    files_list = file_bytes if isinstance(file_bytes, list) else [file_bytes]
    for fb in files_list:
        m = mime_type if (mime_type and mime_type != "application/octet-stream") else _detect_mime(fb)
        extracted_txt = extract_text_from_document(fb, mime=m)
        if extracted_txt:
            parts.append({"text": f"--- Document Content ---\n{extracted_txt}"})
        else:
            b64 = base64.b64encode(fb).decode("utf-8")
            parts.append({"inline_data": {"mime_type": m, "data": b64}})

    payload = {
        "system_instruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.0,
        },
    }

    raw_models = [
        config.settings.ai_model_name,
        *config.settings.ai_fallback_chain,
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-flash-latest",
        "gemini-3.8-flash",
    ]
    # Deduplicate while preserving order
    models_to_try = []
    for m in raw_models:
        if m and m not in models_to_try:
            models_to_try.append(m)

    pool = get_key_pool()
    max_key_attempts = 1 if user_custom_key else max(1, pool.total_count)
    attempted_keys: set[str] = set()
    last_error = None

    for _ in range(max_key_attempts):
        if user_custom_key:
            current_key = user_custom_key
        else:
            try:
                current_key = await pool.get_active_key(exclude=attempted_keys)
            except RuntimeError as e:
                log.warning("gemini_pool_no_active_keys", error=str(e))
                break

        attempted_keys.add(current_key)
        key_rate_limited = False

        for model_to_use in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_use}:generateContent?key={current_key}"

            for attempt in range(2):
                if attempt > 0:
                    await asyncio.sleep(1.5 * attempt)

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                        ) as resp:
                            if resp.status in (429, 503):
                                last_error = f"{resp.status} on {model_to_use}"
                                log.warning(
                                    "gemini_quota_or_capacity",
                                    status=resp.status,
                                    model=model_to_use,
                                    key_masked=current_key[:6] + "..." if len(current_key) > 6 else "***",
                                )
                                # Mark key as cooling down in pool
                                if not user_custom_key:
                                    await pool.mark_rate_limited(
                                        current_key,
                                        f"HTTP {resp.status} on {model_to_use}",
                                        cooldown_seconds=60.0,
                                    )
                                key_rate_limited = True
                                break  # Switch to next key immediately upon 429/503

                            if resp.status == 404:
                                last_error = f"404 on {model_to_use}"
                                log.warning("gemini_404_model_not_found", model=model_to_use)
                                break  # Model doesn't exist, try next model with same key

                            if resp.status != 200:
                                text = await resp.text()
                                log.error("gemini_api_error", status=resp.status, text=text, model=model_to_use)
                                raise AIProviderError(f"Gemini API returned {resp.status}: {text[:200]}")

                            data = await resp.json()

                            try:
                                content_text = data["candidates"][0]["content"]["parts"][0]["text"]
                                parsed_json = json.loads(content_text)
                                record = ExtractedRecord(**parsed_json)
                                record = validate_and_enrich(record)
                                if not user_custom_key:
                                    await pool.mark_success(current_key)
                                log.info(
                                    "gemini_extraction_success",
                                    title=record.title,
                                    category=record.category,
                                    model=model_to_use,
                                )
                                return record
                            except (KeyError, IndexError, json.JSONDecodeError) as e:
                                log.error("gemini_parse_error", error=str(e), raw=str(data)[:300])
                                raise AIProviderError(f"Failed to parse Gemini response: {e}")
                            except Exception as e:
                                log.error("gemini_validation_error", error=str(e))
                                raise AIProviderError(f"Validation failed: {e}")

                except AIProviderError:
                    raise
                except Exception as e:
                    last_error = str(e)
                    log.warning("gemini_network_error", attempt=attempt + 1, error=str(e), model=model_to_use)

            if key_rate_limited:
                # Break model loop to move to next key in key pool
                break

    # If all pooled keys failed, try the offline heuristic fallback parser
    log.warning("all_gemini_keys_exhausted_attempting_offline_heuristic", last_error=last_error)
    try:
        heuristic_record = parse_offline_document(file_bytes)
        log.info("offline_heuristic_fallback_success", title=heuristic_record.title)
        return heuristic_record
    except Exception as e:
        log.error("offline_heuristic_failed", error=str(e))

    raise AIProviderError(f"All Gemini keys exhausted and offline fallback failed: {last_error}")


QA_SYSTEM_PROMPT = """You are the intelligent executive assistant for SQUARES Vault, a private document management and financial vault.
Answer the user's questions about their documents, vehicles, expenses, warranties, bills, or deadlines with high intelligence, clarity, and precision based STRICTLY on the vault context provided.

Domain Scope & Executive Role:
- You are a private advisor and intelligence assistant for personal and business records, documents, finances, and assets stored in the user's vault.
- You CAN deeply analyze, summarize, explain, compare, and extract insights from ANY document in the vault (including resumes, business reports, balance sheets, contracts, loan letters, and insurance policies).
- You can explain technical concepts, job skills, legal clauses, or financial metrics that appear in the user's documents.
- Software Code & External Programming Tasks:
  * Do NOT generate full programming scripts, software codebases, or act as an IDE / general code generator (e.g. writing full Python scripts, web scrapers, or applications).
  * If the user asks you to write code or build a script based on their document (e.g., asking for Python scripts for their resume or SQL workflows), acknowledge the relevant skills and concepts from their document at a high conceptual level, but politely explain that as their Vault Assistant, your focus is on document intelligence, asset tracking, and record analysis rather than software engineering. Gracefully steer back to their documents.
- General / Out-of-Scope Requests:
  * If asked unrelated general knowledge or external tasks (e.g. essays, riddles, world trivia), give a polite, brief 1-sentence acknowledgment and courteously pivot back to assisting with their vault records, vehicles, and upcoming deadlines.
  * Never use harsh, robotic error messages or rigid "access denied" phrasing. Keep the tone executive, polished, and helpful.

Financial & Expense Intelligence:
- When asked about "spending", "expenses", or "how much I spent":
  * ONLY calculate actual outgoing expenses (e.g. bills, recharges, purchases, maintenance, fees, subscriptions).
  * Do NOT count employment salaries, CTC, or income offer letters as expenses! (If relevant, you may mention them separately as income/salary).
  * Always calculate and state the EXACT grand total at the very top in bold with currency: e.g. **Total Spent: ₹8,319.00**.
  * Provide an itemized breakdown of every document contributing to that total.

Cross-Document & Entity Intelligence:
- Connect related records seamlessly across identifiers:
  * When asked about a vehicle or asset (e.g. "Venue" or "Honda City"), identify its registration number / number plate (e.g. "UP15EB3113") and connect ALL documents associated with that vehicle, including Registration Certificates (RC), vehicle insurance, pollution certificates (PUC), service bills, and traffic challans that share the same vehicle name OR registration plate.
  * If a traffic challan or service bill mentions the vehicle's registration number (e.g. "Challan Rohini - UP15EB3113"), deduce that it belongs to that vehicle and include it in the response!
  * Explicitly note the relationship (e.g. "Here are the documents for your Hyundai Venue (UP15EB3113):").

Formatting Guidelines:
- State the direct answer or total in the very first sentence.
  * When referencing vehicle numbers, codes, or identifiers, format in monospace: e.g. `UP15EB3113`.
- When listing documents or items, format each document in a clean 2-line card layout with bold title and bullet-separated metadata:
  • **Document Title** (ID: <id>)
    📅 <Date in DD Mon YYYY> • 💰 <Amount in ₹> • 🏷️ <Category>
  * If amount is not applicable, omit the 💰 part completely.
  * Never cram title and metadata onto one long line.
- NEVER generate horizontal divider lines (no '---', '___', '***', or '—').
- Be thorough and concise. Never leave a response half-finished or cut off.
- If no records match, give a friendly 1-2 sentence notice.

Instructions:
1. Dates and Timeframes:
   - Interpret relative time expressions (e.g. 'today', 'tomorrow', 'this year', 'in 2026', 'next 3 months') accurately relative to TODAY'S DATE.
   - For queries about deadlines or expiries, check the key dates of the records and find all that match.
2. Citing Records:
   - When referencing or listing specific records in your response, ALWAYS cite the record ID immediately after the record title in the exact format: (ID: <id>)
   - Example: • **Hyundai RSA Certificate** (ID: 01M4FS5...)
3. Financial / Calculations:
   - Sum amounts correctly and format in INR (₹).
4. Strictly Factual:
   - Never invent or hallucinate records, dates, or details not present in the context.
"""


async def answer_vault_query(user_id: int, query: str, context_text: str) -> str:
    """Synthesize a natural language answer to user's question using pooled Gemini keys."""
    from squares_vault.ai.pool import get_key_pool
    from squares_vault.ai.quota import check_and_increment_quota, QuotaExceededError, GlobalCeilingExceededError
    from squares_vault.bot.handlers.admin import get_system_setting

    # Check emergency AI kill-switch
    if (await get_system_setting("ai_enabled", "1")) != "1":
        return "⏸️ The Vault AI assistant is temporarily paused by the administrator for maintenance. Your records remain safe and searchable via Search and Browse."

    try:
        await check_and_increment_quota(user_id)
    except (QuotaExceededError, GlobalCeilingExceededError) as e:
        log.warning("ask_vault_quota_blocked", user_id=user_id, error=str(e))
        return "⚠️ Daily AI request limit reached. Please try again tomorrow."

    user_custom_key = await _get_user_custom_key(user_id)
    pool = get_key_pool()

    payload = {
        "system_instruction": {"parts": [{"text": QA_SYSTEM_PROMPT}]},
        "contents": [
            {
                "parts": [
                    {
                        "text": f"--- STORED VAULT RECORDS ---\n{context_text}\n\n--- USER QUESTION ---\n{query}"
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1500,
        },
    }

    raw_models = [
        config.settings.ai_model_name,
        *config.settings.ai_fallback_chain,
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.8-flash",
        "gemini-flash-latest",
    ]
    models_to_try = []
    for m in raw_models:
        if m and m not in models_to_try:
            models_to_try.append(m)

    max_key_attempts = 1 if user_custom_key else max(1, pool.total_count)
    attempted_keys: set[str] = set()

    for _ in range(max_key_attempts):
        if user_custom_key:
            current_key = user_custom_key
        else:
            try:
                current_key = await pool.get_active_key(exclude=attempted_keys)
            except RuntimeError:
                break
        attempted_keys.add(current_key)
        key_rate_limited = False

        for model_to_use in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_use}:generateContent?key={current_key}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                        if resp.status in (429, 503):
                            if not user_custom_key:
                                await pool.mark_rate_limited(
                                    current_key,
                                    f"HTTP {resp.status} on {model_to_use}",
                                    cooldown_seconds=60.0,
                                )
                            key_rate_limited = True
                            break
                        if resp.status == 200:
                            data = await resp.json()
                            ans = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                            if not user_custom_key:
                                await pool.mark_success(current_key)
                            return ans
            except Exception as e:
                log.warning("qa_network_error", model=model_to_use, error=str(e))

            if key_rate_limited:
                break

    # Fallback if Gemini unavailable
    return "⚠️ I couldn't reach the AI service right now, but here are the matching records from your vault above."
