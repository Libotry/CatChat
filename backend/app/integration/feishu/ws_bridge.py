"""
WebSocket bridge between CatChat rooms and Feishu groups.

Attaches to the existing WSConnectionManager as a subscriber,
so that every CatChat event is forwarded to the bound Feishu group.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import WebSocket

from .chatroom_bridge import chatroom_bridge, RoomBinding
from .feishu_client import FeishuClient
from .feishu_handler import FeishuHandler

logger = logging.getLogger(__name__)


class FeishuWSBridge:
    """
    Subscribes to CatChat WebSocket events and forwards them to Feishu.

    Usage:
        bridge = FeishuWSBridge(feishu_handler, room_manager)
        await bridge.start()

    The bridge subscribes to the WSConnectionManager as a pseudo-WebSocket
    subscriber, converting each broadcast event to Feishu messages.
    """

    def __init__(
        self,
        feishu_handler: FeishuHandler,
        room_manager: Any,
        ws_manager: Any,
    ):
        self.handler = feishu_handler
        self.room_manager = room_manager
        self.ws_manager = ws_manager
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the bridge — subscribes to WS events."""
        async with self._lock:
            if self._running:
                return
            self._running = True
            # Register push callback on the handler
            self.handler.register_push_callback(self._on_catchat_event)
            logger.info("FeishuWSBridge started")

    async def stop(self) -> None:
        """Stop the bridge."""
        async with self._lock:
            if not self._running:
                return
            self._running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
            logger.info("FeishuWSBridge stopped")

    async def _on_catchat_event(
        self,
        binding: RoomBinding,
        event: dict,
    ) -> None:
        """
        Called when the FeishuHandler receives a CatChat event.

        This routes the event to the correct Feishu group.
        (Actually the handler calls this directly via on_catchat_event,
        so this is a convenience hook.)
        """
        await self.handler.on_catchat_event(binding, event)

    def get_binding_for_room(self, room_id: str) -> Optional[RoomBinding]:
        """Find the Feishu binding for a CatChat room, if any."""
        return chatroom_bridge.get_by_catchat(room_id)


# ─── WS Event Forwarder ────────────────────────────────────────

async def forward_ws_events_to_feishu(
    feishu_handler: FeishuHandler,
    room_id: str,
    event: str,
    payload: Any,
) -> None:
    """
    Utility to forward a WS event to the bound Feishu group.

    Call this from wherever CatChat broadcasts an event, e.g.:
        forward_ws_events_to_feishu(handler, room_id, "agent_speech", payload)
    """
    binding = chatroom_bridge.get_by_catchat(room_id)
    if binding is None:
        return

    await feishu_handler.on_catchat_event(
        binding,
        {"event": event, "payload": payload},
    )


# ─── Module-level singleton ────────────────────────────────────

_feishu_ws_bridge: Optional[FeishuWSBridge] = None


def get_feishu_ws_bridge() -> Optional[FeishuWSBridge]:
    return _feishu_ws_bridge


async def setup_feishu_bridge(
    feishu_handler: FeishuHandler,
    room_manager: Any,
    ws_manager: Any,
) -> FeishuWSBridge:
    """Initialize and start the Feishu WebSocket bridge."""
    global _feishu_ws_bridge
    bridge = FeishuWSBridge(feishu_handler, room_manager, ws_manager)
    await bridge.start()
    _feishu_ws_bridge = bridge
    return bridge
