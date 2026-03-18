"""
Autonomous Chat Orchestrator - 自主交流编排器

这个模块让猫猫们在流水线中实现自主深度交流，无需铲屎官驱动。

核心机制：
1. 每只猫都有自己的 MCP Server 进程
2. 通过 MCP 工具主动发言和交流
3. 基于事件触发自主对话流程
4. 支持多种交流模式：自由讨论、辩论、协作
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

from app.agent.mcp_callback_bridge import get_bridge, ChatMessage

logger = logging.getLogger(__name__)


class ChatMode(Enum):
    """交流模式"""
    FREE_DISCUSSION = "free_discussion"  # 自由讨论
    DEBATE = "debate"  # 辩论
    COLLABORATION = "collaboration"  # 协作
    ROLEPLAY = "roleplay"  # 角色扮演
    WEREWOLF_GAME = "werewolf_game"  # 狼人杀游戏


@dataclass
class ChatParticipant:
    """聊天参与者"""
    player_id: str
    player_name: str
    model_type: str
    mcp_invocation_id: str
    mcp_callback_token: str
    is_active: bool = True
    message_count: int = 0
    last_message_time: Optional[datetime] = None


@dataclass
class ChatSession:
    """聊天会话"""
    room_id: str
    mode: ChatMode
    participants: Dict[str, ChatParticipant] = field(default_factory=dict)
    topic: str = ""
    max_turns: int = 20
    current_turn: int = 0
    is_active: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None


@dataclass
class TurnResult:
    """回合结果"""
    speaker_id: str
    message: str
    timestamp: datetime
    triggered_responses: List[str] = field(default_factory=list)


class AutonomousChatOrchestrator:
    """
    自主交流编排器
    
    职责：
    1. 管理聊天会话的生命周期
    2. 协调参与者的发言顺序
    3. 监听新消息并触发响应
    4. 实现不同的交流策略
    """
    
    def __init__(self):
        self.sessions: Dict[str, ChatSession] = {}
        self.bridge = get_bridge()
        self._running = False
        self._background_tasks: Dict[str, asyncio.Task] = {}
        
        # 配置
        self.response_timeout = int(os.getenv("CAT_CHAT_RESPONSE_TIMEOUT", "30"))
        self.idle_timeout = int(os.getenv("CAT_CHAT_IDLE_TIMEOUT", "120"))
        self.max_concurrent_sessions = int(os.getenv("CAT_CHAT_MAX_SESSIONS", "5"))
        
        logger.info("Autonomous Chat Orchestrator initialized")
    
    def create_session(
        self,
        room_id: str,
        mode: ChatMode,
        participants: List[Dict],
        topic: str = "",
        max_turns: int = 20
    ) -> ChatSession:
        """创建新的聊天会话"""
        if room_id in self.sessions:
            raise ValueError(f"Session already exists for room {room_id}")
        
        if len(self.sessions) >= self.max_concurrent_sessions:
            raise RuntimeError(f"Maximum concurrent sessions ({self.max_concurrent_sessions}) reached")
        
        session = ChatSession(
            room_id=room_id,
            mode=mode,
            topic=topic,
            max_turns=max_turns
        )
        
        # 添加参与者
        for p in participants:
            participant = ChatParticipant(
                player_id=p["player_id"],
                player_name=p["player_name"],
                model_type=p.get("model_type", "unknown"),
                mcp_invocation_id=p["mcp_invocation_id"],
                mcp_callback_token=p["mcp_callback_token"]
            )
            session.participants[participant.player_id] = participant
        
        self.sessions[room_id] = session
        logger.info(f"Created {mode.value} session in {room_id} with {len(participants)} participants")
        
        return session
    
    def end_session(self, room_id: str) -> None:
        """结束聊天会话"""
        if room_id not in self.sessions:
            return
        
        session = self.sessions[room_id]
        session.is_active = False
        session.ended_at = datetime.utcnow()
        
        # 停止后台任务
        if room_id in self._background_tasks:
            self._background_tasks[room_id].cancel()
            del self._background_tasks[room_id]
        
        logger.info(f"Ended session in {room_id} after {session.current_turn} turns")
    
    async def start_autonomous_chat(self, room_id: str) -> None:
        """启动自主聊天循环"""
        if room_id not in self.sessions:
            raise ValueError(f"No session found for room {room_id}")
        
        session = self.sessions[room_id]
        session.is_active = True
        
        # 启动后台监控任务
        task = asyncio.create_task(self._chat_loop(room_id))
        self._background_tasks[room_id] = task
        
        logger.info(f"Started autonomous chat in {room_id}")
    
    async def _chat_loop(self, room_id: str) -> None:
        """聊天主循环"""
        session = self.sessions[room_id]
        idle_start = datetime.utcnow()
        
        try:
            while session.is_active and session.current_turn < session.max_turns:
                # 检查空闲超时
                if (datetime.utcnow() - idle_start).total_seconds() > self.idle_timeout:
                    logger.info(f"Session {room_id} idle timeout, ending...")
                    break
                
                # 根据模式选择下一个发言人
                next_speaker = self._select_next_speaker(session)
                if not next_speaker:
                    await asyncio.sleep(2)
                    continue
                
                # 触发发言
                try:
                    await self._trigger_turn(session, next_speaker)
                    idle_start = datetime.utcnow()  # 重置空闲计时
                except Exception as e:
                    logger.error(f"Error triggering turn: {e}")
                    await asyncio.sleep(5)
                
                await asyncio.sleep(1)  # 避免过快
            
            # 正常结束
            if session.current_turn >= session.max_turns:
                logger.info(f"Session {room_id} reached max turns ({session.max_turns})")
            
            self.end_session(room_id)
            
        except asyncio.CancelledError:
            logger.info(f"Session {room_id} cancelled")
            self.end_session(room_id)
        except Exception as e:
            logger.exception(f"Chat loop error: {e}")
            self.end_session(room_id)
    
    def _select_next_speaker(self, session: ChatSession) -> Optional[ChatParticipant]:
        """选择下一个发言人（基于模式的策略）"""
        active_participants = [
            p for p in session.participants.values()
            if p.is_active
        ]
        
        if not active_participants:
            return None
        
        if session.mode == ChatMode.FREE_DISCUSSION:
            # 自由讨论：最少发言的人优先
            return min(active_participants, key=lambda p: p.message_count)
        
        elif session.mode == ChatMode.DEBATE:
            # 辩论：轮流发言
            sorted_by_time = sorted(
                active_participants,
                key=lambda p: p.last_message_time or datetime.min
            )
            return sorted_by_time[0]
        
        elif session.mode == ChatMode.COLLABORATION:
            # 协作：根据话题相关性选择（简化版：随机）
            import random
            return random.choice(active_participants)
        
        else:
            # 默认：轮询
            sorted_by_time = sorted(
                active_participants,
                key=lambda p: p.last_message_time or datetime.min
            )
            return sorted_by_time[0]
    
    async def _trigger_turn(self, session: ChatSession, speaker: ChatParticipant) -> None:
        """触发一轮发言"""
        logger.info(f"Triggering turn for {speaker.player_name} in {session.room_id}")
        
        # 这里应该调用 AI 的 MCP Server
        # 由于实际实现依赖于具体的 AI 平台，这里提供一个框架
        
        # TODO: 实现实际的 AI 调用逻辑
        # 伪代码：
        # ai_response = await call_ai_via_mcp(
        #     player_id=speaker.player_id,
        #     context=session.get_recent_messages(),
        #     topic=session.topic
        # )
        # 
        # if ai_response.has_message:
        #     self.bridge.post_message(...)
        #     speaker.message_count += 1
        #     speaker.last_message_time = datetime.utcnow()
        #     session.current_turn += 1
        
        # 临时实现：模拟发言
        await self._simulate_turn(session, speaker)
    
    async def _simulate_turn(self, session: ChatSession, speaker: ChatParticipant) -> None:
        """模拟一轮发言（用于测试）"""
        # 实际使用时会被真实的 AI 调用替代
        simulated_messages = [
            "我觉得这个话题很有意思...",
            "我同意前面的观点，还想补充一点...",
            "从另一个角度来看...",
            "大家说得都很好，我认为关键是...",
            "我有些不同的想法..."
        ]
        
        import random
        message_content = random.choice(simulated_messages)
        
        # 发布消息
        success = self.bridge.handle_post_message(
            room_id=session.room_id,
            invocation_id=speaker.mcp_invocation_id,
            callback_token=speaker.mcp_callback_token,
            sender_id=speaker.player_id,
            sender_name=speaker.player_name,
            content=f"[{speaker.player_name}] {message_content}",
            message_type="normal"
        )[0]
        
        if success:
            speaker.message_count += 1
            speaker.last_message_time = datetime.utcnow()
            session.current_turn += 1
            
            logger.info(
                f"[Turn {session.current_turn}/{session.max_turns}] "
                f"{speaker.player_name}: {message_content}"
            )
    
    def get_session_status(self, room_id: str) -> Optional[dict]:
        """获取会话状态"""
        if room_id not in self.sessions:
            return None
        
        session = self.sessions[room_id]
        return {
            "room_id": session.room_id,
            "mode": session.mode.value,
            "topic": session.topic,
            "is_active": session.is_active,
            "current_turn": session.current_turn,
            "max_turns": session.max_turns,
            "participants": [
                {
                    "player_id": p.player_id,
                    "player_name": p.player_name,
                    "message_count": p.message_count,
                    "is_active": p.is_active
                }
                for p in session.participants.values()
            ],
            "created_at": session.created_at.isoformat(),
            "ended_at": session.ended_at.isoformat() if session.ended_at else None
        }


# 全局单例
orchestrator_instance: Optional[AutonomousChatOrchestrator] = None


def get_orchestrator() -> AutonomousChatOrchestrator:
    """获取全局编排器实例"""
    global orchestrator_instance
    if orchestrator_instance is None:
        orchestrator_instance = AutonomousChatOrchestrator()
    return orchestrator_instance


def reset_orchestrator() -> None:
    """重置编排器（用于测试）"""
    global orchestrator_instance
    orchestrator_instance = None
