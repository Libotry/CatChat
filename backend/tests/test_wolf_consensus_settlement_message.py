from app.agent.ai_god_orchestrator import AIGodOrchestrator
from app.agent.god_orchestrator import GodOrchestrator
from app.core.models import Role
from app.engine.game_engine import GameEngine


def _make_engine() -> GameEngine:
    engine = GameEngine(room_id="r_wolf", owner_id="owner")
    players = {
        "owner": "折耳",
        "p2": "大橘",
        "p3": "布偶",
        "p4": "蓝猫",
        "p5": "狸花",
        "p6": "孟加拉",
        "p7": "波斯",
        "p8": "退罗",
        "p9": "缅因",
        "p10": "暹罗",
        "p11": "英短",
        "p12": "苏格兰",
    }
    for pid, nick in players.items():
        engine.add_player(pid, nick)
    engine.start_game("owner")

    role_map = {
        "owner": Role.WEREWOLF,
        "p2": Role.WEREWOLF,
        "p3": Role.VILLAGER,
        "p4": Role.VILLAGER,
        "p5": Role.GUARD,
        "p6": Role.WITCH,
        "p7": Role.SEER,
        "p8": Role.VILLAGER,
        "p9": Role.VILLAGER,
        "p10": Role.VILLAGER,
        "p11": Role.VILLAGER,
        "p12": Role.VILLAGER,
    }
    for pid, role in role_map.items():
        engine.snapshot.players[pid].role = role
    return engine


def test_consensus_target_no_proposals_returns_none() -> None:
    engine = _make_engine()
    assert GodOrchestrator._consensus_target(engine, {}) is None
    assert AIGodOrchestrator._consensus_target(engine, {}) is None


def test_fallback_wolf_target_returns_non_wolf_alive() -> None:
    engine = _make_engine()
    fallback_1 = GodOrchestrator._fallback_wolf_target(engine)
    fallback_2 = AIGodOrchestrator._fallback_wolf_target(engine)

    assert fallback_1 is not None
    assert fallback_2 is not None
    assert engine.snapshot.players[fallback_1].role != Role.WEREWOLF
    assert engine.snapshot.players[fallback_2].role != Role.WEREWOLF
