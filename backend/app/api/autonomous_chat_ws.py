"""
Autonomous Chat WebSocket - 自主聊天 WebSocket 接口

提供 WebSocket 连接供前端实时接收 AI 猫对话消息。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class AutonomousChatWSManager:
    """自主聊天 WebSocket 连接管理器"""

    def __init__(self) -> None:
        # room_id -> set of WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # Track which rooms have been subscribed to avoid duplicate subscriptions
        self._subscribed_rooms: Set[str] = set()
        self._lock = asyncio.Lock()

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        """接受 WebSocket 连接"""
        await websocket.accept()
        async with self._lock:
            self._connections[room_id].add(websocket)
        logger.info(f"WebSocket connected to room {room_id}, total connections: {len(self._connections[room_id])}")

    async def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        """断开 WebSocket 连接"""
        async with self._lock:
            if room_id in self._connections:
                self._connections[room_id].discard(websocket)
                if not self._connections[room_id]:
                    del self._connections[room_id]
                    self._subscribed_rooms.discard(room_id)
        logger.info(f"WebSocket disconnected from room {room_id}")

    async def broadcast_to_room(self, room_id: str, message: Dict[str, Any]) -> None:
        """向房间的所有连接广播消息"""
        stale_connections = []

        async with self._lock:
            connections = list(self._connections.get(room_id, set()))

        if not connections:
            return

        # 构建消息
        event_data = {
            "event": "chat_message",
            "payload": message,
            "ts": datetime.utcnow().isoformat(),
        }
        message_text = json.dumps(event_data, ensure_ascii=False)

        for ws in connections:
            try:
                await ws.send_text(message_text)
            except Exception as e:
                logger.warning(f"Failed to send message to WebSocket: {e}")
                stale_connections.append(ws)

        # 清理失效连接
        if stale_connections:
            async with self._lock:
                for ws in stale_connections:
                    self._connections[room_id].discard(ws)

    async def send_history(self, room_id: str, websocket: WebSocket, messages: List[Dict[str, Any]]) -> None:
        """发送历史消息给新连接的客户端"""
        try:
            event_data = {
                "event": "chat_history",
                "payload": {"messages": messages},
                "ts": datetime.utcnow().isoformat(),
            }
            await websocket.send_text(json.dumps(event_data, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Failed to send history to WebSocket: {e}")

    def create_broadcast_callback(self, room_id: str) -> Callable[[Any], None]:
        """创建用于特定房间的广播回调函数"""
        def broadcast_callback(message: Any) -> None:
            # message is ChatMessage object
            msg_dict = message.to_dict() if hasattr(message, 'to_dict') else message
            # Schedule broadcast in event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.broadcast_to_room(room_id, msg_dict))
                else:
                    # Fallback for non-running loop
                    loop.run_until_complete(self.broadcast_to_room(room_id, msg_dict))
            except Exception as e:
                logger.error(f"Broadcast callback error: {e}")

        return broadcast_callback

    def ensure_subscribed(self, room_id: str, room: Any) -> None:
        """确保房间已订阅广播（每个房间只订阅一次）"""
        if room_id not in self._subscribed_rooms:
            broadcast_callback = self.create_broadcast_callback(room_id)
            room.subscribe(broadcast_callback)
            self._subscribed_rooms.add(room_id)
            logger.info(f"Subscribed WebSocket broadcast to room {room_id}")


# 全局 WebSocket 管理器实例
autonomous_chat_ws_manager = AutonomousChatWSManager()


async def autonomous_chat_websocket_endpoint(websocket: WebSocket, room_id: str) -> None:
    """
    自主聊天 WebSocket 端点处理函数

    路径: /ws/chat/{room_id}

    消息格式:
    - 客户端 -> 服务器: 无需发送消息，仅接收广播
    - 服务器 -> 客户端:
        {
            "event": "chat_message",
            "payload": {
                "sender_id": "cat_1",
                "sender_name": "布偶",
                "content": "消息内容",
                "timestamp": "2024-01-01T00:00:00",
                "message_type": "normal"
            },
            "ts": "2024-01-01T00:00:00"
        }
    """
    from app.agent.mcp_callback_bridge import mcp_callback_bridge

    await autonomous_chat_ws_manager.connect(room_id, websocket)

    try:
        # 获取房间
        room = mcp_callback_bridge.get_or_create_room(room_id)

        # 发送历史消息
        if room.message_history:
            history = [msg.to_dict() for msg in room.message_history]
            await autonomous_chat_ws_manager.send_history(room_id, websocket, history)

        # 确保房间已订阅广播（每个房间只订阅一次）
        autonomous_chat_ws_manager.ensure_subscribed(room_id, room)

        # 保持连接，处理客户端消息
        while True:
            try:
                # 接收客户端消息（用于保持连接或处理特殊指令）
                data = await websocket.receive_text()
                msg = json.loads(data)
                event = msg.get("event")

                if event == "ping":
                    await websocket.send_text(json.dumps({"event": "pong", "ts": datetime.utcnow().isoformat()}))
                elif event == "get_history":
                    # 重新发送历史消息
                    history = [m.to_dict() for m in room.message_history]
                    await autonomous_chat_ws_manager.send_history(room_id, websocket, history)

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error in room {room_id}: {e}")
    finally:
        await autonomous_chat_ws_manager.disconnect(room_id, websocket)
