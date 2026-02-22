from app.agent.fallback_strategies import FallbackStrategies


def test_day_vote_fallback_emits_speech_and_thinking() -> None:
    strategies = FallbackStrategies()
    result = strategies.get_action(
        "day_vote",
        {
            "player_id": "p1",
            "alive_player_ids": ["p1", "p2", "p3"],
        },
    )

    assert isinstance(result.get("speech"), str)
    assert result.get("speech", "").strip()
    assert isinstance(result.get("thinking"), str)
    assert result.get("thinking", "").strip()


def test_day_vote_template_backfills_speech_and_thinking() -> None:
    strategies = FallbackStrategies()
    strategies._templates["day_vote"] = {
        "action": {"type": "vote", "target": "p2"},
        "reasoning": "我倾向先跟票压制高嫌疑位。",
    }

    result = strategies.get_action(
        "day_vote",
        {
            "player_id": "p1",
            "alive_player_ids": ["p1", "p2", "p3"],
        },
    )

    assert result.get("speech", "").strip()
    assert result.get("thinking", "").strip()
