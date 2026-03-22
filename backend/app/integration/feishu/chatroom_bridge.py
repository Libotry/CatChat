"""Bridge between Feishu chats and CatChat rooms."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RoomBinding:
    """
    Represents a binding between a Feishu chat and a CatChat room.
    """
    catchat_room_id: str       # CatChat internal room ID
    feishu_chat_id: str        # Feishu group chat ID
    feishu_bot_open_id: str    # Bot's own open_id (to detect self-messages)
    mode: str = "werewolf"    # "werewolf" | "autonomous_chat"
    created_at: float = field(default_factory=time.time)
    last_message_at: float = field(default_factory=time.time)

    def touch(self):
        self.last_message_at = time.time()


class ChatroomBridge:
    """
    Manages one-to-one mappings between Feishu groups and CatChat rooms.

    Thread-safety is provided by asyncio.Lock on every write operation.
    Reads are lock-free for performance (dict reads are atomic in CPython).
    """

    def __init__(self):
        # feishu_chat_id → RoomBinding
        self._bindings: Dict[str, RoomBinding] = {}
        # catchat_room_id → feishu_chat_id (reverse index)
        self._reverse: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def bind(
        self,
        feishu_chat_id: str,
        catchat_room_id: str,
        feishu_bot_open_id: str,
        mode: str = "werewolf",
    ) -> RoomBinding:
        """Create a new binding."""
        async with self._lock:
            # Remove old binding if exists
            old = self._bindings.get(feishu_chat_id)
            if old:
                self._reverse.pop(old.catchat_room_id, None)

            binding = RoomBinding(
                catchat_room_id=catchat_room_id,
                feishu_chat_id=feishu_chat_id,
                feishu_bot_open_id=feishu_bot_open_id,
                mode=mode,
            )
            self._bindings[feishu_chat_id] = binding
            self._reverse[catchat_room_id] = feishu_chat_id
            logger.info(
                "Bridge: feishu_chat=%s → catchat_room=%s (mode=%s)",
                feishu_chat_id, catchat_room_id, mode,
            )
            return binding

    async def unbind(self, feishu_chat_id: str) -> bool:
        """Remove a binding by Feishu chat ID."""
        async with self._lock:
            binding = self._bindings.pop(feishu_chat_id, None)
            if binding:
                self._reverse.pop(binding.catchat_room_id, None)
                logger.info("Bridge: unbound feishu_chat=%s", feishu_chat_id)
                return True
            return False

    def get_by_feishu(self, feishu_chat_id: str) -> Optional[RoomBinding]:
        """Look up binding by Feishu chat ID (no lock needed)."""
        return self._bindings.get(feishu_chat_id)

    def get_by_catchat(self, catchat_room_id: str) -> Optional[RoomBinding]:
        """Look up binding by CatChat room ID (no lock needed)."""
        feishu_chat_id = self._reverse.get(catchat_room_id)
        if feishu_chat_id:
            return self._bindings.get(feishu_chat_id)
        return None

    def list_all(self) -> List[RoomBinding]:
        """List all active bindings."""
        return list(self._bindings.values())

    def count(self) -> int:
        return len(self._bindings)

    async def touch(self, feishu_chat_id: str) -> None:
        """Update last_message_at timestamp."""
        binding = self._bindings.get(feishu_chat_id)
        if binding:
            binding.touch()


# Global singleton
chatroom_bridge = ChatroomBridge()
