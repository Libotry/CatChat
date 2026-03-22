"""Feishu tenant_access_token management with auto-refresh."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TokenInfo:
    """Token data with expiration tracking."""
    access_token: str
    expires_at: float  # Unix timestamp


class FeishuTokenManager:
    """
    Singleton for managing Feishu tenant_access_token.

    Token expires in 2 hours. This manager:
    - Caches the token in memory
    - Auto-refreshes when remaining validity < 5 minutes
    - Is thread-safe via asyncio.Lock
    """

    TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: Optional[TokenInfo] = None
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        """
        Get a valid access token, refreshing if necessary.

        Returns:
            Valid tenant_access_token string
        """
        async with self._lock:
            if self._token is None or self._needs_refresh():
                await self._refresh()

            return self._token.access_token

    def _needs_refresh(self) -> bool:
        """Check if token needs refresh (missing or expires within 5 min)."""
        if self._token is None:
            return True
        # Refresh 5 minutes before expiry
        return time.time() + 300 > self._token.expires_at

    async def _refresh(self) -> None:
        """Fetch a new token from Feishu."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.TOKEN_URL,
                    json={
                        "app_id": self.app_id,
                        "app_secret": self.app_secret,
                    },
                )
            data = resp.json()
            code = data.get("code")
            if code != 0:
                logger.error("Feishu token refresh failed: code=%s msg=%s", code, data.get("msg"))
                raise RuntimeError(f"Feishu token refresh error: {data.get('msg')}")

            expire = data.get("expire", 7200)
            self._token = TokenInfo(
                access_token=data["tenant_access_token"],
                expires_at=time.time() + expire - 60,  # 60s buffer
            )
            logger.info(
                "Feishu token refreshed, expires in %ds", expire
            )
        except httpx.RequestError as e:
            logger.error("Failed to reach Feishu token endpoint: %s", e)
            raise RuntimeError(f"Network error during token refresh: {e}") from e

    async def force_refresh(self) -> str:
        """Force refresh token immediately."""
        async with self._lock:
            await self._refresh()
            return self._token.access_token


# Module-level singleton
_token_manager: Optional[FeishuTokenManager] = None


def get_token_manager(app_id: str, app_secret: str) -> FeishuTokenManager:
    global _token_manager
    if _token_manager is None:
        _token_manager = FeishuTokenManager(app_id, app_secret)
    return _token_manager
