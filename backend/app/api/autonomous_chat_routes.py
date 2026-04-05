"""
Autonomous Chat API - 自主聊天控制接口

提供 REST API 来管理和控制自主聊天会话。
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.autonomous_chat_orchestrator import (
    get_orchestrator,
    ChatMode,
    AutonomousChatOrchestrator,
    ChainExecutionLog,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["autonomous-chat"])


class ParticipantConfig(BaseModel):
    """参与者配置"""
    player_id: str = Field(..., description="玩家 ID")
    player_name: str = Field(..., description="玩家名称")
    model_type: str = Field(..., description="模型类型")
    mcp_invocation_id: str = Field(default="", description="MCP 调用 ID（可选）")
    mcp_callback_token: str = Field(default="", description="MCP 回调 Token（可选）")
    api_url: str = Field(default="", description="API 端点 URL")
    api_key: str = Field(default="", description="API 密钥")
    model: str = Field(default="", description="模型名称")
    system_prompt: str = Field(default="", description="系统提示词")


class CreateSessionRequest(BaseModel):
    """创建聊天会话请求"""
    room_id: str = Field(..., description="房间 ID")
    mode: str = Field(default="free_discussion", description="交流模式")
    topic: str = Field(default="", description="讨论话题")
    max_turns: int = Field(default=20, ge=1, le=100, description="最大回合数")
    lead_cat_id: Optional[str] = Field(default=None, description="主导猫 ID（可选）")
    participants: List[ParticipantConfig] = Field(..., description="参与者列表")


class CreateSessionResponse(BaseModel):
    """创建会话响应"""
    status: str
    room_id: str
    session_id: str
    participant_count: int


class StartChatRequest(BaseModel):
    """启动聊天请求"""
    room_id: str = Field(..., description="房间 ID")


class SendMessageRequest(BaseModel):
    """发送用户消息请求（handoff_chain 模式）"""
    content: str = Field(..., min_length=1, max_length=2000, description="用户消息内容")


class SessionStatusResponse(BaseModel):
    """会话状态响应"""
    room_id: str
    mode: str
    topic: str
    is_active: bool
    current_turn: int
    max_turns: int
    chain_max_turns: int
    lead_cat_id: Optional[str]
    lead_cat_name: Optional[str]
    lead_cat_verified: bool
    lead_cat_vote_open: bool
    vote_count: int
    participants: List[dict]
    created_at: str
    ended_at: Optional[str]


class SetLeadCatRequest(BaseModel):
    """指定主导猫"""
    lead_cat_id: str = Field(..., description="主导猫 ID")


class VoteLeadCatRequest(BaseModel):
    """主导猫投票"""
    voter_cat_id: str = Field(..., description="投票者 ID")
    voted_cat_id: str = Field(..., description="被投票的猫 ID")


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
        
        # 转换参与者配置为字典列表
        participants_data = [
            {
                "player_id": p.player_id,
                "player_name": p.player_name,
                "model_type": p.model_type,
                "mcp_invocation_id": p.mcp_invocation_id,
                "mcp_callback_token": p.mcp_callback_token,
                "api_url": p.api_url,
                "api_key": p.api_key,
                "model": p.model,
                "system_prompt": p.system_prompt
            }
            for p in request.participants
        ]

        # 创建会话
        session = orchestrator.create_session(
            room_id=request.room_id,
            mode=mode,
            participants=participants_data,
            topic=request.topic,
            max_turns=request.max_turns,
            lead_cat_id=request.lead_cat_id,
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


@router.post("/sessions/{room_id}/messages", status_code=202)
async def send_message(room_id: str, request: SendMessageRequest):
    """
    发送用户消息并触发 handoff chain

    仅用于 HANDOFF_CHAIN 模式。消息发送后，猫猫们会通过 @mention 链式接力对话。
    链条在后台异步执行，立即返回 202 Accepted。
    链条结果通过 WebSocket 实时推送。
    """
    orchestrator = get_orchestrator()

    if room_id not in orchestrator.sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = orchestrator.sessions[room_id]
    if session.mode != ChatMode.HANDOFF_CHAIN:
        raise HTTPException(
            status_code=400,
            detail=f"This endpoint requires handoff_chain mode, current mode is {session.mode.value}"
        )

    if not session.is_active:
        raise HTTPException(status_code=400, detail="Session is not active")

    # 检查锁是否已被持有（另一条链正在执行）
    if session._lock.locked():
        raise HTTPException(
            status_code=429,
            detail="A handoff chain is already running in this session. Please wait."
        )

    async def _run_chain():
        try:
            chain_log = await orchestrator.handle_user_message(
                room_id=room_id,
                user_text=request.content,
            )
            logger.info(
                f"[Chain {chain_log.chain_id}] completed: "
                f"{len(chain_log.steps)} steps, end_reason={chain_log.end_reason}"
            )
        except Exception as e:
            logger.exception(f"Handoff chain failed in room {room_id}: {e}")

    asyncio.create_task(_run_chain())

    return {
        "status": "accepted",
        "room_id": room_id,
        "message": "Message received, handoff chain started in background"
    }


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


# ──────────────────────────────────────────────────────────────
# F200-LeadCat: 主导猫管理路由
# ──────────────────────────────────────────────────────────────

@router.post("/sessions/{room_id}/lead-cat")
async def set_lead_cat(room_id: str, request: SetLeadCatRequest):
    """
    指定主导猫（铲屎官手动指定）。
    """
    orchestrator = get_orchestrator()
    result = orchestrator.set_lead_cat(room_id, request.lead_cat_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/sessions/{room_id}/lead-cat/vote-open")
async def open_lead_cat_vote(room_id: str):
    """
    开启主导猫投票（铲屎官不指定时的 fallback）。
    """
    orchestrator = get_orchestrator()
    result = orchestrator.open_lead_cat_vote(room_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/sessions/{room_id}/lead-cat/vote")
async def cast_lead_cat_vote(room_id: str, request: VoteLeadCatRequest):
    """
    猫猫投票给主导猫候选人。
    """
    orchestrator = get_orchestrator()
    result = orchestrator.cast_lead_cat_vote(room_id, request.voter_cat_id, request.voted_cat_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/sessions/{room_id}/lead-cat/tally")
async def tally_lead_cat_vote(room_id: str):
    """
    统计主导猫投票并选出获胜者。
    """
    orchestrator = get_orchestrator()
    result = orchestrator.tally_lead_cat_vote(room_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result
