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

import httpx

from app.agent.mcp_callback_bridge import get_bridge, ChatMessage
from app.agent.a2a_parser import parse_a2a_mentions, A2AParseResult
from app.agent.target_resolver import resolve_targets, ResolveResult

logger = logging.getLogger(__name__)


class ChatMode(Enum):
    """交流模式"""
    FREE_DISCUSSION = "free_discussion"  # 自由讨论
    DEBATE = "debate"  # 辩论
    COLLABORATION = "collaboration"  # 协作
    ROLEPLAY = "roleplay"  # 角色扮演
    WEREWOLF_GAME = "werewolf_game"  # 狼人杀游戏
    HANDOFF_CHAIN = "handoff_chain"  # 链式接力协作（文档架构核心模式）


@dataclass
class ChatParticipant:
    """聊天参与者"""
    player_id: str
    player_name: str
    model_type: str
    mcp_invocation_id: str
    mcp_callback_token: str
    api_url: str = ""          # API endpoint URL
    api_key: str = ""          # API key
    model: str = ""            # Model name
    system_prompt: str = ""    # System prompt for the cat
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
    is_processing: bool = False  # 状态机处理状态
    last_processing_time: Optional[datetime] = None  # 上次处理时间
    last_active_cat: Optional[str] = None  # 上一个活跃猫 ID（用于 handoff 链）
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)  # 并发锁

    # F200-LeadCat: 主导猫
    lead_cat_id: Optional[str] = None          # 当前主导猫 ID
    lead_cat_verified: bool = False            # 是否经过系统认证
    lead_cat_votes: Dict[str, str] = field(default_factory=dict)  # voter_id -> voted_cat_id
    lead_cat_vote_open: bool = False          # 是否正在投票中
    chain_max_turns: int = 20                # 链式讨论轮数上限（random 5-15）


