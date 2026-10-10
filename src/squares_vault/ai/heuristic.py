"""Offline Heuristic Parser.

Extracts structured metadata (dates, amounts, vehicle plates, categories)
using regex and offline PDF text extraction (PyMuPDF) without making any AI API calls.
Acts as a zero-token fallback when all Gemini API keys are exhausted or rate limited.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Union

import pymupdf
import dateparser

from squares_vault.ai.schema import ExtractedRecord
from squares_vault.ai.validate import normalize_vehicle_number, validate_and_enrich
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

# Category regex markers
CATEGORY_KEYWORDS = [
    ("vehicle", re.compile(r"\b(puc|rc|pollution under control|registration certificate|form 23|chassis no|engine no|rto|motor vehicle)\b", re.I)),
    ("insurance", re.compile(r"\b(insurance|policy schedule|policy no|sum insured|premium amount|first loss|insur(?:er|ed))\b", re.I)),
    ("warranty & purchases", re.compile(r"\b(warranty|guarantee|tax invoice|bill of supply|serial no|order id|purchase invoice)\b", re.I)),
    ("bills & subscriptions", re.compile(r"\b(electricity bill|water bill|piped gas|broadband|airtel|jio|bsnl|consumer no|bill date|due date)\b", re.I)),
    ("ids & government docs", re.compile(r"\b(aadhaar|pan card|income tax department|permanent account number|passport|voter id|driving licen[cs]e)\b", re.I)),
    ("health", re.compile(r"\b(hospital|prescription|diagnostic|patient|pathology|doctor|clinic|rx|lab report)\b", re.I)),
]

VEHICLE_PLATE_REGEX = re.compile(
    r"\b([A-Z]{2}[ -]?[0-9]{1,2}[ -]?[A-Z]{1,3}[ -]?[0-9]{4}|\d{2}[ -]?BH[ -]?\d{4}[ -]?[A-Z]{1,2})\b",
    re.I,
)

AMOUNT_REGEX = re.compile(
    r"(?:(?:₹|Rs\.?|INR)\s*|Total(?:\s+Amount)?[:\s]*|Grand Total[:\s]*|Net Amount[:\s]*|Amount Due[:\s]*)([\d,]+(?:\.\d{2})?)",
    re.I,
)

DATE_PATTERNS = [
    re.compile(r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b"),
    re.compile(r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"),
    re.compile(r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})\b", re.I),
]


def extract_text_from_bytes(file_bytes: Union[bytes, list[bytes]]) -> str:
    """Extract plain text from PDF or raw bytes."""
    texts = []
    items = file_bytes if isinstance(file_bytes, list) else [file_bytes]

    for b in items:
        if b[:4] == b"%PDF":
            try:
                doc = pymupdf.open(stream=b, filetype="pdf")
                for page in doc:
                    texts.append(page.get_text())
            except Exception as e:
                log.warning("pymupdf_extract_error", error=str(e))
        else:
            # For non-PDF byte data, try decoding text or extracting printable strings
            try:
                decoded = b.decode("utf-8", errors="ignore")
                # If decoded text looks like mostly text rather than binary noise
                clean_lines = [l.strip() for l in decoded.splitlines() if l.strip()]
                if clean_lines and sum(len(l) for l in clean_lines) >= 10:
                    texts.append("\n".join(clean_lines))
                else:
                    printable = "".join(chr(c) if 32 <= c <= 126 or c in (10, 13) else " " for c in b)
                    words = [w for w in printable.split() if len(w) >= 2]
                    if len(words) > 5:
                        texts.append(" ".join(words[:200]))
            except Exception:
                pass

    return "\n".join(texts)


def _parse_date_string(raw: str) -> Optional[str]:
    """Parse date string into YYYY-MM-DD."""
    try:
        parsed = dateparser.parse(raw, settings={"PREFER_DAY_OF_MONTH": "first"})
        if parsed:
            return parsed.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def parse_offline_document(
    file_bytes: Union[bytes, list[bytes]],
    fallback_title: str = "Scanned Document",
) -> ExtractedRecord:
    """Heuristic offline parser extracting key metadata using regex without any external API."""
    full_text = extract_text_from_bytes(file_bytes)

    # 1. Detect Vehicle Number
    vehicle_num = None
    v_match = VEHICLE_PLATE_REGEX.search(full_text)
    if v_match:
        vehicle_num = normalize_vehicle_number(v_match.group(1))

    # 2. Detect Category
    category = "custom"
    subtype = None
    if vehicle_num:
        category = "vehicle"
        subtype = "RC / Vehicle Doc"
    else:
        for cat_name, pattern in CATEGORY_KEYWORDS:
            if pattern.search(full_text):
                category = cat_name
                break

    # 3. Detect Amount
    amount_minor = None
    amt_matches = AMOUNT_REGEX.findall(full_text)
    for raw_amt in amt_matches:
        clean_amt = raw_amt.replace(",", "")
        try:
            val = float(clean_amt)
            if 0 < val < 100_000_000:  # reasonable upper bound (1 crore)
                amount_minor = int(round(val * 100))
                break
        except ValueError:
            continue

    # 4. Detect Dates
    key_date = None
    key_date_type = None
    purchase_date = None

    lines = full_text.splitlines()
    for line in lines:
        for pattern in DATE_PATTERNS:
            matches = pattern.findall(line)
            for raw_dt in matches:
                iso_dt = _parse_date_string(raw_dt)
                if not iso_dt:
                    continue

                line_lower = line.lower()
                if any(w in line_lower for w in ("valid till", "expiry", "expires", "due date", "valid upto", "end date")):
                    # Expiry / Due dates are the highest priority for key_date
                    key_date = iso_dt
                    key_date_type = "expiry"
                elif any(w in line_lower for w in ("purchase date", "invoice date", "bill date", "date of issue", "issued on", "test date")):
                    if not purchase_date:
                        purchase_date = iso_dt
                elif not key_date:
                    key_date = iso_dt
                    key_date_type = "due"

    # 5. Formulate Title
    if vehicle_num:
        title = f"Vehicle Document – {vehicle_num}"
    elif category != "custom":
        title = f"{category.title()} Document"
    else:
        title = fallback_title

    # 6. Notes
    notes_lines = ["⚠️ Extracted via Offline Heuristic Parser (API limit / offline fallback)."]
    if vehicle_num:
        notes_lines.append(f"• Detected Plate: {vehicle_num}")
    if amount_minor:
        notes_lines.append(f"• Detected Amount: ₹{amount_minor / 100:.2f}")

    record = ExtractedRecord(
        category=category,
        subtype=subtype,
        title=title,
        key_date=key_date,
        key_date_type=key_date_type,
        amount_minor=amount_minor,
        currency="INR",
        vehicle_number=vehicle_num,
        purchase_date=purchase_date,
        notes="\n".join(notes_lines),
    )

    return validate_and_enrich(record)
