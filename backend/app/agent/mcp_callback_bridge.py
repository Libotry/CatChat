"""
MCP Callback Bridge - 将 MCP 回传系统集成到 AI 流水线

这个模块让猫猫们可以通过 MCP 工具主动发言，实现自主交流。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CallbackCredentials:
    """MCP 回调凭证"""
    invocation_id: str
    callback_token: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    @classmethod
    def generate(cls) -> "CallbackCredentials":
        """生成新的凭证对"""
        return cls(
            invocation_id=str(uuid.uuid4()),
            callback_token=str(uuid.uuid4())
        )
    
    def verify(self, req_invocation_id: str, req_callback_token: str) -> bool:
        """验证请求凭证"""
        return hmac.compare_digest(
            self.invocation_id.encode(),
            req_invocation_id.encode()
        ) and hmac.compare_digest(
            self.callback_token.encode(),
            req_callback_token.encode()
        )


@dataclass
class ChatMessage:
    """聊天室消息"""
    sender_id: str
    sender_name: str
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    message_type: str = "normal"  # normal, thinking, action
    
    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "message_type": self.message_type
        }


class MCPBridgeRoom:
    """单个聊天房间的 MCP 桥接管理"""
    
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.credentials = CallbackCredentials.generate()
        self.message_history: List[ChatMessage] = []
        self.subscribers: List[Callable[[ChatMessage], None]] = []
        
        logger.info(f"[Room {room_id}] MCP Bridge initialized")
        logger.info(f"  Invocation ID: {self.credentials.invocation_id}")
        logger.info(f"  Callback Token: {self.credentials.callback_token}")
    
    def get_context(self, limit: int = 10) -> dict:
        """获取对话上下文（供 MCP 工具调用）"""
        recent_messages = self.message_history[-limit:]
        return {
            "room_id": self.room_id,
            "messages": [msg.to_dict() for msg in recent_messages]
        }
    
    def post_message(
        self,
        sender_id: str,
        sender_name: str,
        content: str,
        message_type: str = "normal"
    ) -> bool:
        """发布消息到聊天室"""
        message = ChatMessage(
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            message_type=message_type
        )

        self.message_history.append(message)
        logger.info(f"[Room {self.room_id}] {sender_name}: {content[:50]}...")

        # 通知订阅者
        for callback in self.subscribers:
            try:
                callback(message)
            except Exception as e:
                logger.error(f"Subscriber callback error: {e}")

        return True

    def post_message_without_auth(
        self,
        sender_id: str,
        sender_name: str,
        content: str,
        message_type: str = "normal"
    ) -> bool:
        """
        发布消息（无需认证，供后端编排器直接调用）

        这是 post_message 的别名，用于明确表示无需 MCP 回调验证
        """
        return self.post_message(sender_id, sender_name, content, message_type)
    
    def subscribe(self, callback: Callable[[ChatMessage], None]) -> None:
        """订阅新消息事件"""
        self.subscribers.append(callback)
    
    def unsubscribe(self, callback: Callable[[ChatMessage], None]) -> None:
        """取消订阅"""
        if callback in self.subscribers:
            self.subscribers.remove(callback)


class MCPCallbackBridge:
    """
    MCP 回调桥接管理器
    
    功能：
    1. 为每个房间生成唯一的 MCP 凭证
    2. 提供 HTTP 接口供 MCP Server 调用
    3. 将 AI 的主动发言转发到游戏引擎
    4. 维护聊天历史记录
    """
    
    def __init__(self):
        self.rooms: Dict[str, MCPBridgeRoom] = {}
        self._lock = asyncio.Lock()
        
        # 从环境变量加载配置
        self.enabled = os.getenv("CAT_MCP_BRIDGE_ENABLED", "true").lower() in ("true", "1", "yes")
        self.max_history_length = int(os.getenv("CAT_MCP_MAX_HISTORY", "100"))
        
        logger.info(f"MCP Callback Bridge initialized (enabled={self.enabled})")
    
    def get_or_create_room(self, room_id: str) -> MCPBridgeRoom:
        """获取或创建房间的 MCP 桥接"""
        if room_id not in self.rooms:
            self.rooms[room_id] = MCPBridgeRoom(room_id)
        return self.rooms[room_id]
    
    def get_room_credentials(self, room_id: str) -> Optional[CallbackCredentials]:
        """获取房间的凭证（用于配置 MCP Server）"""
        if room_id not in self.rooms:
            return None
        return self.rooms[room_id].credentials
    
    def verify_callback(
        self,
        room_id: str,
        invocation_id: str,
        callback_token: str
    ) -> bool:
        """验证回调请求"""
        if room_id not in self.rooms:
            return False
        
        room = self.rooms[room_id]
        return room.credentials.verify(invocation_id, callback_token)
    
    async def handle_get_context(
        self,
        room_id: str,
        invocation_id: str,
        callback_token: str,
        limit: int = 10
    ) -> tuple[bool, dict]:
        """
        处理获取上下文请求
        
        Returns:
            (success, context_data or error)
        """
        if not self.verify_callback(room_id, invocation_id, callback_token):
            return False, {"error": "Unauthorized"}
        
        room = self.rooms[room_id]
        context = room.get_context(limit)
        return True, context
    
    async def handle_post_message(
        self,
        room_id: str,
        invocation_id: str,
        callback_token: str,
        sender_id: str,
        sender_name: str,
        content: str,
        message_type: str = "normal"
    ) -> tuple[bool, dict]:
        """
        处理发布消息请求
        
        Returns:
            (success, result_data or error)
        """
        if not self.verify_callback(room_id, invocation_id, callback_token):
            return False, {"error": "Unauthorized"}
        
        room = self.rooms[room_id]
        success = room.post_message(sender_id, sender_name, content, message_type)
        
        if success:
            return True, {"status": "ok", "message_id": len(room.message_history)}
        else:
            return False, {"error": "Failed to post message"}
    
    def trim_history(self, room_id: str) -> None:
        """裁剪过长的历史记录"""
        if room_id not in self.rooms:
            return
        
        room = self.rooms[room_id]
        if len(room.message_history) > self.max_history_length:
            excess = len(room.message_history) - self.max_history_length
            room.message_history = room.message_history[excess:]
            logger.debug(f"[Room {room_id}] Trimmed {excess} old messages")


# 全局单例
bridge_instance: Optional[MCPCallbackBridge] = None


def get_bridge() -> MCPCallbackBridge:
    """获取全局 MCP 桥接实例"""
    global bridge_instance
    if bridge_instance is None:
        bridge_instance = MCPCallbackBridge()
    return bridge_instance


def reset_bridge() -> None:
    """重置桥接（用于测试）"""
    global bridge_instance
    bridge_instance = None
