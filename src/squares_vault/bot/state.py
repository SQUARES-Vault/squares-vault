"""SQLite-backed FSM storage for aiogram.

Stores FSM state in our `user_state` table with expiry.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, cast

from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from aiogram.fsm.state import State

from squares_vault.db.connection import get_db

STATE_EXPIRY_MINUTES = 30


class SqliteStorage(BaseStorage):
    """FSM storage connected to `user_state` table.
    
    States expire after 30 minutes.
    """

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        async with get_db() as db:
            if state is None:
                await db.execute("DELETE FROM user_state WHERE user_id = ?", (key.user_id,))
            else:
                expires_at = (datetime.now(timezone.utc) + timedelta(minutes=STATE_EXPIRY_MINUTES)).isoformat()
                state_str = state.state if isinstance(state, State) else str(state)
                
                await db.execute(
                    """
                    INSERT INTO user_state (user_id, state, expires_at) 
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET 
                        state = excluded.state,
                        expires_at = excluded.expires_at
                    """,
                    (key.user_id, state_str, expires_at)
                )
            await db.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        async with get_db() as db:
            async with db.execute(
                "SELECT state, expires_at FROM user_state WHERE user_id = ?", 
                (key.user_id,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if datetime.now(timezone.utc) > expires_at:
                        await db.execute("DELETE FROM user_state WHERE user_id = ?", (key.user_id,))
                        await db.commit()
                        return None
                    return cast(str, row["state"])
                return None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        async with get_db() as db:
            data_json = json.dumps(data)
            await db.execute(
                """
                UPDATE user_state SET data_json = ? WHERE user_id = ?
                """,
                (data_json, key.user_id)
            )
            await db.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        async with get_db() as db:
            async with db.execute(
                "SELECT data_json, expires_at FROM user_state WHERE user_id = ?", 
                (key.user_id,)
            ) as cur:
                row = await cur.fetchone()
                if row and row["data_json"]:
                    expires_at = datetime.fromisoformat(row["expires_at"])
                    if datetime.now(timezone.utc) > expires_at:
                        return {}
                    return cast(Dict[str, Any], json.loads(row["data_json"]))
                return {}

    async def close(self) -> None:
        pass
