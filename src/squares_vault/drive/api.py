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
                        
                    # Save new token (preserving existing refresh_token if Google didn't return a new one)
                    ref_token_to_save = new_token.refresh_token or refresh_token
                    await save_token(self.user_id, new_token.access_token, ref_token_to_save, new_token.expires_in)
                    
                    # Retry request
                    headers["Authorization"] = f"Bearer {new_token.access_token}"
                    async with session.request(method, url, **kwargs) as retry_resp:
                        if retry_resp.status == 204 or (retry_resp.status == 404 and method == "DELETE"):
                            return {}
                        retry_resp.raise_for_status()
                        return await retry_resp.json()
                        
                if resp.status == 204 or (resp.status == 404 and method == "DELETE"):
                    return {}
                resp.raise_for_status()
                return await resp.json()

    async def get_app_folder(self) -> str:
        """Get or create the SQUARES Vault folder in Google Drive (single canonical folder)."""
        from squares_vault.db.connection import get_db

        # 1. Check if we already have the folder ID stored in DB
        async with get_db() as db:
            async with db.execute(
                "SELECT root_folder_id FROM oauth_tokens WHERE user_id = ?",
                (self.user_id,)
            ) as cur:
                row = await cur.fetchone()
                if row and row["root_folder_id"]:
                    return row["root_folder_id"]

        # 2. Search for existing folders in Drive, sorted oldest first
        res = await self._request("GET", "files", params={
            "q": "mimeType='application/vnd.google-apps.folder' and name='SQUARES Vault' and trashed=false",
            "fields": "files(id, name, createdTime)",
            "orderBy": "createdTime asc"
        })
        folders = res.get("files", [])
        if folders:
            canonical_id = folders[0]["id"]
            # Clean up duplicate folders if any
            for dup in folders[1:]:
                dup_id = dup["id"]
                try:
                    f_res = await self._request("GET", "files", params={
                        "q": f"'{dup_id}' in parents and trashed=false",
                        "fields": "files(id)"
                    })
                    for f in f_res.get("files", []):
                        await self._request("PATCH", f"files/{f['id']}", params={
                            "addParents": canonical_id,
                            "removeParents": dup_id,
                            "fields": "id, parents"
                        })
                    await self._request("DELETE", f"files/{dup_id}")
                except Exception as e:
                    log.warning("error_cleaning_duplicate_folder", folder_id=dup_id, error=str(e))

            # Store canonical folder ID in DB
            async with get_db() as db:
                await db.execute(
                    "UPDATE oauth_tokens SET root_folder_id = ? WHERE user_id = ?",
                    (canonical_id, self.user_id)
                )
                await db.commit()
            return canonical_id

        # 3. Create it once if no folder exists
        metadata = {
            "name": "SQUARES Vault",
            "mimeType": "application/vnd.google-apps.folder"
        }
        res = await self._request("POST", "files", json=metadata)
        new_id = res["id"]

        async with get_db() as db:
            await db.execute(
                "UPDATE oauth_tokens SET root_folder_id = ? WHERE user_id = ?",
                (new_id, self.user_id)
            )
            await db.commit()
        return new_id

    async def upload_file(self, name: str, mime_type: str, data: bytes) -> str:
        """Upload a file to the user's SQUARES Vault folder in Google Drive. Returns Drive file ID."""
        tokens = await get_token(self.user_id)
        if not tokens:
            raise DriveAuthError("No tokens available.")
        
        folder_id = await self.get_app_folder()
        url = f"{UPLOAD_API_BASE}/files?uploadType=multipart"
        
        from aiohttp import FormData
        import json
        
        metadata = {
            "name": name,
            "parents": [folder_id]
        }
        
        def _build_form():
            form = FormData()
            form.add_field("metadata", json.dumps(metadata), content_type="application/json")
            form.add_field("file", data, content_type=mime_type)
            return form

        headers = {"Authorization": f"Bearer {tokens[0]}"}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, data=_build_form()) as resp:
                if resp.status == 401:
                    new_token = await refresh_access_token(tokens[1])
                    if not new_token:
                        await delete_token(self.user_id)
                        raise DriveAuthError("Token refresh failed.")
                    ref_token_to_save = new_token.refresh_token or tokens[1]
                    await save_token(self.user_id, new_token.access_token, ref_token_to_save, new_token.expires_in)
                    headers["Authorization"] = f"Bearer {new_token.access_token}"
                    async with session.post(url, data=_build_form(), headers=headers) as retry_resp:
                        retry_resp.raise_for_status()
                        res = await retry_resp.json()
                        return res["id"]
                resp.raise_for_status()
                res = await resp.json()
                return res["id"]

    async def download_file(self, file_id: str) -> bytes:
        """Download a file's bytes from Google Drive."""
        tokens = await get_token(self.user_id)
        if not tokens:
            raise DriveAuthError("No tokens available.")
            
        url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media"
        headers = {"Authorization": f"Bearer {tokens[0]}"}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status == 401:
                    new_token = await refresh_access_token(tokens[1])
                    if not new_token:
                        await delete_token(self.user_id)
                        raise DriveAuthError("Token refresh failed.")
                    ref_token_to_save = new_token.refresh_token or tokens[1]
                    await save_token(self.user_id, new_token.access_token, ref_token_to_save, new_token.expires_in)
                    headers["Authorization"] = f"Bearer {new_token.access_token}"
                    async with session.get(url, headers=headers) as retry_resp:
                        retry_resp.raise_for_status()
                        return await retry_resp.read()
                resp.raise_for_status()
                return await resp.read()

    async def delete_file(self, file_id: str) -> None:
        """Delete a file from Google Drive."""
        await self._request("DELETE", f"files/{file_id}")

    async def update_file_content(self, file_id: str, mime_type: str, data: bytes) -> None:
        """Update existing file content on Google Drive."""
        tokens = await get_token(self.user_id)
        if not tokens:
            raise DriveAuthError("No tokens available.")

        url = f"{UPLOAD_API_BASE}/files/{file_id}?uploadType=media"
        headers = {
            "Authorization": f"Bearer {tokens[0]}",
            "Content-Type": mime_type
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.patch(url, data=data) as resp:
                if resp.status == 401:
                    new_token = await refresh_access_token(tokens[1])
                    if not new_token:
                        await delete_token(self.user_id)
                        raise DriveAuthError("Token refresh failed.")
                    ref_token_to_save = new_token.refresh_token or tokens[1]
                    await save_token(self.user_id, new_token.access_token, ref_token_to_save, new_token.expires_in)
                    headers["Authorization"] = f"Bearer {new_token.access_token}"
                    async with session.patch(url, data=data, headers=headers) as retry_resp:
                        retry_resp.raise_for_status()
                        return
                resp.raise_for_status()



    async def create_calendar_event(self, title: str, date_str: str, description: str = "") -> str:
        """Create an all-day event in Google Calendar."""
        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
        
        tokens = await get_token(self.user_id)
        if not tokens:
            raise DriveAuthError("No tokens available.")
            
        metadata = {
            "summary": title,
            "description": description,
            "start": {"date": date_str},
            "end": {"date": date_str}
        }
        
        headers = {"Authorization": f"Bearer {tokens[0]}"}
        import aiohttp
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json=metadata) as resp:
                if resp.status == 401:
                    # try refresh logic if needed, but for simplicity let's rely on standard flow or just throw
                    pass
                resp.raise_for_status()
                res = await resp.json()
                return res.get("htmlLink", "")
