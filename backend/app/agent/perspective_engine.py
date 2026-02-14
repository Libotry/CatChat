from __future__ import annotations

from typing import Any, Dict, List

from app.core.models import Phase, Role
from app.engine.game_engine import GameEngine


class PerspectiveEngine:
    """Build role-scoped view of game state for each cat agent."""

    @staticmethod
    def build_visible_state(engine: GameEngine, player_id: str) -> Dict[str, Any]:
        snapshot = engine.snapshot
        player = snapshot.players[player_id]
        role = player.role
        ctx = snapshot.round_context

        alive_players: List[str] = [p.player_id for p in snapshot.players.values() if p.alive]
        dead_players: List[str] = [p.player_id for p in snapshot.players.values() if not p.alive]

        base: Dict[str, Any] = {
            "room_id": snapshot.room_id,
            "round_no": ctx.round_no,
            "phase": snapshot.phase.value,
            "player_id": player_id,
            "alive_players": alive_players,
            "dead_players": dead_players,
            "public_deaths_this_round": {
                pid: cause.value for pid, cause in ctx.deaths_this_round.items()
            },
            "public_vote_log": PerspectiveEngine._extract_public_votes(snapshot.action_audit_log),
            "your_identity_hint": PerspectiveEngine._identity_hint(role),
            "role_capability": {
                "can_use_antidote": role == Role.WITCH and not player.used_antidote,
                "can_use_poison": role == Role.WITCH and not player.used_poison,
                "can_hunter_shoot": role == Role.HUNTER and player.can_hunter_shoot,
            },
        }

        if role == Role.WEREWOLF:
            base["wolf_team"] = [
                p.player_id
                for p in snapshot.players.values()
                if p.alive and p.role == Role.WEREWOLF
            ]

        if role == Role.WITCH and snapshot.phase == Phase.NIGHT_WITCH:
            base["wolf_target"] = ctx.wolf_target

        if role == Role.SEER:
            base["seer_result"] = engine.get_seer_result(player_id)

        if role == Role.GUARD:
            base["last_guard_target"] = player.last_guard_target

        return base

    @staticmethod
    def _extract_public_votes(audit_log: list[dict]) -> list[dict]:
        result: list[dict] = []
        for row in audit_log:
            if row.get("event_type") not in {"vote", "vote_result"}:
                continue
            payload = row.get("payload", {})
            result.append(
                {
                    "event_type": row.get("event_type"),
                    "actor_id": row.get("actor_id"),
                    "target": payload.get("target") if isinstance(payload, dict) else None,
                    "ts": row.get("ts"),
                }
            )
        return result[-50:]

    @staticmethod
    def _identity_hint(role: Role | None) -> str:
        mapping = {
            Role.WEREWOLF: "你每晚可与狼队友协同刀人，白天需隐藏身份。",
            Role.VILLAGER: "你是普通村民，依靠发言与投票找狼。",
            Role.SEER: "你每晚可查验一人身份（是否狼人）。",
            Role.WITCH: "你有一瓶解药和一瓶毒药，各只能使用一次。",
            Role.HUNTER: "你被狼杀或放逐时可开枪带走一人（被毒杀不可开枪）。",
            Role.GUARD: "你每晚可守护一人，不能连续两晚守同一人。",
            Role.FOOL: "你被白天放逐时会翻牌免死一次，但之后失去投票权。",
        }
        return mapping.get(role, "保持理性分析并遵守规则。")
