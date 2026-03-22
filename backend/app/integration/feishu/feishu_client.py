"""Feishu API client for sending messages and querying data."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from .token_manager import FeishuTokenManager

logger = logging.getLogger(__name__)


class FeishuAPIError(RuntimeError):
    """Raised when a Feishu API call fails."""

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"Feishu API error {code}: {msg}")


class FeishuClient:
    """
    Feishu API client.

    All methods use the shared token manager to get a valid
    tenant_access_token automatically.
    """

    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, token_manager: FeishuTokenManager):
        self._tm = token_manager
        self._http = httpx.AsyncClient(timeout=30.0)

    async def _headers(self) -> Dict[str, str]:
        token = await self._tm.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        url = f"{self.BASE_URL}{path}"
        resp = await self._http.get(url, headers=await self._headers(), params=params)
        data = resp.json()
        self._check_code(data)
        return data

    async def _post(self, path: str, json_data: Dict) -> Dict:
        url = f"{self.BASE_URL}{path}"
        resp = await self._http.post(
            url, headers=await self._headers(), json=json_data
        )
        data = resp.json()
        self._check_code(data)
        return data

    @staticmethod
    def _check_code(data: Dict) -> None:
        code = data.get("code")
        if code != 0:
            raise FeishuAPIError(
                code=code,
                msg=data.get("msg", "unknown error"),
            )

    # ─── Message Sending ─────────────────────────────────────────

    async def send_text(
        self,
        receive_id: str,
        receive_id_type: str,
        content: str,
    ) -> str:
        """
        Send a text message.

        Args:
            receive_id: ID of the recipient (chat_id, open_id, or user_id)
            receive_id_type: One of "chat_id", "open_id", "user_id"
            content: Message text content

        Returns:
            message_id of the sent message
        """
        payload = {
            "receive_id": receive_id,
            "receive_id_type": receive_id_type,
            "msg_type": "text",
            "content": json.dumps({"text": content}),
        }
        data = await self._post("/im/v1/messages", payload)
        return data.get("data", {}).get("message_id", "")

    async def send_post(
        self,
        receive_id: str,
        receive_id_type: str,
        post_content: Dict,
    ) -> str:
        """
        Send a rich text (post) message.

        Args:
            receive_id: ID of the recipient
            receive_id_type: "chat_id", "open_id", or "user_id"
            post_content: Feishu post content dict (from message_mapper.to_feishu())

        Returns:
            message_id of the sent message
        """
        payload = {
            "receive_id": receive_id,
            "receive_id_type": receive_id_type,
            "msg_type": "post",
            "content": json.dumps(post_content),
        }
        data = await self._post("/im/v1/messages", payload)
        return data.get("data", {}).get("message_id", "")

    async def send_interactive(
        self,
        receive_id: str,
        receive_id_type: str,
        card_content: Dict,
    ) -> str:
        """
        Send an interactive card message.

        Args:
            receive_id: ID of the recipient
            receive_id_type: "chat_id", "open_id", or "user_id"
            card_content: Feishu card JSON content

        Returns:
            message_id of the sent message
        """
        payload = {
            "receive_id": receive_id,
            "receive_id_type": receive_id_type,
            "msg_type": "interactive",
            "content": json.dumps(card_content),
        }
        data = await self._post("/im/v1/messages", payload)
        return data.get("data", {}).get("message_id", "")

    # ─── Chat / Group Management ────────────────────────────────

    async def get_chat_members(
        self,
        chat_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Get members of a chat (group).

        Returns:
            List of member dicts with open_id, name, etc.
        """
        members = []
        page_token = None

        while True:
            params = {"member_id_type": "open_id"}
            if page_token:
                params["page_token"] = page_token

            data = await self._get(
                f"/im/v1/chats/{chat_id}/members",
                params=params,
            )
            items = data.get("data", {}).get("items", [])
            members.extend(items)

            page_token = data.get("data", {}).get("page_token")
            if not page_token:
                break

        return members

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get chat (group) info."""
        return await self._get(f"/im/v1/chats/{chat_id}")

    # ─── User Info ──────────────────────────────────────────────

    async def get_user_info(self, open_id: str) -> Dict[str, Any]:
        """
        Get user info by open_id.

        Returns:
            User data including name, avatar, etc.
        """
        return await self._get(
            f"/contact/v3/users/{open_id}",
            params={"user_id_type": "open_id"},
        )

    async def get_bot_info(self) -> Dict[str, Any]:
        """
        Get the bot (application) info.

        Returns the bot's own profile including open_id.
        """
        return await self._get("/bot/v3/info")

    # ─── Message Management ─────────────────────────────────────

    async def reply_message(
        self,
        message_id: str,
        content: str,
        msg_type: str = "text",
    ) -> str:
        """
        Reply to an existing message by message_id.

        Args:
            message_id: ID of the message to reply to
            content: Message content
            msg_type: "text", "post", etc.

        Returns:
            message_id of the reply
        """
        if msg_type == "text":
            payload = {
                "msg_type": "text",
                "content": json.dumps({"text": content}),
            }
        else:
            payload = {
                "msg_type": msg_type,
                "content": json.dumps(content),
            }

        data = await self._post(f"/im/v1/messages/{message_id}/reply", payload)
        return data.get("data", {}).get("message_id", "")
