"""PII masking helpers.

Blueprint S2 (LOCKED): full ID/policy/account numbers are never stored in
the database; only masked forms are stored (last 4 characters, rest replaced
with '•').  The original number lives only inside the stored document file in
Drive.

Rules
-----
- ``mask_identifier(s)``   → ``"••••1234"``  (always 8 chars output, or
  shorter if input is shorter)
- ``mask_vehicle_number(s)``  → ``"MH12••••34"``
- ``mask_phone(s)``           → ``"••••••0099"``

All functions are pure (no I/O) and safe to call from any layer.
"""

from __future__ import annotations

import re

_BULLET: str = "•"


def mask_identifier(value: str, show_last: int = 4) -> str:
    """Mask *value* showing only the last *show_last* characters.

    Parameters
    ----------
    value:
        The raw identifier (Aadhaar, PAN, passport number, etc.).
    show_last:
        How many trailing characters to reveal.  Default 4 per blueprint.

    Returns
    -------
    str
        Masked string.  Empty input returns empty string.

    Examples
    --------
    >>> mask_identifier("ABCDE1234F")
    '••••••1234F'       # last 4 chars revealed, rest replaced
    >>> mask_identifier("1234", show_last=4)
    '1234'
    """
    value = value.strip()
    if not value:
        return ""
    if len(value) <= show_last:
        return value
    hidden = len(value) - show_last
    return _BULLET * hidden + value[-show_last:]


def mask_vehicle_number(vehicle_no: str) -> str:
    """Mask a vehicle registration number.

    Keeps the state code (2 chars) and series digits (last 4), masks the
    middle portion.

    Examples
    --------
    >>> mask_vehicle_number("MH12AB1234")
    'MH12••••34'
    >>> mask_vehicle_number("27BH1234AB")   # BH series
    '27BH••••AB'
    """
    v = vehicle_no.strip().upper().replace(" ", "").replace("-", "")
    if len(v) <= 6:  # too short to mask meaningfully
        return v
    # Show first 4 and last 2, mask the rest
    prefix = v[:4]
    suffix = v[-2:]
    hidden = _BULLET * (len(v) - 6)
    return f"{prefix}{hidden}{suffix}"


def mask_phone(phone: str, show_last: int = 4) -> str:
    """Mask a phone number showing only the last *show_last* digits.

    Examples
    --------
    >>> mask_phone("+919876543210")
    '••••••••3210'
    """
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= show_last:
        return digits
    hidden = len(digits) - show_last
    return _BULLET * hidden + digits[-show_last:]


def mask_account_ref(ref: str, show_last: int = 4) -> str:
    """Mask a bank account or subscription reference number.

    Examples
    --------
    >>> mask_account_ref("SB123456789")
    '•••••••789'   # show_last defaults to 4
    """
    return mask_identifier(ref, show_last=show_last)


def is_safe_for_log(text: str) -> bool:
    """Return True if *text* appears not to contain raw identifiers.

    This is a best-effort heuristic used to guard accidental logging.  It
    checks for sequences of digits or alphanumeric chars that resemble common
    ID formats.  A ``False`` result means the string should be redacted.

    Note: the scrubbing processor in ``logging_setup`` is the primary guard;
    this function is a secondary check for code that manually builds log
    strings.
    """
    # 10-digit Aadhaar-like sequences
    if re.search(r"\b\d{10,12}\b", text):
        return False
    # PAN-like patterns
    if re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", text):
        return False
    return True
