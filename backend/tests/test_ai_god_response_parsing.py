from app.agent.ai_god_orchestrator import AIGodOrchestrator


def test_extract_openai_compatible_text_from_standard_json() -> None:
    raw = '{"choices":[{"message":{"content":"{\\"narration\\":\\"天亮了\\"}"}}]}'
    text = AIGodOrchestrator._extract_openai_compatible_text(raw)
    assert "narration" in text


def test_extract_openai_compatible_text_from_sse_chunks() -> None:
    raw = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"{\\"narration\\":\\""}}]}',
            'data: {"choices":[{"delta":{"content":"开始讨论\\"}"}}]}',
            "data: [DONE]",
        ]
    )
    text = AIGodOrchestrator._extract_openai_compatible_text(raw)
    assert "narration" in text


def test_extract_openai_compatible_text_empty_raises() -> None:
    try:
        AIGodOrchestrator._extract_openai_compatible_text("")
        assert False, "should raise"
    except RuntimeError as exc:
        assert "empty" in str(exc)


def test_extract_openai_compatible_text_from_reasoning_content() -> None:
    raw = '{"choices":[{"message":{"content":"","reasoning_content":"{\\"narration\\":\\"进入讨论\\"}"}}]}'
    text = AIGodOrchestrator._extract_openai_compatible_text(raw)
    assert "narration" in text


def test_extract_openai_compatible_text_from_direct_god_json() -> None:
    raw = '{"narration":"天亮了","reasoning":"ok","rulings":{}}'
    text = AIGodOrchestrator._extract_openai_compatible_text(raw)
    assert "天亮了" in text


def test_extract_claude_text_from_content_blocks() -> None:
    raw = '{"content":[{"type":"text","text":"{\\"narration\\":\\"公布结果\\"}"}]}'
    text = AIGodOrchestrator._extract_claude_text(raw)
    assert "narration" in text


def test_coerce_public_narration_from_raw_json_text() -> None:
    raw = '{"narration":"女巫请睁眼并决定是否使用药剂。","phase_instructions":"请行动","rulings":{}}'
    text = AIGodOrchestrator._coerce_public_narration("night_witch", raw)
    assert text == "女巫请睁眼并决定是否使用药剂。"


def test_coerce_public_narration_from_nested_json_string() -> None:
    raw = '"{\\"narration\\":\\"天亮了，请公布昨夜结果。\\",\\"rulings\\":{}}"'
    text = AIGodOrchestrator._coerce_public_narration("day_announce", raw)
    assert "天亮了" in text