@dataclass
class ChainExecutionLog:
    """链式执行日志条目（用于调试和可观测性）"""
    chain_id: str
    trigger_text: str
    steps: List[dict] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    end_reason: str = ""  # "completed" | "max_depth" | "no_targets" | "error"


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
        self.reply_cooldown = int(os.getenv("ADD_REPLY_COOLDOWN_SECONDS", "3"))  # 发言冷却时间
        self.processing_timeout = int(os.getenv("PROCESSING_TIMEOUT_SECONDS", "60"))  # 状态机超时重置

        logger.info("Autonomous Chat Orchestrator initialized")

    def create_session(
        self,
        room_id: str,
        mode: ChatMode,
        participants: List[Dict],
        topic: str = "",
        max_turns: int = None,
        lead_cat_id: Optional[str] = None,
    ) -> ChatSession:
        """创建新的聊天会话"""
        if room_id in self.sessions:
            raise ValueError(f"Session already exists for room {room_id}")

        if len(self.sessions) >= self.max_concurrent_sessions:
            raise RuntimeError(f"Maximum concurrent sessions ({self.max_concurrent_sessions}) reached")

        # 使用环境变量作为默认最大轮数
        actual_max_turns = max_turns or int(os.getenv("MAX_AUTONOMOUS_ROUNDS", "20"))
        # F200-LeadCat: 链式讨论轮数随机 5-15
        import random
        chain_turns = random.randint(5, 15)
        session = ChatSession(
            room_id=room_id,
            mode=mode,
            topic=topic,
            max_turns=actual_max_turns,
            chain_max_turns=chain_turns,
        )

        # 添加参与者
        for p in participants:
            participant = ChatParticipant(
                player_id=p["player_id"],
                player_name=p["player_name"],
                model_type=p.get("model_type", "unknown"),
                mcp_invocation_id=p["mcp_invocation_id"],
                mcp_callback_token=p["mcp_callback_token"],
                api_url=p.get("api_url", ""),
                api_key=p.get("api_key", ""),
                model=p.get("model", ""),
                system_prompt=p.get("system_prompt", "")
            )
            session.participants[participant.player_id] = participant

        # F200-LeadCat: 主导猫
        if lead_cat_id and lead_cat_id in session.participants:
            session.lead_cat_id = lead_cat_id
            session.lead_cat_verified = True
            logger.info(f"Lead cat set by user: {lead_cat_id}")
        elif session.participants:
            # 自动选择：从未指定时，随机选一只猫作为主导猫
            import random
            chosen = random.choice(list(session.participants.keys()))
            session.lead_cat_id = chosen
            session.lead_cat_verified = True
            logger.info(f"[lead-cat] Auto-selected lead cat: {chosen}")

        self.sessions[room_id] = session
        logger.info(f"Created {mode.value} session in {room_id} with {len(participants)} participants, chain_max_turns={chain_turns}")

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

        if session.mode == ChatMode.HANDOFF_CHAIN:
            # HANDOFF_CHAIN 模式：
            # 1. 标记为 active，等待 handle_user_message() 触发链式执行
            # 2. 同时运行 _chat_loop 以处理 lead cat 空闲超时触发议题
            logger.info(
                f"Started HANDOFF_CHAIN session in {room_id}, "
                f"waiting for messages + lead-cat idle timeout"
            )
            # 继续往下走，启动 _chat_loop

        # 其他模式：启动后台轮询任务
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
                    # F200-LeadCat: 空闲超时 → 主导猫发起议题
                    if session.lead_cat_id:
                        logger.info(f"Session {room_id} idle timeout, lead cat initiating topic...")
                        await self._lead_cat_initiate_topic(session)
                        idle_start = datetime.utcnow()  # 重置空闲计时
                        continue
                    else:
                        logger.info(f"Session {room_id} idle timeout, ending...")
                        break

                # 检查并重置状态机死锁
                if session.is_processing and session.last_processing_time:
                    elapsed = (datetime.utcnow() - session.last_processing_time).total_seconds()
                    if elapsed > self.processing_timeout:
                        logger.warning(f"Session {room_id} processing timeout ({elapsed}s), resetting state...")
                        session.is_processing = False
                        session.last_processing_time = None

                # 如果正在处理，等待
                if session.is_processing:
                    await asyncio.sleep(1)
                    continue

                # 根据模式选择下一个发言人
                next_speaker = self._select_next_speaker(session)
                if not next_speaker:
                    # F200-LeadCat: 没有可发言的猫 → 主导猫发起议题
                    if session.lead_cat_id and session.mode == ChatMode.FREE_DISCUSSION:
                        topic_initiated = await self._lead_cat_initiate_topic(session)
                        if topic_initiated:
                            idle_start = datetime.utcnow()
                    await asyncio.sleep(2)
                    continue

                # 标记开始处理
                session.is_processing = True
                session.last_processing_time = datetime.utcnow()

                # 触发发言
                try:
                    await self._trigger_turn(session, next_speaker)
                    idle_start = datetime.utcnow()  # 重置空闲计时
                except Exception as e:
                    logger.error(f"Error triggering turn: {e}")
                    await asyncio.sleep(5)
                finally:
                    # 无论成功失败，都重置处理状态
                    session.is_processing = False
                    session.last_processing_time = None

                await asyncio.sleep(self.reply_cooldown)  # 使用冷却时间避免发言过快

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
        now = datetime.utcnow()
        active_participants = [
            p for p in session.participants.values()
            if p.is_active and (
                p.last_message_time is None or
                (now - p.last_message_time).total_seconds() >= self.reply_cooldown
            )
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
        """触发一轮发言 - 直接调用 LLM API"""
        logger.info(f"Triggering turn for {speaker.player_name} in {session.room_id}")

        try:
            # 获取对话上下文
            context = self._build_context(session, speaker)

            # 调用 LLM
            response = await self._call_llm(
                speaker=speaker,
                context=context
            )

            if response:
                # 直接发布消息（后端驱动，无需 MCP 回调验证）
                room = self.bridge.get_or_create_room(session.room_id)
                success = room.post_message(
                    sender_id=speaker.player_id,
                    sender_name=speaker.player_name,
                    content=response,
                    message_type="normal"
                )

                if success:
                    speaker.message_count += 1
                    speaker.last_message_time = datetime.utcnow()
                    session.current_turn += 1
                    logger.info(f"[Turn {session.current_turn}/{session.max_turns}] {speaker.player_name}: {response[:50]}...")

        except Exception as e:
            logger.error(f"Error in _trigger_turn: {e}")
            raise

    async def _simulate_turn(self, session: ChatSession, speaker: ChatParticipant) -> None:
        """模拟一轮发言（用于测试）"""
        """
        模拟发言 - 仅在无法调用真实 LLM 时使用
        保留此方法用于测试和降级场景
        """
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

    def _build_context(self, session: ChatSession, speaker: ChatParticipant) -> list:
        """构建对话上下文"""
        # 从 bridge 获取历史消息
        room = self.bridge.get_or_create_room(session.room_id)
        recent_messages = room.message_history[-10:]  # 最近10条

        # 构建消息列表
        messages = []

        # 添加系统提示
        system_content = speaker.system_prompt or f"你是{speaker.player_name}，正在参与一个群聊讨论。"
        if session.topic:
            system_content += f"\n当前讨论话题：{session.topic}"
        system_content += "\n请保持自然、有深度的对话风格，回应其他参与者的观点。发言简短有力，不超过100字。"
        messages.append({"role": "system", "content": system_content})

        # 添加历史消息
        for msg in recent_messages:
            role = "assistant" if msg.sender_id == speaker.player_id else "user"
            content = f"{msg.sender_name}: {msg.content}"
            messages.append({"role": role, "content": content})

        return messages

    async def _call_llm(self, speaker: ChatParticipant, context: list) -> Optional[str]:
        """调用 LLM API"""
        if not speaker.api_url or not speaker.api_key:
            logger.error(f"Missing API config for {speaker.player_name}")
            return None

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # 判断 API 类型
                if "anthropic" in speaker.api_url.lower() or "claude" in speaker.model.lower():
                    # Anthropic API
                    response = await client.post(
                        speaker.api_url,
                        headers={
                            "x-api-key": speaker.api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json"
                        },
                        json={
                            "model": speaker.model or "claude-3-sonnet-20240229",
                            "max_tokens": 500,
                            "messages": context
                        }
                    )

                    if response.status_code == 200:
                        data = response.json()
                        return data.get("content", [{}])[0].get("text", "").strip()
                    else:
                        logger.error(f"Anthropic API error: {response.status_code} - {response.text}")
                        return None
                else:
                    # OpenAI 兼容 API
                    response = await client.post(
                        speaker.api_url,
                        headers={
                            "Authorization": f"Bearer {speaker.api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": speaker.model or "gpt-3.5-turbo",
                            "messages": context,
                            "temperature": 0.8,
                            "max_tokens": 500
                        }
                    )

                    if response.status_code == 200:
                        data = response.json()
                        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    else:
                        logger.error(f"OpenAI API error: {response.status_code} - {response.text}")
                        return None

        except Exception as e:
            logger.error(f"LLM call failed for {speaker.player_name}: {e}")
            return None

    # ──────────────────────────────────────────────────────────────
    # Handoff Chain 模式：消息驱动的串行 worklist 编排
    # ──────────────────────────────────────────────────────────────

    async def handle_user_message(
        self,
        room_id: str,
        user_text: str,
        *,
        max_chain_depth: int = 8,
    ) -> ChainExecutionLog:
        """
        处理用户消息并触发链式 handoff 执行。

        仅在 HANDOFF_CHAIN 模式下使用。
        是消息驱动的：用户发一条消息 -> 猫猫链式接力 -> 无 @mention 时链条结束。

        Parameters
        ----------
        room_id : str
            房间 ID。
        user_text : str
            用户发送的消息内容。
        max_chain_depth : int
            最大链条深度（防止无限循环）。

        Returns
        -------
        ChainExecutionLog
            本次链条执行的完整日志。
        """
        if room_id not in self.sessions:
            raise ValueError(f"No session found for room {room_id}")

        session = self.sessions[room_id]
        if session.mode != ChatMode.HANDOFF_CHAIN:
            raise ValueError(
                f"handle_user_message only supports HANDOFF_CHAIN mode, "
                f"current mode is {session.mode.value}"
            )

        import uuid
        chain_log = ChainExecutionLog(
            chain_id=str(uuid.uuid4())[:8],
            trigger_text=user_text,
        )

        # 并发锁：同一个 session 同时只能有一条链在执行
        async with session._lock:
            try:
                await self._execute_handoff_chain(
                    session, user_text, max_chain_depth, chain_log
                )
            except Exception as e:
                chain_log.end_reason = "error"
                chain_log.ended_at = datetime.utcnow()
                logger.exception(
                    f"[Chain {chain_log.chain_id}] Error in handoff chain: {e}"
                )
                raise
            finally:
                if not chain_log.ended_at:
                    chain_log.ended_at = datetime.utcnow()

        return chain_log

    async def _execute_handoff_chain(
        self,
        session: ChatSession,
        user_text: str,
        max_depth: int,
        chain_log: ChainExecutionLog,
    ) -> None:
        """串行 worklist 执行核心"""
        room = self.bridge.get_or_create_room(session.room_id)

        # Step 1: 持久化用户消息
        room.post_message(
            sender_id="user",
            sender_name="铲屎官",
            content=user_text,
            message_type="normal",
        )

        # Step 2: 构建参与者名称映射
        participant_names = self._build_participant_name_map(session)
        active_ids = {
            p.player_id for p in session.participants.values() if p.is_active
        }

        # Step 3: 解析初始目标
        resolve_result = resolve_targets(
            user_text,
            participant_names,
            last_active_cat=session.last_active_cat,
            active_ids=active_ids,
        )
        worklist = list(resolve_result.targets)

        logger.info(
            f"[Chain {chain_log.chain_id}] Resolved initial targets: "
            f"{worklist} (source={resolve_result.source})"
        )

        if not worklist:
            chain_log.end_reason = "no_targets"
            chain_log.ended_at = datetime.utcnow()
            logger.warning(f"[Chain {chain_log.chain_id}] No targets resolved, chain ends immediately")
            return

        # Step 4: 串行 worklist 循环
        depth = 0
        while worklist and depth < max_depth:
            cat_id = worklist.pop(0)
            depth += 1

            speaker = session.participants.get(cat_id)
            if not speaker or not speaker.is_active:
                logger.warning(f"[Chain {chain_log.chain_id}] Skipping inactive cat {cat_id}")
                continue

            logger.info(
                f"[Chain {chain_log.chain_id}] Step {depth}/{max_depth}: "
                f"invoking {speaker.player_name} ({cat_id})"
            )

            # Step 4a: 上报意图 — analyzing（正在思考）
            await self._report_intention(speaker, "analyzing", f"正在分析 {session.topic or '讨论议题'}...")

            # Step 4b: 构建上下文（锚点 + 滑动窗口）
            context = self._build_chain_context(session, speaker, user_text)

            # Step 4c: 调用 LLM
            try:
                # 上报意图 — implementing（正在执行）
                await self._report_intention(speaker, "implementing", f"正在撰写回复...")
                response_text = await self._call_llm(speaker=speaker, context=context)
            except Exception as e:
                logger.error(f"[Chain {chain_log.chain_id}] LLM call failed for {speaker.player_name}: {e}")
                await self._report_intention(speaker, "blocked", f"LLM 调用失败: {e}", blockers=str(e))
                chain_log.steps.append({
                    "depth": depth,
                    "cat_id": cat_id,
                    "cat_name": speaker.player_name,
                    "status": "error",
                    "error": str(e),
                })
                chain_log.end_reason = "error"
                chain_log.ended_at = datetime.utcnow()
                return

            if not response_text:
                logger.warning(f"[Chain {chain_log.chain_id}] Empty response from {speaker.player_name}")
                await self._clear_intention(speaker)
                chain_log.steps.append({
                    "depth": depth,
                    "cat_id": cat_id,
                    "cat_name": speaker.player_name,
                    "status": "empty_response",
                })
                chain_log.end_reason = "error"
                chain_log.ended_at = datetime.utcnow()
                return

            # Step 4d: 持久化回复
            room.post_message(
                sender_id=speaker.player_id,
                sender_name=speaker.player_name,
                content=response_text,
                message_type="normal",
            )
            speaker.message_count += 1
            speaker.last_message_time = datetime.utcnow()
            session.current_turn += 1
            session.last_active_cat = cat_id

            # Step 4e: 清除意图
            await self._clear_intention(speaker)

            # Step 4f: A2A 解析下一跳
            a2a_result = parse_a2a_mentions(
                response_text,
                current_cat_id=cat_id,
                participant_names=participant_names,
            )

            chain_log.steps.append({
                "depth": depth,
                "cat_id": cat_id,
                "cat_name": speaker.player_name,
                "status": "ok",
                "response_preview": response_text[:80],
                "a2a_next": a2a_result.next_targets,
                "a2a_raw": a2a_result.raw_mentions,
                "a2a_ignored_self": a2a_result.ignored_self,
                "a2a_ignored_unknown": a2a_result.ignored_unknown,
            })

            # Step 4g: 追加到 worklist（不重复追加已在 worklist 中的）
            existing = set(worklist)
            for target in a2a_result.next_targets:
                if target not in existing:
                    worklist.append(target)
                    existing.add(target)

            logger.info(
                f"[Chain {chain_log.chain_id}] {speaker.player_name} done. "
                f"Next hops: {a2a_result.next_targets}. "
                f"Worklist remaining: {worklist}"
            )

            # Step 4h: 短暂让出，避免瞬间打爆速率限制
            await asyncio.sleep(1)

        # 链条结束
        if depth >= max_depth:
            chain_log.end_reason = "max_depth"
        elif not worklist:
            chain_log.end_reason = "completed"
        chain_log.ended_at = datetime.utcnow()

        logger.info(
            f"[Chain {chain_log.chain_id}] Finished: "
            f"reason={chain_log.end_reason}, steps={depth}"
        )

    def _build_chain_context(
        self,
        session: ChatSession,
        speaker: ChatParticipant,
        trigger_text: str,
    ) -> list:
        """
        构建 handoff chain 上下文（锚点 + 滑动窗口）。

        永远保留：
        1. 系统提示（含 handoff 指令）
        2. 原始触发消息（锚点，防止深层链条丢失目标）
        3. 最近 N 条历史消息（滑动窗口）
        """
        room = self.bridge.get_or_create_room(session.room_id)
        messages: list[dict] = []

        # ── System Prompt ──
        participant_list = ", ".join(
            p.player_name for p in session.participants.values() if p.is_active
        )
        system_content = speaker.system_prompt or f"你是{speaker.player_name}。"
        if session.topic:
            system_content += f"\n当前任务：{session.topic}"
        system_content += (
            f"\n\n## 协作规则\n"
            f"当前参与者：{participant_list}\n"
            f"- 先给出你的回答或工作成果\n"
            f"- 如果需要另一只猫接手，在新的一行开头写 @猫名（例如 @布偶猫 请审查代码）\n"
            f"- 最多点名 1-2 只猫\n"
            f"- 如果任务已完成或不需要其他猫参与，就不要写 @\n"
            f"- 不要在正文中间随意提及 @猫名\n"
        )
        messages.append({"role": "system", "content": system_content})

        # ── 锚点：原始触发消息 ──
        messages.append({"role": "user", "content": f"[任务发起] {trigger_text}"})

        # ── 滑动窗口：最近 N 条（排除触发消息本身） ──
        recent = room.message_history[-15:]
        # 跳过靠近末尾且就是 trigger 的那条
        for msg in recent:
            if msg.content == trigger_text and msg.sender_id == "user":
                continue
            role = "assistant" if msg.sender_id == speaker.player_id else "user"
            messages.append({
                "role": role,
                "content": f"{msg.sender_name}: {msg.content}",
            })

        return messages

    def _build_participant_name_map(self, session: ChatSession) -> dict[str, str]:
        """构建 {name -> player_id} 映射"""
        return {
            p.player_name: p.player_id
            for p in session.participants.values()
            if p.is_active
        }

    # ──────────────────────────────────────────────────────────────
    # F200 P0: 意图上报（通过 MCP Bridge 调用回调服务器）
    # ──────────────────────────────────────────────────────────────

    async def _report_intention(
        self,
        speaker: ChatParticipant,
        intent_type: str,  # analyzing | implementing | waiting | blocked
        intent: str,
        *,
        blockers: Optional[str] = None,
        urgency: float = 0.5,
        task_id: Optional[str] = None,
    ) -> None:
        """
        向回调服务器上报意图状态。

        CatChat 的编排器通过 bridge 调用 Node.js 回调服务器，
        回调服务器再广播到前端侧边栏。
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{os.getenv('CAT_CAFE_API_URL', 'http://localhost:3200')}/api/callbacks/report-intention",
                    json={
                        "invocationId": speaker.mcp_invocation_id,
                        "callbackToken": speaker.mcp_callback_token,
                        "intent": intent,
                        "type": intent_type,
                        "blockers": blockers,
                        "urgency": urgency,
                        "taskId": task_id,
                    }
                )
                if response.status_code == 200:
                    logger.info(f"[intention][{speaker.player_id}] {intent_type}: {intent}")
                else:
                    logger.warning(f"[intention][{speaker.player_id}] failed: {response.status_code}")
        except Exception as e:
            # 意图上报失败不影响主流程
            logger.warning(f"[intention][{speaker.player_id}] report failed: {e}")

    async def _clear_intention(
        self,
        speaker: ChatParticipant,
        *,
        task_id: Optional[str] = None,
    ) -> None:
        """清除意图状态"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{os.getenv('CAT_CAFE_API_URL', 'http://localhost:3200')}/api/callbacks/clear-intention",
                    json={
                        "invocationId": speaker.mcp_invocation_id,
                        "callbackToken": speaker.mcp_callback_token,
                        "taskId": task_id,
                    }
                )
                if response.status_code == 200:
                    logger.info(f"[intention][{speaker.player_id}] cleared")
        except Exception as e:
            logger.warning(f"[intention][{speaker.player_id}] clear failed: {e}")

    # ──────────────────────────────────────────────────────────────
    # F200-LeadCat: 主导猫管理
    # ──────────────────────────────────────────────────────────────

    def set_lead_cat(self, room_id: str, lead_cat_id: str) -> dict:
        """
        铲屎官手动指定主导猫。
        """
        if room_id not in self.sessions:
            return {"status": "error", "message": f"No session for {room_id}"}

        session = self.sessions[room_id]
        if lead_cat_id not in session.participants:
            return {"status": "error", "message": f"Cat {lead_cat_id} not in session"}

        session.lead_cat_id = lead_cat_id
        session.lead_cat_verified = True
        session.lead_cat_votes.clear()
        session.lead_cat_vote_open = False
        logger.info(f"[lead-cat] Set by user: {lead_cat_id}")
        return {
            "status": "ok",
            "lead_cat_id": lead_cat_id,
            "lead_cat_name": session.participants[lead_cat_id].player_name,
        }

    def open_lead_cat_vote(self, room_id: str) -> dict:
        """
        开启主导猫投票（铲屎官不指定时 fallback）。
        """
        if room_id not in self.sessions:
            return {"status": "error", "message": f"No session for {room_id}"}

        session = self.sessions[room_id]
        session.lead_cat_votes.clear()
        session.lead_cat_vote_open = True
        logger.info(f"[lead-cat] Vote opened in {room_id}")
        return {"status": "ok", "vote_open": True}

    def cast_lead_cat_vote(self, room_id: str, voter_cat_id: str, voted_cat_id: str) -> dict:
        """
        主导猫投票：cat 投票给另一个 cat。
        格式：[VOTE:cat_id]
        """
        if room_id not in self.sessions:
            return {"status": "error", "message": f"No session for {room_id}"}

        session = self.sessions[room_id]
        if not session.lead_cat_vote_open:
            return {"status": "error", "message": "Vote not open"}

        if voter_cat_id not in session.participants:
            return {"status": "error", "message": "Voter not in session"}

        if voted_cat_id not in session.participants:
            return {"status": "error", "message": "Voted cat not in session"}

        session.lead_cat_votes[voter_cat_id] = voted_cat_id
        logger.info(f"[lead-cat] Vote: {voter_cat_id} → {voted_cat_id}")
        return {"status": "ok", "votes": len(session.lead_cat_votes)}

    def tally_lead_cat_vote(self, room_id: str) -> dict:
        """
        统计票数并选出主导猫。
        """
        if room_id not in self.sessions:
            return {"status": "error", "message": f"No session for {room_id}"}

        session = self.sessions[room_id]
        if not session.lead_cat_vote_open:
            return {"status": "error", "message": "Vote not open"}

        session.lead_cat_vote_open = False

        # 统计得票
        vote_counts: Dict[str, int] = {}
        for voted in session.lead_cat_votes.values():
            vote_counts[voted] = vote_counts.get(voted, 0) + 1

        if not vote_counts:
            return {"status": "ok", "elected": None, "reason": "no_votes"}

        elected_id = max(vote_counts, key=vote_counts.get)
        session.lead_cat_id = elected_id
        session.lead_cat_verified = True

        logger.info(f"[lead-cat] Elected: {elected_id} with {vote_counts[elected_id]} votes")
        return {
            "status": "ok",
            "elected": elected_id,
            "elected_name": session.participants[elected_id].player_name,
            "votes": vote_counts,
        }

    def _select_topic_from_pool(self) -> Optional[str]:
        """从议题库随机选一个话题"""
        import random
        import yaml
        try:
            pool_path = Path(__file__).parent.parent.parent / "config" / "topic_pool.yaml"
            if pool_path.exists():
                with open(pool_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    topics = data.get("topics", [])
                    if topics:
                        return random.choice(topics).get("text")
        except Exception as e:
            logger.warning(f"[lead-cat] Failed to load topic pool: {e}")
        return None

    async def _lead_cat_initiate_topic(self, session: ChatSession) -> Optional[str]:
        """
        主导猫发起议题（空闲超时触发）。
        返回发起的话题文本。
        """
        if not session.lead_cat_id:
            return None

        speaker = session.participants.get(session.lead_cat_id)
        if not speaker:
            return None

        topic = self._select_topic_from_pool()
        if not topic:
            topic = "大家有什么想讨论的吗？"

        logger.info(f"[lead-cat][{speaker.player_name}] initiating topic: {topic}")

        # 主导猫上报意图
        await self._report_intention(speaker, "implementing", f"正在发起议题: {topic}")

        try:
            context = [
                {"role": "system", "content": (
                    f"你是{speaker.player_name}，是这场讨论的主导猫。"
                    "请用一句话简洁地提出一个新话题发起讨论。"
                    "直接说出话题，不要加前缀或解释。"
                )},
                {"role": "user", "content": f"请发起讨论。推荐话题：{topic}"}
            ]
            result = await self._call_llm(speaker=speaker, context=context)
            await self._clear_intention(speaker)

            if result:
                room = self.bridge.get_or_create_room(session.room_id)
                room.post_message(
                    sender_id=speaker.player_id,
                    sender_name=speaker.player_name,
                    content=result,
                    message_type="normal",
                )
                speaker.message_count += 1
                speaker.last_message_time = datetime.utcnow()
                session.current_turn += 1
                session.last_active_cat = speaker.player_id
                return result
        except Exception as e:
            logger.error(f"[lead-cat] Failed to initiate topic: {e}")
            await self._report_intention(speaker, "blocked", f"发起议题失败: {e}", blockers=str(e))

        return None

    def get_session_status(self, room_id: str) -> Optional[dict]:
        """获取会话状态"""
        if room_id not in self.sessions:
            return None

        session = self.sessions[room_id]
        lead_cat_name = None
        if session.lead_cat_id and session.lead_cat_id in session.participants:
            lead_cat_name = session.participants[session.lead_cat_id].player_name

        return {
            "room_id": session.room_id,
            "mode": session.mode.value,
            "topic": session.topic,
            "is_active": session.is_active,
            "is_processing": session.is_processing,
            "current_turn": session.current_turn,
            "max_turns": session.max_turns,
            "chain_max_turns": session.chain_max_turns,
            "lead_cat_id": session.lead_cat_id,
            "lead_cat_name": lead_cat_name,
            "lead_cat_verified": session.lead_cat_verified,
            "lead_cat_vote_open": session.lead_cat_vote_open,
            "vote_count": len(session.lead_cat_votes),
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
