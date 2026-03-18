"""
Autonomous Chat API - 自主聊天控制接口

提供 REST API 来管理和控制自主聊天会话。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.autonomous_chat_orchestrator import (
    get_orchestrator,
    ChatMode,
    AutonomousChatOrchestrator
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["autonomous-chat"])


class CreateSessionRequest(BaseModel):
    """创建聊天会话请求"""
    room_id: str = Field(..., description="房间 ID")
    mode: str = Field(default="free_discussion", description="交流模式")
    topic: str = Field(default="", description="讨论话题")
    max_turns: int = Field(default=20, ge=1, le=100, description="最大回合数")
    participants: List[dict] = Field(..., description="参与者列表")


class CreateSessionResponse(BaseModel):
    """创建会话响应"""
    status: str
    room_id: str
    session_id: str
    participant_count: int


class StartChatRequest(BaseModel):
    """启动聊天请求"""
    room_id: str = Field(..., description="房间 ID")


class SessionStatusResponse(BaseModel):
    """会话状态响应"""
    room_id: str
    mode: str
    topic: str
    is_active: bool
    current_turn: int
    max_turns: int
    participants: List[dict]
    created_at: str
    ended_at: Optional[str]


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest):
    """
    创建自主聊天会话
    
    配置参与者和交流模式，准备开始自主对话。
    """
    orchestrator = get_orchestrator()
    
    try:
        # 转换 mode 字符串到枚举
        try:
            mode = ChatMode(request.mode)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {request.mode}. Valid values: {[m.value for m in ChatMode]}"
            )
        
        # 创建会话
        session = orchestrator.create_session(
            room_id=request.room_id,
            mode=mode,
            participants=request.participants,
            topic=request.topic,
            max_turns=request.max_turns
        )
        
        logger.info(f"Created session in {request.room_id} with mode {mode.value}")
        
        return CreateSessionResponse(
            status="created",
            room_id=request.room_id,
            session_id=request.room_id,
            participant_count=len(request.participants)
        )
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("Failed to create session")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/start")
async def start_chat(request: StartChatRequest):
    """
    启动自主聊天
    
    开始后台聊天循环，AI 们会自动开始交流。
    """
    orchestrator = get_orchestrator()
    
    if request.room_id not in orchestrator.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        await orchestrator.start_autonomous_chat(request.room_id)
        return {
            "status": "started",
            "room_id": request.room_id,
            "message": "Autonomous chat started"
        }
    except Exception as e:
        logger.exception("Failed to start chat")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{room_id}/end")
async def end_session(room_id: str):
    """结束聊天会话"""
    orchestrator = get_orchestrator()
    
    if room_id not in orchestrator.sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    orchestrator.end_session(room_id)
    return {
        "status": "ended",
        "room_id": room_id
    }


@router.get("/sessions/{room_id}/status", response_model=SessionStatusResponse)
async def get_session_status(room_id: str):
    """获取会话状态"""
    orchestrator = get_orchestrator()
    
    status = orchestrator.get_session_status(room_id)
    if not status:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return SessionStatusResponse(**status)


@router.get("/sessions")
async def list_sessions():
    """列出所有活跃会话"""
    orchestrator = get_orchestrator()
    
    sessions = []
    for room_id, session in orchestrator.sessions.items():
        if session.is_active:
            sessions.append({
                "room_id": room_id,
                "mode": session.mode.value,
                "topic": session.topic,
                "current_turn": session.current_turn,
                "max_turns": session.max_turns,
                "participant_count": len(session.participants)
            })
    
    return {
        "active_sessions": len(sessions),
        "sessions": sessions
    }
