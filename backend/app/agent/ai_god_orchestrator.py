"""AI-driven God (Judge) Orchestrator.

Instead of hard-coded phase logic, an LLM acts as the judge:
it reads full game state and emits structured commands that the
engine executes.  Player agents remain child agents invoked via
AgentScheduler, but the *orchestration* decisions come from the
god LLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from app.agent.agent_scheduler import AgentScheduler
from app.agent.fallback_strategies import FallbackStrategies
from app.agent.perspective_engine import PerspectiveEngine
from app.core.models import Phase, Role
from app.engine.game_engine import GameEngine

logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)
DAY_PHASE_MAX_CONCURRENCY = max(1, int(os.getenv("CAT_DAY_PHASE_MAX_CONCURRENCY", "8")))
WOLF_PHASE_MAX_CONCURRENCY = max(1, int(os.getenv("CAT_WOLF_PHASE_MAX_CONCURRENCY", "2")))
LLM_LOG_MAX_CHARS = max(100, int(os.getenv("CAT_LLM_LOG_MAX_CHARS", "1024")))


def _clip_text(text: str, limit: int = LLM_LOG_MAX_CHARS) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f" ...[truncated {len(text) - limit} chars]"

# ---------------------------------------------------------------------------
# God-Agent system prompt (injected at every phase call)
# ---------------------------------------------------------------------------

GOD_SYSTEM_PROMPT = """\
你是一场 AI 狼人杀游戏的【法官（上帝）】。你拥有全知视角，能看到所有角色身份和所有行动。

# 游戏规则
- 角色：狼人(werewolf)、村民(villager)、预言家(seer)、女巫(witch)、猎人(hunter)、守卫(guard)、白痴(fool)
- 阵营：狼人阵营 vs 好人阵营（村民+神职）
- 获胜条件：好人全部死亡→狼人胜；狼人全部死亡→好人胜
- 夜间顺序：狼人→守卫→女巫→预言家
- 守卫不能连续两晚守同一人
- 女巫有解药(第一晚可自救)和毒药各一瓶
- 猎人被毒死不能开枪
- 白天投票放逐，如票数相同则无人出局
- 白痴被投票出局后翻牌免死一次

# 你的职责
1. 在每个阶段开始时，向玩家们宣布阶段信息
2. 收集玩家的行动结果后，做出裁决
3. 宣布结果（谁死了、谁被保护等）
4. 判断游戏是否结束

# 输出格式
你必须输出严格的 JSON，格式如下：
```json
{
  "narration": "你作为法官对所有人说的话（剧情旁白）",
  "phase_instructions": "你对当前需要行动的玩家的指令",
  "rulings": {
    "kill_targets": ["player_id_1"],
    "save_targets": ["player_id_2"],
    "poison_targets": [],
    "vote_result": "player_id or null",
    "hunter_shoot": "player_id or null"
  },
  "reasoning": "你的内部推理（不对玩家公开）",
  "next_phase_hint": "你建议进入的下一阶段"
}
```

