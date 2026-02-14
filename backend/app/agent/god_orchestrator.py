from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.agent.agent_scheduler import AgentScheduler
from app.agent.fallback_strategies import FallbackStrategies
from app.agent.perspective_engine import PerspectiveEngine
from app.core.models import Phase, Role
from app.engine.game_engine import GameEngine


@dataclass(slots=True)
class PromptTemplates:
    night_wolf: str = "请返回JSON: {\"action\": {\"target\": \"player_id\"}}"
    night_guard: str = "请返回JSON: {\"action\": {\"target\": \"player_id\"}}"
    night_witch: str = "请返回JSON: {\"action\": {\"target\": \"player_id\", \"save\": true|false}}"
    night_seer: str = "请返回JSON: {\"action\": {\"target\": \"player_id\"}}"
    day_discuss: str = "作为[角色]，请分析局势，怀疑对象及理由（100字内），返回JSON action+reasoning"
    day_vote: str = "请返回JSON: {\"action\": {\"target\": \"player_id\"}}"
    hunter_shot: str = "请返回JSON: {\"action\": {\"type\": \"shoot\", \"target\": \"player_id\"}}"


class GodOrchestrator:
    """Only trusted source for all game state transitions in AI-vs-AI mode."""

    def __init__(
        self,
        scheduler: Optional[AgentScheduler] = None,
        perspective_engine: Optional[PerspectiveEngine] = None,
        fallback: Optional[FallbackStrategies] = None,
    ) -> None:
        self.scheduler = scheduler or AgentScheduler(fallback=fallback)
        self.perspective = perspective_engine or PerspectiveEngine()
        self.templates = PromptTemplates()

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
        phase = engine.snapshot.phase
        if phase == Phase.NIGHT_WOLF:
            await self._run_wolf_phase(engine)
            engine.advance_phase()
            return

        if phase == Phase.NIGHT_GUARD:
            await self._run_guard_phase(engine)
            engine.advance_phase()
            return

        if phase == Phase.NIGHT_WITCH:
            await self._run_witch_phase(engine)
            engine.advance_phase()
            return

        if phase == Phase.NIGHT_SEER:
            await self._run_seer_phase(engine)
            engine.advance_phase()
            return

        if phase == Phase.DAY_ANNOUNCE:
            engine.advance_phase()
            return

        if phase == Phase.DAY_DISCUSS:
            await self._run_day_discuss(engine)
            engine.advance_phase()
            return

        if phase == Phase.DAY_VOTE:
            await self._run_day_vote(engine)
            engine.advance_phase()
            await self._run_hunter_if_needed(engine)
            return

    async def _run_wolf_phase(self, engine: GameEngine) -> None:
        wolves = [p for p in engine.snapshot.players.values() if p.alive and p.role == Role.WEREWOLF]
        for wolf in wolves:
            visible = self.perspective.build_visible_state(engine, wolf.player_id)
            result = await self.scheduler.trigger_agent_action(
                player_id=wolf.player_id,
                session_id=engine.snapshot.room_id,
                role=Role.WEREWOLF.value,
                phase=Phase.NIGHT_WOLF.value,
                visible_state=visible,
                prompt_template=self.templates.night_wolf,
                strategy_name="night_wolf",
            )
            target = self._normalize_target(engine, result.get("action", {}).get("target"), exclude_wolves=True)
            if target:
                try:
                    engine.submit_night_action(wolf.player_id, target)
                except ValueError:
                    continue

    async def _run_guard_phase(self, engine: GameEngine) -> None:
        guard = next((p for p in engine.snapshot.players.values() if p.alive and p.role == Role.GUARD), None)
        if not guard:
            return
        visible = self.perspective.build_visible_state(engine, guard.player_id)
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
        if target:
            try:
                engine.submit_night_action(guard.player_id, target)
            except ValueError:
                return

    async def _run_witch_phase(self, engine: GameEngine) -> None:
        witch = next((p for p in engine.snapshot.players.values() if p.alive and p.role == Role.WITCH), None)
        if not witch:
            return
        visible = self.perspective.build_visible_state(engine, witch.player_id)
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
        target = self._normalize_target(engine, action.get("target"), allow_self=True)
        save = bool(action.get("save", False))
        try:
            engine.submit_night_action(witch.player_id, target, save=save)
        except ValueError:
            return

    async def _run_seer_phase(self, engine: GameEngine) -> None:
        seer = next((p for p in engine.snapshot.players.values() if p.alive and p.role == Role.SEER), None)
        if not seer:
            return
        visible = self.perspective.build_visible_state(engine, seer.player_id)
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
        if target:
            try:
                engine.submit_night_action(seer.player_id, target)
            except ValueError:
                return

    async def _run_day_discuss(self, engine: GameEngine) -> None:
        alive_players = [p for p in engine.snapshot.players.values() if p.alive]
        for player in alive_players:
            visible = self.perspective.build_visible_state(engine, player.player_id)
            await self.scheduler.trigger_agent_action(
                player_id=player.player_id,
                session_id=engine.snapshot.room_id,
                role=player.role.value,
                phase=Phase.DAY_DISCUSS.value,
                visible_state=visible,
                prompt_template=self.templates.day_discuss,
                strategy_name="day_discuss",
            )

    async def _run_day_vote(self, engine: GameEngine) -> None:
        alive_players = [p for p in engine.snapshot.players.values() if p.alive]
        for player in alive_players:
            visible = self.perspective.build_visible_state(engine, player.player_id)
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
                continue

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
        visible = self.perspective.build_visible_state(engine, hunter.player_id)
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
        if target:
            try:
                engine.submit_hunter_shot(hunter.player_id, target)
            except ValueError:
                return

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
        player = engine.snapshot.players.get(target)
        if not player or not player.alive:
            return None
        if exclude_wolves and player.role == Role.WEREWOLF:
            return None
        return target
