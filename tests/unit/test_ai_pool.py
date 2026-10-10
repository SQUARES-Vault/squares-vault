"""Unit tests for GeminiKeyPool, offline heuristic parser, and quota summary."""

import pytest
import asyncio
from unittest.mock import patch

from squares_vault.ai.pool import GeminiKeyPool, PooledKey
from squares_vault.ai.heuristic import parse_offline_document


def test_pooled_key_masking():
    pk = PooledKey.from_raw("AIzaSyDk1234567890TestKey999")
    assert pk.masked.startswith("AIzaSy...")
    assert pk.masked.endswith("tKey999"[-4:])
    assert pk.status == "active"
    assert not pk.is_cooling_down


@pytest.mark.asyncio
async def test_key_pool_round_robin():
    keys = ["KEY_ALPHA_12345", "KEY_BETA_67890", "KEY_GAMMA_11111"]
    pool = GeminiKeyPool(keys)
    assert pool.total_count == 3
    assert pool.active_count == 3

    k1 = await pool.get_active_key()
    k2 = await pool.get_active_key()
    k3 = await pool.get_active_key()
    k4 = await pool.get_active_key()

    assert k1 == keys[0]
    assert k2 == keys[1]
    assert k3 == keys[2]
    assert k4 == keys[0]  # wrapped around


@pytest.mark.asyncio
async def test_key_pool_failover_on_rate_limit():
    keys = ["KEY_ALPHA_12345", "KEY_BETA_67890"]
    pool = GeminiKeyPool(keys)

    # Key 0 gets rate limited
    await pool.mark_rate_limited(keys[0], "HTTP 429 ResourceExhausted", cooldown_seconds=30.0)

    # Now active_count is 1
    assert pool.active_count == 1

    # Requesting key should immediately skip Key 0 and return Key 1
    selected = await pool.get_active_key()
    assert selected == keys[1]

    # Another request should still return Key 1 because Key 0 is cooling down
    selected2 = await pool.get_active_key()
    assert selected2 == keys[1]


@pytest.mark.asyncio
async def test_key_pool_all_cooling_down_raises_runtime_error():
    keys = ["KEY_ONE_12345", "KEY_TWO_67890"]
    pool = GeminiKeyPool(keys)

    await pool.mark_rate_limited(keys[0], "Quota reached", cooldown_seconds=60.0)
    await pool.mark_rate_limited(keys[1], "Quota reached", cooldown_seconds=60.0)

    assert pool.active_count == 0

    with pytest.raises(RuntimeError) as exc_info:
        await pool.get_active_key()

    assert "All 2 available Gemini API keys are in cooldown" in str(exc_info.value)


@pytest.mark.asyncio
async def test_key_pool_mark_success():
    keys = ["KEY_ONE_12345"]
    pool = GeminiKeyPool(keys)

    await pool.mark_rate_limited(keys[0], "Temporary error", cooldown_seconds=60.0)
    assert pool.active_count == 0

    await pool.mark_success(keys[0])
    assert pool.active_count == 1
    summary = pool.get_status_summary()
    assert summary[0]["status"] == "active"
    assert summary[0]["requests_today"] == 1


def test_offline_heuristic_parser():
    doc_text = """
    VEHICLE POLLUTION CERTIFICATE
    Registration No: MH 12 AB 1234
    Test Date: 2026-10-10
    Valid Till: 2027-04-10
    Fee Paid: INR 150.00
    """
    rec = parse_offline_document(doc_text.encode("utf-8"))
    assert rec.category == "vehicle"
    assert rec.vehicle_number == "MH12AB1234"
    assert rec.amount_minor == 15000  # 150 INR in paise
    assert rec.key_date == "2027-04-10"
    assert rec.key_date_type == "expiry"
    assert "Offline Heuristic Parser" in rec.notes