重要：
- 你的 narration 会被广播给所有人看到
- rulings 中的内容是你的裁决，引擎会据此执行
- 请严格按照游戏规则裁决，不要偏袒任何一方
- 如果某个阶段没有需要裁决的内容，对应字段留空/null
- 在 day_vote 阶段，narration 只能是“投票指令/流程引导”，不能提前宣布投票结果、票型统计或放逐对象
"""


@dataclass(slots=True)
class GodAgentConfig:
    """LLM endpoint config for the god (judge) agent."""

    api_url: str = ""
    api_key: str = ""
    model_name: str = "gpt-4o-mini"
    provider: str = "openai"   # openai | claude | ...
    timeout_sec: int = 60
    temperature: float = 0.7
    max_tokens: int = 2000


@dataclass(slots=True)
class GodNarration:
    """One narration entry produced by the god agent."""

    phase: str
    narration: str
    reasoning: str
    rulings: Dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    latency_ms: float = 0.0
    is_fallback: bool = False


class AIGodOrchestrator:
    """LLM-driven judge that replaces the hard-coded GodOrchestrator.

    Shares the same public interface so RoomManager/API can use either
    interchangeably:
      - ``all_agents_ready(engine)``
      - ``run_single_phase(engine)``
      - ``run_to_game_over(engine, max_steps)``
      - ``scheduler``  (AgentScheduler instance for child agents)
    """

    def __init__(
        self,
        god_config: Optional[GodAgentConfig] = None,
        scheduler: Optional[AgentScheduler] = None,
        perspective_engine: Optional[PerspectiveEngine] = None,
        fallback: Optional[FallbackStrategies] = None,
    ) -> None:
        self.god_config = god_config or GodAgentConfig()
        self.scheduler = scheduler or AgentScheduler(fallback=fallback)
        self.perspective = perspective_engine or PerspectiveEngine()
        self.narration_log: List[GodNarration] = []
        # Re-use the same prompt templates for child agents (lightweight)
        from app.agent.god_orchestrator import PromptTemplates

        self.templates = PromptTemplates()

    # ------------------------------------------------------------------
    # Public interface (same as GodOrchestrator)
    # ------------------------------------------------------------------

    def all_agents_ready(self, engine: GameEngine) -> bool:
        for p in engine.snapshot.players.values():
            reg = self.scheduler.registry.get(p.player_id)
            if not reg or not reg.online:
                return False
        return True

    async def run_to_game_over(self, engine: GameEngine, max_steps: int = 500) -> dict:
        steps = 0
        while not engine.snapshot.game_over and steps < max_steps:
            await self.run_single_phase(engine)
            steps += 1
        return {
            "game_over": engine.snapshot.game_over,
            "winner": engine.snapshot.winner.value if engine.snapshot.winner else None,
            "steps": steps,
            "metrics": self.scheduler.metrics.to_dict(),
        }

    async def run_single_phase(self, engine: GameEngine) -> None:
        """Ask the god LLM what to do, then execute child agent actions
        and apply rulings."""
        phase = engine.snapshot.phase

        if phase == Phase.DAY_ANNOUNCE:
            # Announce must be strictly consistent with engine settlement.
            # Use deterministic summary instead of free-form LLM narration.
            self._emit_day_announce_summary(engine)
            engine.advance_phase()
            return

        if phase == Phase.NIGHT_WOLF:
            god_result = await self._ask_god(engine, "night_wolf")
            self._broadcast_narration(engine, god_result)
            await self._run_wolf_phase(engine, god_result)
            engine.advance_phase()
            return

        if phase == Phase.NIGHT_GUARD:
            god_result = await self._ask_god(engine, "night_guard")
            self._broadcast_narration(engine, god_result)
            await self._run_guard_phase(engine, god_result)
            engine.advance_phase()
            return

        if phase == Phase.NIGHT_WITCH:
            god_result = await self._ask_god(engine, "night_witch")
            self._broadcast_narration(engine, god_result)
            await self._run_witch_phase(engine, god_result)
            engine.advance_phase()
            return

        if phase == Phase.NIGHT_SEER:
            god_result = await self._ask_god(engine, "night_seer")
            self._broadcast_narration(engine, god_result)
            await self._run_seer_phase(engine, god_result)
            engine.advance_phase()
            return

        if phase == Phase.DAY_DISCUSS:
            god_result = await self._ask_god(engine, "day_discuss")
            self._broadcast_narration(engine, god_result)
            await self._run_day_discuss(engine, god_result)
            engine.advance_phase()
            return

        if phase == Phase.DAY_VOTE:
            god_result = await self._ask_god(engine, "day_vote")
            self._broadcast_narration(engine, god_result)
            await self._run_day_vote(engine, god_result)
            engine.advance_phase()
            await self._run_hunter_if_needed(engine)
            return

    # ------------------------------------------------------------------
    # God LLM call
    # ------------------------------------------------------------------

    async def _ask_god(self, engine: GameEngine, phase_label: str) -> GodNarration:
        """Build a prompt with full game state and ask the god LLM."""
        game_state = self._build_god_view(engine)
        user_prompt = (
            f"当前阶段: {phase_label}\n"
            f"回合数: {engine.snapshot.round_context.round_no}\n"
            f"存活玩家: {json.dumps(game_state['alive_players'], ensure_ascii=False)}\n"
            f"死亡玩家: {json.dumps(game_state['dead_players'], ensure_ascii=False)}\n"
            f"角色分布: {json.dumps(game_state['role_map'], ensure_ascii=False)}\n"
            f"本回合死亡: {json.dumps(game_state['deaths_this_round'], ensure_ascii=False)}\n"
            f"历史行动记录: {json.dumps(game_state['recent_audit'][-20:], ensure_ascii=False)}\n"
            f"\n请以法官身份，对当前阶段做出指示和裁决。输出严格JSON。"
        )

        start = time.perf_counter()
        try:
            raw_text = await self._call_god_llm(user_prompt)
            latency_ms = (time.perf_counter() - start) * 1000
            parsed = self._parse_god_response(raw_text)
            narration = GodNarration(
                phase=phase_label,
                narration=parsed.get("narration", ""),
                reasoning=parsed.get("reasoning", ""),
                rulings=parsed.get("rulings", {}),
                raw_response=raw_text[:2000],
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("God LLM call failed: %s", exc)
            narration = GodNarration(
                phase=phase_label,
                narration=self._fallback_narration(phase_label),
                reasoning=f"[god-llm-error: {type(exc).__name__}: {exc}]",
                rulings={},
                latency_ms=latency_ms,
                is_fallback=True,
            )

        self.narration_log.append(narration)
        return narration

    async def _call_god_llm(self, user_prompt: str) -> str:
        """Call the god agent's LLM endpoint."""
        cfg = self.god_config
        if not cfg.api_url or not cfg.api_key:
            raise ValueError("god agent LLM not configured (api_url / api_key missing)")

        provider = (cfg.provider or "openai").lower()
        logger.info(
            "[GodLLM][request] provider=%s model=%s prompt=%s",
            provider,
            cfg.model_name,
            _clip_text(user_prompt),
        )

        if provider == "claude":
            text = await self._call_claude(user_prompt)
        else:
            text = await self._call_openai_compatible(user_prompt)

        logger.info(
            "[GodLLM][response] provider=%s model=%s content=%s",
            provider,
            cfg.model_name,
            _clip_text(text),
        )
        return text

    async def _call_openai_compatible(self, user_prompt: str) -> str:
        cfg = self.god_config
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        }
        body = {
            "model": cfg.model_name,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "messages": [
                {"role": "system", "content": GOD_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        async with httpx.AsyncClient(timeout=cfg.timeout_sec) as client:
            resp = await client.post(cfg.api_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def _call_claude(self, user_prompt: str) -> str:
        cfg = self.god_config
        headers = {
            "Content-Type": "application/json",
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": cfg.model_name,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "system": GOD_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        api_url = cfg.api_url
        if not api_url.endswith("/messages"):
            api_url = api_url.rstrip("/") + "/messages"
        async with httpx.AsyncClient(timeout=cfg.timeout_sec) as client:
            resp = await client.post(api_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]

    # ------------------------------------------------------------------
    # God view builder (full state, not perspective-limited)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_god_view(engine: GameEngine) -> Dict[str, Any]:
        snapshot = engine.snapshot
        ctx = snapshot.round_context

        role_map = {}
        alive_players = []
        dead_players = []
        for p in snapshot.players.values():
            role_map[p.nickname or p.player_id] = {
                "player_id": p.player_id,
                "role": p.role.value if p.role else "unknown",
                "alive": p.alive,
                "online": p.online,
            }
            name = p.nickname or p.player_id
            if p.alive:
                alive_players.append(name)
            else:
                dead_players.append(name)

        deaths_this_round = {
            (snapshot.players[pid].nickname or pid): cause.value
            for pid, cause in ctx.deaths_this_round.items()
        }

        recent_audit = snapshot.action_audit_log[-30:] if snapshot.action_audit_log else []

        return {
            "room_id": snapshot.room_id,
            "round_no": ctx.round_no,
            "phase": snapshot.phase.value,
            "alive_players": alive_players,
            "dead_players": dead_players,
            "role_map": role_map,
            "deaths_this_round": deaths_this_round,
            "recent_audit": recent_audit,
            "wolf_target": ctx.wolf_target,
            "night_actions": {
                "wolf_votes": dict(ctx.night_actions.wolf_votes),
                "guard_target": ctx.night_actions.guard_target,
                "witch_save": ctx.night_actions.witch_save,
                "witch_poison": ctx.night_actions.witch_poison_target,
                "seer_target": ctx.night_actions.seer_target,
            },
        }

    # ------------------------------------------------------------------
    # Broadcast god narration into engine audit log
    # ------------------------------------------------------------------

    def _broadcast_narration(self, engine: GameEngine, narration: GodNarration) -> None:
        text = narration.narration or narration.reasoning or ""
        if not text:
            text = self._fallback_narration(narration.phase)
        text = self._sanitize_public_narration(narration.phase, text)
        engine._audit(
            "god_narration",
            "god",
            {
                "phase": narration.phase,
                "content": text,
                "is_fallback": narration.is_fallback,
                "latency_ms": round(narration.latency_ms, 2),
            },
        )

    @classmethod
    def _sanitize_public_narration(cls, phase: str, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return cls._fallback_narration(phase)

        if str(phase or "").lower() == "day_vote":
            has_result_claim = bool(
                re.search(r"投票结果|平票|放逐|出局|得票|票型统计|无人出局", cleaned, flags=re.IGNORECASE)
            )
            if has_result_claim:
                return "进入投票环节，请每位存活玩家明确给出放逐目标。"
        return cleaned

    # ------------------------------------------------------------------
    # Phase runners (child agent calls — same pattern as GodOrchestrator
    # but now with god narration injected)
    # ------------------------------------------------------------------

    async def _run_wolf_phase(self, engine: GameEngine, god_result: GodNarration) -> None:
        wolves = self._ordered_wolves_for_discussion(engine)
        if not wolves:
            return

        proposals: dict[str, str] = {}
        discussion_so_far: list[dict] = []
        for wolf in wolves:
            visible = self.perspective.build_visible_state(engine, wolf.player_id)
            visible["god_narration"] = god_result.narration
            visible["wolf_teammates"] = [
                {
                    "player_id": p.player_id,
                    "nickname": p.nickname,
                }
                for p in wolves
                if p.player_id != wolf.player_id
            ]
            visible["wolf_discussion_so_far"] = list(discussion_so_far)
            result = await self.scheduler.trigger_agent_action(
                player_id=wolf.player_id,
                session_id=engine.snapshot.room_id,
                role=Role.WEREWOLF.value,
                phase="night_wolf_discuss",
                visible_state=visible,
                prompt_template=self.templates.night_wolf_discuss,
                strategy_name="night_wolf",
            )
            target = self._normalize_target(engine, result.get("action", {}).get("target"), exclude_wolves=True)
            speech = self._wolf_discuss_speech_text(engine, result, target)
            is_fallback = bool(result.get("fallback_reason"))
            fallback_reason = result.get("fallback_reason")
            engine._audit(
                "agent_speech",
                wolf.player_id,
                {
                    "phase": "night_wolf_discuss",
                    "role": Role.WEREWOLF.value,
                    "content": speech,
                    "is_fallback": is_fallback,
                    "fallback_reason": fallback_reason,
                },
            )
            discussion_so_far.append(
                {
                    "player_id": wolf.player_id,
                    "nickname": wolf.nickname,
                    "content": speech,
                    "target": target,
                    "is_fallback": is_fallback,
                }
            )
            if target:
                proposals[wolf.player_id] = target

        consensus_target = self._consensus_target(engine, proposals)
        used_fallback = False
        if not consensus_target:
            consensus_target = self._fallback_wolf_target(engine)
            used_fallback = consensus_target is not None
        if not consensus_target:
            return

        consensus_name = engine.snapshot.players[consensus_target].nickname or consensus_target
        if used_fallback:
            judge_line = f"狼人讨论未形成一致，法官按托管规则选定：今晚目标为 {consensus_name}。"
        else:
            judge_line = f"狼人已讨论并达成一致：今晚目标为 {consensus_name}。"
        engine._audit(
            "agent_speech",
            "god",
            {
                "phase": Phase.NIGHT_WOLF.value,
                "role": "judge",
                "content": judge_line,
                "is_fallback": False,
                "fallback_reason": None,
            },
        )

        for wolf in wolves:
            try:
                engine.submit_night_action(wolf.player_id, consensus_target)
            except ValueError:
                continue

    @staticmethod
    def _ordered_wolves_for_discussion(engine: GameEngine) -> list:
        wolves = [p for p in engine.snapshot.players.values() if p.alive and p.role == Role.WEREWOLF]
        wolves.sort(key=lambda p: p.player_id)
        if len(wolves) <= 1:
            return wolves
        round_no = getattr(engine.snapshot.round_context, "round_no", 0)
        seed = f"{engine.snapshot.room_id}|{round_no}|night_wolf_discuss"
        rng = random.Random(seed)
        rng.shuffle(wolves)
        return wolves

    async def _run_guard_phase(self, engine: GameEngine, god_result: GodNarration) -> None:
        guard = next((p for p in engine.snapshot.players.values() if p.alive and p.role == Role.GUARD), None)
        if not guard:
            return
        visible = self.perspective.build_visible_state(engine, guard.player_id)
        visible["god_narration"] = god_result.narration
        result = await self.scheduler.trigger_agent_action(
            player_id=guard.player_id,
            session_id=engine.snapshot.room_id,
            role=Role.GUARD.value,
            phase=Phase.NIGHT_GUARD.value,
            visible_state=visible,
            prompt_template=self.templates.night_guard,
            strategy_name="night_guard",
        )
        target = self._normalize_target(engine, result.get("action", {}).get("target"), allow_self=True)
        guard_content = self._guard_speech_text(engine, result, target)
        engine._audit(
            "agent_speech",
            guard.player_id,
            {
                "phase": Phase.NIGHT_GUARD.value,
                "role": Role.GUARD.value,
                "content": guard_content,
                "is_fallback": bool(result.get("fallback_reason")),
                "fallback_reason": result.get("fallback_reason"),
            },
        )
        if target:
            try:
                engine.submit_night_action(guard.player_id, target)
            except ValueError:
                return

    async def _run_witch_phase(self, engine: GameEngine, god_result: GodNarration) -> None:
        witch = next((p for p in engine.snapshot.players.values() if p.alive and p.role == Role.WITCH), None)
        if not witch:
            return
        visible = self.perspective.build_visible_state(engine, witch.player_id)
        visible["god_narration"] = god_result.narration
        result = await self.scheduler.trigger_agent_action(
            player_id=witch.player_id,
            session_id=engine.snapshot.room_id,
            role=Role.WITCH.value,
            phase=Phase.NIGHT_WITCH.value,
            visible_state=visible,
            prompt_template=self.templates.night_witch,
            strategy_name="night_witch",
        )
        action = result.get("action", {})
        target = self._normalize_target(engine, action.get("target"), allow_self=False)
        save = bool(action.get("save", False))
        witch_content = self._speech_text(result, default="女巫正在权衡药剂使用。")
        if save:
            wolf_target = engine.snapshot.round_context.wolf_target
            wolf_name = (
                (engine.snapshot.players[wolf_target].nickname or wolf_target)
                if wolf_target and wolf_target in engine.snapshot.players
                else "未知"
            )
            witch_content += f" 使用解药：是（救助目标：{wolf_name}）。"
        else:
            witch_content += " 使用解药：否。"
        if target:
            poison_name = engine.snapshot.players[target].nickname or target
            witch_content += f" 使用毒药：是（毒杀目标：{poison_name}）。"
        else:
            witch_content += " 使用毒药：否。"
        engine._audit(
            "agent_speech",
            witch.player_id,
            {
                "phase": Phase.NIGHT_WITCH.value,
                "role": Role.WITCH.value,
                "content": witch_content,
                "is_fallback": bool(result.get("fallback_reason")),
                "fallback_reason": result.get("fallback_reason"),
            },
        )
        try:
            engine.submit_night_action(witch.player_id, target, save=save)
        except ValueError:
            return

    async def _run_seer_phase(self, engine: GameEngine, god_result: GodNarration) -> None:
        seer = next((p for p in engine.snapshot.players.values() if p.alive and p.role == Role.SEER), None)
        if not seer:
            return
        visible = self.perspective.build_visible_state(engine, seer.player_id)
        visible["god_narration"] = god_result.narration
        result = await self.scheduler.trigger_agent_action(
            player_id=seer.player_id,
            session_id=engine.snapshot.room_id,
            role=Role.SEER.value,
            phase=Phase.NIGHT_SEER.value,
            visible_state=visible,
            prompt_template=self.templates.night_seer,
            strategy_name="night_seer",
        )
        target = self._normalize_target(engine, result.get("action", {}).get("target"), allow_self=False)
        seer_content = self._seer_speech_text(engine, result, target)
        engine._audit(
            "agent_speech",
            seer.player_id,
            {
                "phase": Phase.NIGHT_SEER.value,
                "role": Role.SEER.value,
                "content": seer_content,
                "is_fallback": bool(result.get("fallback_reason")),
                "fallback_reason": result.get("fallback_reason"),
            },
        )
        if target:
            try:
                engine.submit_night_action(seer.player_id, target)
            except ValueError:
                return

    async def _run_day_discuss(self, engine: GameEngine, god_result: GodNarration) -> None:
        alive_players = sorted(
            [p for p in engine.snapshot.players.values() if p.alive],
            key=lambda p: p.player_id,
        )
        discussion_so_far: list[dict] = []

        for player in alive_players:
            visible = self.perspective.build_visible_state(engine, player.player_id)
            visible["god_narration"] = god_result.narration
            visible["day_discussion_so_far"] = list(discussion_so_far)
            result = await self.scheduler.trigger_agent_action(
                player_id=player.player_id,
                session_id=engine.snapshot.room_id,
                role=player.role.value,
                phase=Phase.DAY_DISCUSS.value,
                visible_state=visible,
                prompt_template=self.templates.day_discuss,
                strategy_name="day_discuss",
            )
            speech_text, thinking_text = self._split_day_discuss_texts(
                result,
                default_speech="我先听听大家的意见。",
            )
            speech_text = self._sanitize_day_discuss_public_speech(speech_text)
            engine._audit(
                "agent_speech",
                player.player_id,
                {
                    "phase": Phase.DAY_DISCUSS.value,
                    "role": player.role.value,
                    "content": speech_text,
                    "thought_content": thinking_text,
                    "is_fallback": bool(result.get("fallback_reason")),
                    "fallback_reason": result.get("fallback_reason"),
                },
            )
            discussion_so_far.append(
                {
                    "player_id": player.player_id,
                    "nickname": player.nickname,
                    "content": speech_text,
                }
            )

    async def _run_day_vote(self, engine: GameEngine, god_result: GodNarration) -> None:
        alive_players = [p for p in engine.snapshot.players.values() if p.alive]
        sem = asyncio.Semaphore(DAY_PHASE_MAX_CONCURRENCY)

        async def _one(player) -> None:
            async with sem:
                visible = self.perspective.build_visible_state(engine, player.player_id)
                visible["god_narration"] = god_result.narration
                result = await self.scheduler.trigger_agent_action(
                    player_id=player.player_id,
                    session_id=engine.snapshot.room_id,
                    role=player.role.value,
                    phase=Phase.DAY_VOTE.value,
                    visible_state=visible,
                    prompt_template=self.templates.day_vote,
                    strategy_name="day_vote",
                )
                target = self._normalize_target(engine, result.get("action", {}).get("target"), allow_self=False)
                try:
                    engine.submit_vote(player.player_id, target)
                except ValueError:
                    return
                vote_speech, vote_thinking = self._split_day_vote_texts(
                    result,
                    default_speech="我选择弃票。",
                )
                vote_content = self._vote_speech_text(engine, target, vote_speech)
                engine._audit(
                    "agent_speech",
                    player.player_id,
                    {
                        "phase": Phase.DAY_VOTE.value,
                        "role": player.role.value,
                        "content": vote_content,
                        "thought_content": vote_thinking,
                        "is_fallback": bool(result.get("fallback_reason")),
                        "fallback_reason": result.get("fallback_reason"),
                    },
                )

        await asyncio.gather(*[_one(player) for player in alive_players])

    async def _run_hunter_if_needed(self, engine: GameEngine) -> None:
        hunter = next(
            (
                p
                for p in engine.snapshot.players.values()
                if p.role == Role.HUNTER and not p.alive and p.can_hunter_shoot
            ),
            None,
        )
        if not hunter:
            return
        god_result = await self._ask_god(engine, "hunter_shot")
        self._broadcast_narration(engine, god_result)
        visible = self.perspective.build_visible_state(engine, hunter.player_id)
        visible["god_narration"] = god_result.narration
        result = await self.scheduler.trigger_agent_action(
            player_id=hunter.player_id,
            session_id=engine.snapshot.room_id,
            role=Role.HUNTER.value,
            phase="hunter_shot",
            visible_state=visible,
            prompt_template=self.templates.hunter_shot,
            strategy_name="hunter_shot",
        )
        target = self._normalize_target(engine, result.get("action", {}).get("target"), allow_self=False)
        hunter_content = self._hunter_speech_text(engine, result, target)
        engine._audit(
            "agent_speech",
            hunter.player_id,
            {
                "phase": "hunter_shot",
                "role": Role.HUNTER.value,
                "content": hunter_content,
                "is_fallback": bool(result.get("fallback_reason")),
                "fallback_reason": result.get("fallback_reason"),
            },
        )
        if target:
            try:
                engine.submit_hunter_shot(hunter.player_id, target)
            except ValueError:
                return

    # ------------------------------------------------------------------
    # Helper: parse god LLM response
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_god_response(raw: str) -> Dict[str, Any]:
        """Extract JSON from the god LLM response text."""
        text = raw.strip()
        # Try to find JSON block in markdown code fence
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start)
            text = text[start:end].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find first { ... } block
            brace_start = text.find("{")
            brace_end = text.rfind("}")
            if brace_start >= 0 and brace_end > brace_start:
                try:
                    return json.loads(text[brace_start : brace_end + 1])
                except json.JSONDecodeError:
                    pass
            return {"narration": text[:500], "reasoning": "parse-failed", "rulings": {}}

    # ------------------------------------------------------------------
    # Fallback narration when god LLM fails
    # ------------------------------------------------------------------

    _PHASE_NARRATION_MAP = {
        "night_wolf":  "天黑请闭眼，狼人请睁眼并选择目标。",
        "night_guard": "守卫请行动，选择今晚守护对象。",
        "night_witch": "女巫请行动，决定是否使用解药或毒药。",
        "night_seer":  "预言家请查验一名玩家身份。",
        "day_announce": "天亮了，现在公布昨夜结果。",
        "day_discuss":  "进入白天讨论环节，请各位依次发言。",
        "day_vote":     "进入投票环节，请选择你要放逐的对象。",
        "hunter_shot":  "猎人发动技能，请选择开枪目标。",
    }

    @classmethod
    def _fallback_narration(cls, phase: str) -> str:
        return cls._PHASE_NARRATION_MAP.get(phase, "请各位继续。")

    # ------------------------------------------------------------------
    # Shared utilities (copied from GodOrchestrator for independence)
    # ------------------------------------------------------------------

    @staticmethod
    def _speech_text(result: dict, *, default: str) -> str:
        reasoning = str((result or {}).get("reasoning") or "").strip()
        if reasoning:
            return reasoning
        action = (result or {}).get("action") or {}
        if not isinstance(action, dict):
            return default
        action_type = str(action.get("type") or "").strip()
        target = action.get("target")
        save = action.get("save")
        parts: list[str] = []
        if action_type:
            parts.append(f"行动={action_type}")
        if isinstance(target, str) and target:
            parts.append(f"目标={target}")
        if isinstance(save, bool):
            parts.append(f"save={save}")
        return "；".join(parts) if parts else default

    @staticmethod
    def _split_day_discuss_texts(result: dict, *, default_speech: str) -> tuple[str, str]:
        data = result or {}
        speech = str(data.get("speech") or "").strip()
        thinking = str(data.get("thinking") or "").strip()
        reasoning = str(data.get("reasoning") or "").strip()

        if not speech and reasoning:
            speech_match = re.search(r"(?:^|\n)\s*(?:公开发言|发言|speech)\s*[:：]\s*(.+)", reasoning, flags=re.IGNORECASE)
            if speech_match:
                speech = speech_match.group(1).strip()

        if not thinking and reasoning:
            thinking_match = re.search(r"(?:^|\n)\s*(?:思考|内心|推理|thinking|reasoning)\s*[:：]\s*(.+)", reasoning, flags=re.IGNORECASE)
            if thinking_match:
                thinking = thinking_match.group(1).strip()
            elif not speech:
                private_markers = re.search(r"真实想法|仅法官|仅内部|不要公开|内心|推理过程|chain.?of.?thought", reasoning, flags=re.IGNORECASE)
                if private_markers:
                    thinking = reasoning
                else:
                    cleaned_reasoning = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", reasoning).strip()
                    if cleaned_reasoning:
                        speech = cleaned_reasoning

        if speech and re.fullmatch(r"(?:\[[^\]]+\]\s*)+", speech):
            speech = ""

        if not speech:
            speech = default_speech

        return speech, thinking

    @staticmethod
    def _sanitize_day_discuss_public_speech(text: str) -> str:
        speech = str(text or "").strip()
        if not speech:
            return speech

        target_token = ""
        target_match = re.search(r"(?:查验|验了|验出|查杀|金水)\s*(?:了)?\s*([^\s，。；,.;:：]{2,10})", speech)
        if target_match:
            target_token = str(target_match.group(1) or "").strip()
        weak_target = bool(
            not target_token
            or target_token.startswith(("他", "她", "它", "这", "那", "其", "某"))
            or "狼人" in target_token
        )
        has_explicit_target = bool(
            re.search(r"[0-9一二三四五六七八九十]+号", speech)
            or (
                target_token
                and not weak_target
            )
        )

        seer_claim_with_check = bool(
            re.search(r"(?:我是|本轮我[是为]?|我身份是)\s*预言家", speech)
            and re.search(r"昨晚|昨夜|今早|本轮|上轮", speech)
            and re.search(r"查验|验了|验出|金水|查杀", speech)
            and has_explicit_target
        )
        if seer_claim_with_check:
            return speech

        replacements = [
            ("（狼人）", "（疑似狼人）"),
            ("是狼人", "疑似狼人"),
            ("就是狼人", "疑似狼人"),
            ("为狼人", "疑似狼人"),
            ("必是狼人", "疑似狼人"),
        ]
        for src, dst in replacements:
            speech = speech.replace(src, dst)
        return speech

    @staticmethod
    def _split_day_vote_texts(result: dict, *, default_speech: str) -> tuple[str, str]:
        data = result or {}
        speech = str(data.get("speech") or "").strip()
        thinking = str(data.get("thinking") or "").strip()
        reasoning = str(data.get("reasoning") or "").strip()

        if not speech and reasoning:
            speech_match = re.search(r"(?:^|\n)\s*(?:投票发言|公开发言|发言|speech)\s*[:：]\s*(.+)", reasoning, flags=re.IGNORECASE)
            if speech_match:
                speech = speech_match.group(1).strip()

        if not thinking and reasoning:
            thinking_match = re.search(r"(?:^|\n)\s*(?:思考|内心|推理|thinking|reasoning)\s*[:：]\s*(.+)", reasoning, flags=re.IGNORECASE)
            if thinking_match:
                thinking = thinking_match.group(1).strip()
            else:
                thinking = reasoning

        if thinking and re.fullmatch(r"(?:\[[^\]]+\]\s*)+", thinking):
            thinking = ""

        if speech and re.fullmatch(r"(?:\[[^\]]+\]\s*)+", speech):
            speech = ""

        if not speech:
            speech = default_speech

        return speech, thinking

    def _vote_speech_text(self, engine: GameEngine, target_id: Optional[str], base: str) -> str:
        base = str(base or "").strip()
        if target_id:
            target_name = engine.snapshot.players[target_id].nickname or target_id
            if base and ("弃票" in base or "不投票" in base):
                base = re.sub(r"[，,。；;]?\s*我?\s*(?:选择)?\s*(?:弃票|不投票)\s*[，,。；;]?", " ", base)
                base = re.sub(r"(?:弃票|不投票)", "", base)
                base = re.sub(r"\s+", " ", base).strip(" ，,。；;")
            if base and (target_id in base or target_name in base or "投票给" in base):
                return base
            explicit = f"我投票给 {target_name}。"
        else:
            if base and ("投票给" in base or "投给" in base):
                base = re.sub(r"[，,。；;]?\s*我?\s*(?:投票给|投给|投)\s*[^，,。；;\s]+\s*[，,。；;]?", " ", base)
                base = re.sub(r"\s+", " ", base).strip(" ，,。；;")
            if base and ("弃票" in base or "不投票" in base):
                return base
            explicit = "我选择弃票。"

        if not base:
            return explicit
        return f"{explicit} {base}".strip()

    def _seer_speech_text(self, engine: GameEngine, result: dict, target_id: Optional[str]) -> str:
        base = self._speech_text(result, default="")
        if target_id:
            target_name = engine.snapshot.players[target_id].nickname or target_id
            if base and (target_id in base or target_name in base):
                return base
            explicit = f"我查验了 {target_name}。"
        else:
            if base and ("未查验" in base or "没查验" in base):
                return base
            explicit = "我本轮未完成查验。"

        if not base:
            return explicit
        return f"{explicit} {base}".strip()

    def _hunter_speech_text(self, engine: GameEngine, result: dict, target_id: Optional[str]) -> str:
        base = self._speech_text(result, default="")
        if target_id:
            target_name = engine.snapshot.players[target_id].nickname or target_id
            if base and (target_id in base or target_name in base):
                return base
            explicit = f"我开枪带走 {target_name}。"
        else:
            if base and ("不开枪" in base or "未开枪" in base):
                return base
            explicit = "我本轮不开枪。"

        if not base:
            return explicit
        return f"{explicit} {base}".strip()

    def _wolf_discuss_speech_text(self, engine: GameEngine, result: dict, target_id: Optional[str]) -> str:
        base = self._speech_text(result, default="")
        if target_id:
            target_name = engine.snapshot.players[target_id].nickname or target_id
            if base and (target_id in base or target_name in base):
                return base
            explicit = f"我建议今晚击杀 {target_name}。"
        else:
            if base and ("暂无目标" in base or "未确定目标" in base):
                return base
            explicit = "我暂未确定今晚目标。"

        if not base:
            return explicit
        return f"{explicit} {base}".strip()

    def _guard_speech_text(self, engine: GameEngine, result: dict, target_id: Optional[str]) -> str:
        base = self._speech_text(result, default="")
        if target_id:
            target_name = engine.snapshot.players[target_id].nickname or target_id
            if base and (target_id in base or target_name in base):
                return base
            explicit = f"我守护了 {target_name}。"
        else:
            if base and ("未守护" in base or "不守护" in base):
                return base
            explicit = "我本轮未守护任何人。"

        if not base:
            return explicit
        return f"{explicit} {base}".strip()

    @staticmethod
    def _consensus_target(engine: GameEngine, proposals: dict[str, str]) -> Optional[str]:
        if not proposals:
            return None

        counter: dict[str, int] = {}
        for _, target in proposals.items():
            counter[target] = counter.get(target, 0) + 1
        best_score = max(counter.values()) if counter else 0
        leaders = {target for target, score in counter.items() if score == best_score}
        for _, target in proposals.items():
            if target in leaders:
                return target
        return None

    @staticmethod
    def _fallback_wolf_target(engine: GameEngine) -> Optional[str]:
        candidates = [
            p.player_id
            for p in engine.snapshot.players.values()
            if p.alive and p.role != Role.WEREWOLF
        ]
        if not candidates:
            return None
        candidates.sort()
        round_no = getattr(engine.snapshot.round_context, "round_no", 0)
        seed = f"{engine.snapshot.room_id}|{round_no}|night_wolf_fallback"
        rng = random.Random(seed)
        return rng.choice(candidates)

    @staticmethod
    def _emit_day_announce_summary(engine: GameEngine) -> None:
        ctx = engine.snapshot.round_context
        deaths = ctx.deaths_this_round or {}
        wolf_target = ctx.wolf_target
        guard_target = ctx.night_actions.guard_target

        parts: list[str] = []
        if guard_target:
            guard_name = engine.snapshot.players[guard_target].nickname or guard_target
            parts.append(f"守卫昨夜守护了 {guard_name}。")

        if wolf_target:
            wolf_name = engine.snapshot.players[wolf_target].nickname or wolf_target
            if wolf_target in deaths:
                parts.append(f"昨夜狼人袭击了 {wolf_name}，该玩家已出局。")
            elif wolf_target in ctx.protected_players:
                parts.append(f"昨夜狼人袭击了 {wolf_name}，但被守卫成功守护。")
            elif ctx.night_actions.witch_save:
                parts.append(f"昨夜狼人袭击了 {wolf_name}，女巫使用解药救下了TA。")
            else:
                parts.append(f"昨夜狼人目标为 {wolf_name}，但未造成有效击杀。")

        poison_target = ctx.night_actions.witch_poison_target
        if poison_target:
            poison_name = engine.snapshot.players[poison_target].nickname or poison_target
            if poison_target in deaths:
                parts.append(f"女巫昨夜毒杀了 {poison_name}。")
            else:
                parts.append(f"女巫昨夜对 {poison_name} 使用了毒药（未造成新增出局）。")

        if not deaths:
            parts.append("昨夜是平安夜，无人出局。")
        else:
            death_lines: list[str] = []
            for pid, cause in deaths.items():
                name = engine.snapshot.players[pid].nickname or pid
                death_lines.append(f"{name}（{AIGodOrchestrator._death_cause_label(cause.value)}）")
            parts.append("出局名单：" + "、".join(death_lines) + "。")

        engine._audit(
            "agent_speech",
            "god",
            {
                "phase": Phase.DAY_ANNOUNCE.value,
                "role": "judge",
                "content": " ".join(parts),
                "is_fallback": False,
                "fallback_reason": None,
            },
        )

    @staticmethod
    def _normalize_target(
        engine: GameEngine,
        target: object,
        *,
        allow_self: bool = False,
        exclude_wolves: bool = False,
    ) -> Optional[str]:
        if not isinstance(target, str):
            return None
        raw = target.strip()
        if not raw:
            return None

        resolved_id: Optional[str] = None
        if raw in engine.snapshot.players:
            resolved_id = raw
        else:
            exact = [
                p.player_id
                for p in engine.snapshot.players.values()
                if (p.nickname or "").strip() == raw
            ]
            if len(exact) == 1:
                resolved_id = exact[0]
            elif len(exact) > 1:
                return None
            else:
                lowered = raw.lower()
                fuzzy = [
                    p.player_id
                    for p in engine.snapshot.players.values()
                    if (p.nickname or "").strip().lower() == lowered
                ]
                if len(fuzzy) == 1:
                    resolved_id = fuzzy[0]

        if not resolved_id:
            return None

        player = engine.snapshot.players.get(resolved_id)
        if not player or not player.alive:
            return None
        if exclude_wolves and player.role == Role.WEREWOLF:
            return None
        return resolved_id

    @staticmethod
    def _death_cause_label(cause: str) -> str:
        mapping = {
            "wolf": "被狼人袭击",
            "poison": "被女巫毒杀",
            "vote": "被投票放逐",
            "hunter": "被猎人带走",
        }
        return mapping.get(str(cause or "").lower(), str(cause or "未知原因"))
