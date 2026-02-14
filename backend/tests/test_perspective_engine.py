from app.agent.perspective_engine import PerspectiveEngine
from app.core.models import Phase, Role
from app.engine.game_engine import GameEngine


def _started_engine() -> GameEngine:
    engine = GameEngine(room_id="r_p", owner_id="cat_01")
    for i in range(1, 13):
        pid = f"cat_{i:02d}"
        engine.add_player(pid, pid)
    engine.start_game("cat_01")

    role_map = {
        "cat_01": Role.WEREWOLF,
        "cat_02": Role.WEREWOLF,
        "cat_03": Role.WEREWOLF,
        "cat_04": Role.WEREWOLF,
        "cat_05": Role.GUARD,
        "cat_06": Role.WITCH,
        "cat_07": Role.SEER,
        "cat_08": Role.HUNTER,
        "cat_09": Role.VILLAGER,
        "cat_10": Role.VILLAGER,
        "cat_11": Role.VILLAGER,
        "cat_12": Role.VILLAGER,
    }
    for pid, role in role_map.items():
        engine.snapshot.players[pid].role = role
    return engine


def test_werewolf_can_see_wolf_team() -> None:
    engine = _started_engine()
    visible = PerspectiveEngine.build_visible_state(engine, "cat_01")
    assert "wolf_team" in visible
    assert set(visible["wolf_team"]) == {"cat_01", "cat_02", "cat_03", "cat_04"}


def test_villager_cannot_see_wolf_team() -> None:
    engine = _started_engine()
    visible = PerspectiveEngine.build_visible_state(engine, "cat_09")
    assert "wolf_team" not in visible


def test_witch_only_gets_wolf_target_in_witch_phase() -> None:
    engine = _started_engine()

    # wolf phase action
    engine.submit_night_action("cat_01", "cat_09")
    engine.submit_night_action("cat_02", "cat_09")
    engine.submit_night_action("cat_03", "cat_09")
    engine.submit_night_action("cat_04", "cat_09")
    engine.advance_phase()  # guard
    engine.advance_phase()  # witch

    assert engine.snapshot.phase == Phase.NIGHT_WITCH
    visible = PerspectiveEngine.build_visible_state(engine, "cat_06")
    assert visible["wolf_target"] == "cat_09"

    engine.advance_phase()  # seer
    visible2 = PerspectiveEngine.build_visible_state(engine, "cat_06")
    assert "wolf_target" not in visible2


def test_seer_can_read_own_result() -> None:
    engine = _started_engine()
    engine.submit_night_action("cat_01", "cat_09")
    engine.submit_night_action("cat_02", "cat_09")
    engine.submit_night_action("cat_03", "cat_09")
    engine.submit_night_action("cat_04", "cat_09")
    engine.advance_phase()  # guard
    engine.submit_night_action("cat_05", "cat_10")
    engine.advance_phase()  # witch
    engine.advance_phase()  # seer
    engine.submit_night_action("cat_07", "cat_01")

    visible = PerspectiveEngine.build_visible_state(engine, "cat_07")
    assert visible["seer_result"]["target_id"] == "cat_01"
    assert visible["seer_result"]["is_wolf"] is True


def test_public_vote_log_contains_only_vote_events() -> None:
    engine = _started_engine()
    engine.snapshot.phase = Phase.DAY_VOTE
    engine.submit_vote("cat_01", "cat_09")
    engine.submit_vote("cat_02", "cat_09")

    visible = PerspectiveEngine.build_visible_state(engine, "cat_09")
    assert "public_vote_log" in visible
    assert len(visible["public_vote_log"]) >= 2
    assert all(item["event_type"] in {"vote", "vote_result"} for item in visible["public_vote_log"])
