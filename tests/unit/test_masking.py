"""Unit tests for security/masking.py.

Acceptance criteria (M0):
- mask_identifier: last 4 shown, rest bullets; handles short inputs.
- mask_vehicle_number: keeps first 4 + last 2, masks middle.
- mask_phone: only last 4 digits shown.
- mask_account_ref: same as mask_identifier.
- is_safe_for_log: detects Aadhaar-like and PAN-like strings.
"""

from __future__ import annotations

import pytest

from squares_vault.security.masking import (
    is_safe_for_log,
    mask_account_ref,
    mask_identifier,
    mask_phone,
    mask_vehicle_number,
)

BULLET = "•"


# ── mask_identifier ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "show_last", "expected"),
    [
        ("ABCDE1234F", 4, f"{BULLET * 6}234F"),
        ("1234567890", 4, f"{BULLET * 6}7890"),
        ("1234", 4, "1234"),           # shorter than show_last: reveal all
        ("123", 4, "123"),
        ("", 4, ""),                   # empty input
        ("ABCDE1234F", 6, f"{BULLET * 4}E1234F"),   # show_last=6 → 4 hidden
        ("  ABCDE1234F  ", 4, f"{BULLET * 6}234F"),  # strip whitespace
    ],
)
def test_mask_identifier(value: str, show_last: int, expected: str) -> None:
    assert mask_identifier(value, show_last=show_last) == expected


# ── mask_vehicle_number ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("vehicle_no", "expected"),
    [
        ("MH12AB1234", f"MH12{BULLET * 4}34"),
        ("DL9CAB1234", f"DL9C{BULLET * 4}34"),
        ("KA01MF1234", f"KA01{BULLET * 4}34"),
        # BH series
        ("27BH1234AB", f"27BH{BULLET * 4}AB"),
        # Already short (≤6): returned as-is
        ("MH123", "MH123"),
        # Spaces and hyphens normalised
        ("MH 12 AB 1234", f"MH12{BULLET * 4}34"),
        ("MH-12-AB-1234", f"MH12{BULLET * 4}34"),
    ],
)
def test_mask_vehicle_number(vehicle_no: str, expected: str) -> None:
    assert mask_vehicle_number(vehicle_no) == expected


# ── mask_phone ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("phone", "show_last", "expected"),
    [
        ("+919876543210", 4, f"{BULLET * 8}3210"),
        ("9876543210", 4, f"{BULLET * 6}3210"),
        ("1234", 4, "1234"),
        ("+91 98765 43210", 4, f"{BULLET * 8}3210"),  # spaces stripped
    ],
)
def test_mask_phone(phone: str, show_last: int, expected: str) -> None:
    assert mask_phone(phone, show_last=show_last) == expected


# ── mask_account_ref ──────────────────────────────────────────────────────────


def test_mask_account_ref() -> None:
    assert mask_account_ref("SB123456789") == f"{BULLET * 7}6789"


# ── is_safe_for_log ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Aadhaar-like 12-digit number → NOT safe
        ("Aadhaar: 123456789012", False),
        # PAN-like pattern → NOT safe
        ("PAN: ABCDE1234F", False),
        # Normal log text → safe
        ("record saved category=vehicle", True),
        ("reminder sent user=12345", True),
        # 9-digit number alone is fine (not 10+)
        ("amount 999999999 paise", True),
    ],
)
def test_is_safe_for_log(text: str, expected: bool) -> None:
    assert is_safe_for_log(text) == expected
