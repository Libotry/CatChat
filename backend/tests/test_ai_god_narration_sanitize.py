from app.agent.ai_god_orchestrator import AIGodOrchestrator


def test_day_vote_narration_with_result_claim_is_sanitized() -> None:
    text = "投票时间结束，现在公布投票结果：蓝猫获得3票，被放逐。"
    sanitized = AIGodOrchestrator._sanitize_public_narration("day_vote", text)
    assert sanitized == "进入投票环节，请每位存活玩家明确给出放逐目标。"


def test_non_day_vote_narration_keeps_original() -> None:
    text = "天亮了，现在公布昨夜结果。"
    sanitized = AIGodOrchestrator._sanitize_public_narration("day_announce", text)
    assert sanitized == text


def test_day_discuss_narration_with_night_settlement_claim_keeps_original() -> None:
    text = "天亮了！昨夜是个平安夜，没有人死亡。请开始讨论。"
    sanitized = AIGodOrchestrator._sanitize_public_narration("day_discuss", text)
    assert sanitized == text


def test_day_vote_narration_with_night_settlement_claim_is_sanitized() -> None:
    text = "昨夜出局名单已公布，现在开始投票。"
    sanitized = AIGodOrchestrator._sanitize_public_narration("day_vote", text)
    assert sanitized == "进入投票环节，请每位存活玩家明确给出放逐目标。"
