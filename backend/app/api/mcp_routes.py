"""
MCP Callback API Routes

这些路由提供给 MCP Server 调用，实现 AI 与聊天室的通信。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.agent.mcp_callback_bridge import get_bridge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class PostMessageRequest(BaseModel):
    """发布消息请求体"""
    roomId: str = Field(..., description="房间 ID")
    invocationId: str = Field(..., description="MCP 调用 ID")
    callbackToken: str = Field(..., description="回调令牌")
    senderId: str = Field(..., description="发送者玩家 ID")
    senderName: str = Field(..., description="发送者名称")
    content: str = Field(..., description="消息内容")
    messageType: str = Field(default="normal", description="消息类型")
    metadata: Optional[dict] = Field(default=None, description="额外元数据")


class PostMessageResponse(BaseModel):
    """发布消息响应"""
    status: str
    message_id: int
    room_id: str


class GetContextResponse(BaseModel):
    """获取上下文响应"""
    room_id: str
    messages: list[dict]
    message_count: int


@router.post("/post-message", response_model=PostMessageResponse)
async def post_message(request: PostMessageRequest):
    """
    MCP Server 调用此接口发布消息到聊天室
    
    AI 通过这个接口主动发言，实现与其他 AI 的交流。
    """
    bridge = get_bridge()
    
    success, result = await bridge.handle_post_message(
        room_id=request.roomId,
        invocation_id=request.invocationId,
        callback_token=request.callbackToken,
        sender_id=request.senderId,
        sender_name=request.senderName,
        content=request.content,
        message_type=request.messageType
    )
    
    if not success:
        logger.warning(f"Failed to post message: {result}")
        raise HTTPException(status_code=401, detail=result.get("error", "Authentication failed"))
    
    logger.info(
        f"[MCP] {request.senderName} posted to {request.roomId}: "
        f"{request.content[:50]}{'...' if len(request.content) > 50 else ''}"
    )
    
    return PostMessageResponse(
        status="ok",
        message_id=result["message_id"],
        room_id=request.roomId
    )


@router.get("/get-context", response_model=GetContextResponse)
async def get_context(
    roomId: str = Query(..., description="房间 ID"),
    invocationId: str = Query(..., description="MCP 调用 ID"),
    callbackToken: str = Query(..., description="回调令牌"),
    limit: int = Query(default=10, ge=1, le=50, description="获取消息数量限制")
):
    """
    MCP Server 调用此接口获取聊天室上下文
    
    AI 在发言前可以使用此接口了解当前的讨论进展。
    """
    bridge = get_bridge()
    
    success, result = await bridge.handle_get_context(
        room_id=roomId,
        invocation_id=invocationId,
        callback_token=callbackToken,
        limit=limit
    )
    
    if not success:
        logger.warning(f"Failed to get context: {result}")
        raise HTTPException(status_code=401, detail=result.get("error", "Authentication failed"))
    
    return GetContextResponse(
        room_id=roomId,
        messages=result["messages"],
        message_count=len(result["messages"])
    )


@router.get("/health")
async def health_check():
    """MCP Bridge 健康检查"""
    bridge = get_bridge()
    return {
        "status": "ok",
        "rooms_active": len(bridge.rooms),
        "enabled": bridge.enabled
    }
