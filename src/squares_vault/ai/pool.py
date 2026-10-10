"""Multi-Account Gemini API Key Pool with failover and cooldown tracking."""

from __future__ import annotations

import time
import asyncio
from dataclasses import dataclass
from typing import Optional, Sequence

from squares_vault import config
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class PooledKey:
    """State and usage metrics for an individual pooled Gemini API key."""

    key: str
    masked: str
    status: str = "active"  # "active" | "cooling_down"
    cooldown_until: float = 0.0  # monotonic timestamp
    last_used_at: float = 0.0
    requests_today: int = 0
    consecutive_errors: int = 0
    last_error: str = ""

    @classmethod
    def from_raw(cls, raw_key: str) -> PooledKey:
        k = raw_key.strip()
        if len(k) <= 8:
            masked = "***"
        else:
            masked = f"{k[:6]}...{k[-4:]}"
        return cls(key=k, masked=masked)

    @property
    def is_cooling_down(self) -> bool:
        if self.status != "cooling_down":
            return False
        if time.monotonic() >= self.cooldown_until:
            # Cooldown expired, restore active status
            self.status = "active"
            self.cooldown_until = 0.0
            return False
        return True

    @property
    def remaining_cooldown(self) -> float:
        if not self.is_cooling_down:
            return 0.0
        return max(0.0, self.cooldown_until - time.monotonic())


class GeminiKeyPool:
    """Thread-safe and async-safe API key pool for round-robin rotation and failover."""

    def __init__(self, keys: Sequence[str]) -> None:
        if not keys:
            raise ValueError("GeminiKeyPool requires at least one API key.")
        self._keys: list[PooledKey] = [PooledKey.from_raw(k) for k in keys]
        self._current_idx: int = 0
        self._lock = asyncio.Lock()

    @property
    def total_count(self) -> int:
        return len(self._keys)

    @property
    def active_count(self) -> int:
        return sum(1 for k in self._keys if not k.is_cooling_down)

    async def get_active_key(self, exclude: Optional[set[str]] = None) -> str:
        """Return the next available key that is not in cooldown.

        Parameters
        ----------
        exclude : set[str], optional
            Set of keys already attempted in the current request to prevent re-trying.

        Raises
        ------
        RuntimeError
            If all keys in the pool are currently cooling down or excluded.
        """
        async with self._lock:
            now = time.monotonic()
            excluded_keys = exclude or set()
            total = len(self._keys)

            # Try round-robin search starting from current index
            for offset in range(total):
                idx = (self._current_idx + offset) % total
                candidate = self._keys[idx]

                if candidate.key in excluded_keys:
                    continue

                if not candidate.is_cooling_down:
                    # Found ready key! Update current index for round-robin fairness
                    self._current_idx = (idx + 1) % total
                    candidate.last_used_at = now
                    return candidate.key

            # All keys are either in cooldown or excluded.
            # Find the minimum remaining cooldown among non-excluded keys
            eligible = [k for k in self._keys if k.key not in excluded_keys]
            if not eligible:
                raise RuntimeError(
                    f"All {total} Gemini API keys have been attempted and failed for this request."
                )

            min_cd = min(k.remaining_cooldown for k in eligible)
            raise RuntimeError(
                f"All {len(eligible)} available Gemini API keys are in cooldown. "
                f"Next key resets in {min_cd:.1f}s."
            )

    async def mark_rate_limited(
        self,
        key: str,
        error_msg: str,
        cooldown_seconds: float = 60.0,
    ) -> None:
        """Mark a key as rate limited (HTTP 429 / ResourceExhausted)."""
        async with self._lock:
            for k in self._keys:
                if k.key == key:
                    k.status = "cooling_down"
                    k.cooldown_until = time.monotonic() + max(5.0, cooldown_seconds)
                    k.consecutive_errors += 1
                    k.last_error = error_msg
                    log.warning(
                        "gemini_key_cooling_down",
                        key=k.masked,
                        cooldown_seconds=cooldown_seconds,
                        error=error_msg,
                    )
                    break

    async def mark_success(self, key: str) -> None:
        """Record a successful API call for the key, resetting error counters."""
        async with self._lock:
            for k in self._keys:
                if k.key == key:
                    k.status = "active"
                    k.cooldown_until = 0.0
                    k.consecutive_errors = 0
                    k.requests_today += 1
                    break

    def get_status_summary(self) -> list[dict]:
        """Return a structured summary of each key in the pool for admin dashboards."""
        result = []
        for i, k in enumerate(self._keys):
            is_cooling = k.is_cooling_down
            result.append(
                {
                    "index": i + 1,
                    "masked": k.masked,
                    "status": "cooling_down" if is_cooling else "active",
                    "cooldown_remaining_sec": round(k.remaining_cooldown, 1) if is_cooling else 0.0,
                    "requests_today": k.requests_today,
                    "consecutive_errors": k.consecutive_errors,
                    "last_error": k.last_error,
                }
            )
        return result


_pool_instance: Optional[GeminiKeyPool] = None


def get_key_pool() -> GeminiKeyPool:
    """Return the global singleton GeminiKeyPool instance."""
    global _pool_instance  # noqa: PLW0603
    if _pool_instance is None:
        keys = config.settings.gemini_api_keys if config.settings else ()
        if not keys:
            # Fallback if accessed before settings or empty
            keys = (config.settings.gemini_api_key,) if config.settings and config.settings.gemini_api_key else ()
        if not keys:
            raise ValueError("No Gemini API keys found to initialize key pool.")
        _pool_instance = GeminiKeyPool(keys)
    return _pool_instance


def reset_key_pool(keys: Sequence[str]) -> GeminiKeyPool:
    """Reset the pool singleton with new keys (useful on reload or in tests)."""
    global _pool_instance  # noqa: PLW0603
    _pool_instance = GeminiKeyPool(keys)
    return _pool_instance
