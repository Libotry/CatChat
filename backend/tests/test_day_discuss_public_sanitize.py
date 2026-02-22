from app.agent.ai_god_orchestrator import AIGodOrchestrator
from app.agent.god_orchestrator import GodOrchestrator


def test_day_discuss_public_speech_sanitizes_definitive_wolf_claims() -> None:
    raw = "上一轮投票里，蓝猫（狼人）跳得太高，他就是狼人。"

    s1 = GodOrchestrator._sanitize_day_discuss_public_speech(raw)
    s2 = AIGodOrchestrator._sanitize_day_discuss_public_speech(raw)

    assert "（疑似狼人）" in s1
    assert "就是狼人" not in s1
    assert "（疑似狼人）" in s2
    assert "就是狼人" not in s2


def test_day_discuss_public_speech_keeps_definitive_claim_for_seer_check_result() -> None:
    raw = "昨晚我查验了折耳，他就是狼人。我是预言家，信息可靠。"

    s1 = GodOrchestrator._sanitize_day_discuss_public_speech(raw)
    s2 = AIGodOrchestrator._sanitize_day_discuss_public_speech(raw)

    assert "就是狼人" in s1
    assert "疑似狼人" not in s1
    assert "就是狼人" in s2
    assert "疑似狼人" not in s2


def test_day_discuss_public_speech_still_sanitizes_without_explicit_target() -> None:
    raw = "昨晚我查验了，他就是狼人。我是预言家，信息可靠。"

    s1 = GodOrchestrator._sanitize_day_discuss_public_speech(raw)
    s2 = AIGodOrchestrator._sanitize_day_discuss_public_speech(raw)

    assert "就是狼人" not in s1
    assert "疑似狼人" in s1
    assert "就是狼人" not in s2
    assert "疑似狼人" in s2


def test_day_discuss_public_speech_still_sanitizes_without_round_time_hint() -> None:
    raw = "我查验了折耳，他就是狼人。我是预言家，信息可靠。"

    s1 = GodOrchestrator._sanitize_day_discuss_public_speech(raw)
    s2 = AIGodOrchestrator._sanitize_day_discuss_public_speech(raw)

    assert "就是狼人" not in s1
    assert "疑似狼人" in s1
    assert "就是狼人" not in s2
    assert "疑似狼人" in s2
