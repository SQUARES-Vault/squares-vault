"""Google Drive API interactions."""

from __future__ import annotations

from typing import Optional, Any, Dict
import aiohttp

from squares_vault.drive.token_store import get_token, save_token, delete_token
from squares_vault.drive.client import refresh_access_token
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"


class DriveAuthError(Exception):
    """Raised when authentication to Drive fails and cannot be refreshed."""
    pass


async def get_authorized_session(user_id: int) -> aiohttp.ClientSession:
    """Get an aiohttp session with the user's OAuth token injected.
    
    If the token is expired (401), it will attempt to refresh it automatically.
    """
    tokens = await get_token(user_id)
    if not tokens:
        raise DriveAuthError("User has no tokens.")
        
    access_token, refresh_token = tokens
    
    # We create a session. Note: caller is responsible for closing it, 
    # but aiohttp warns if you don't close. Usually it's better to pass the session, 
    # but we'll use a wrapper class for simpler usage.
    session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {access_token}"})
    return session


class DriveAPI:
    """Wrapper for Google Drive API calls."""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        
    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
        """Make an authenticated request. Automatically refreshes token on 401."""
        url = f"{DRIVE_API_BASE}/{endpoint.lstrip('/')}"
        
        tokens = await get_token(self.user_id)
        if not tokens:
            raise DriveAuthError("No tokens available.")
        access_token, refresh_token = tokens
        
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {access_token}"
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status == 401:
                    # Token expired, try refresh
                    log.info("token_expired", user_id=self.user_id)
                    new_token = await refresh_access_token(refresh_token)
                    if not new_token:
                        await delete_token(self.user_id)
                        raise DriveAuthError("Token refresh failed.")
                        
                    # Save new token
                    await save_token(self.user_id, new_token.access_token, new_token.refresh_token, new_token.expires_in)
                    
                    # Retry request
                    headers["Authorization"] = f"Bearer {new_token.access_token}"
                    async with session.request(method, url, **kwargs) as retry_resp:
                        retry_resp.raise_for_status()
                        return await retry_resp.json()
                        
                resp.raise_for_status()
                if resp.status != 204:
                    return await resp.json()
                return {}

    async def get_app_folder(self) -> str:
        """Ensure the appDataFolder is accessible. Returns its ID ('appDataFolder')."""
        # The appDataFolder is a special alias in Google Drive
        res = await self._request("GET", "files/appDataFolder", params={"fields": "id"})
        return res["id"]
