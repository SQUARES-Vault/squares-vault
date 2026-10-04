"""Rate limit middleware.

Blueprint S6: limits at 20 msg/min and 5 uploads/min per user.
This is an in-memory sliding window or token bucket. For simplicity in a
single-VM setup, we use an in-memory deque-based sliding window.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Dict, Deque

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message

from squares_vault.i18n.loader import t
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

# User ID -> list of timestamps
_message_times: dict[int, deque[float]] = defaultdict(deque)
_upload_times: dict[int, deque[float]] = defaultdict(deque)

MSG_LIMIT = 20
UPLOAD_LIMIT = 5
WINDOW_SEC = 60.0


class RateLimitMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        user_id = event.from_user.id
        now = time.monotonic()

        # Clean old
        m_q = _message_times[user_id]
        while m_q and m_q[0] < now - WINDOW_SEC:
            m_q.popleft()
            
        u_q = _upload_times[user_id]
        while u_q and u_q[0] < now - WINDOW_SEC:
            u_q.popleft()

        is_upload = bool(event.photo or event.document)

        if len(m_q) >= MSG_LIMIT or (is_upload and len(u_q) >= UPLOAD_LIMIT):
            log.warning("rate_limit_exceeded", user_id=user_id, is_upload=is_upload)
            await event.answer(t("err_rate_limit"))
            return None  # Drop update

        m_q.append(now)
        if is_upload:
            u_q.append(now)

        return await handler(event, data)
