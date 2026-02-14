from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict

import httpx
from fastapi import FastAPI
from pydantic import BaseModel


class ActRequest(BaseModel):
    session_id: str
    player_id: str
    role: str
    phase: str
    visible_state: Dict[str, Any]
    prompt_template: str
    agent_config: Dict[str, Any] = {}


class ActResponse(BaseModel):
    action: Dict[str, Any]
    reasoning: str = ""
    timestamp: int


app = FastAPI(title="Cat Agent Template", version="0.1.0")

MODEL_TYPE = os.getenv("MODEL_TYPE", "cat-agent")
DEFAULT_API_TIMEOUT_SEC = int(os.getenv("CAT_AGENT_API_TIMEOUT", "30"))


def _mock_action(req: ActRequest) -> Dict[str, Any]:
    alive = req.visible_state.get("alive_players", [])
    me = req.player_id
    candidates = [pid for pid in alive if pid != me]

    target = candidates[0] if candidates else None
    action_type = "vote"

    if req.phase == "night_wolf":
        action_type = "kill"
    elif req.phase == "night_guard":
        action_type = "guard"
    elif req.phase == "night_witch":
        action_type = "save"
        wolf_target = req.visible_state.get("wolf_target")
        target = wolf_target
    elif req.phase == "night_seer":
        action_type = "check"
    elif req.phase == "hunter_shot":
        action_type = "shoot"
    elif req.phase == "day_discuss":
        action_type = "speak"
        target = None

    return {
        "action": {
            "type": action_type,
            "target": target,
            "save": req.phase == "night_witch" and bool(target),
        },
        "reasoning": (
            f"[{MODEL_TYPE}] phase={req.phase} "
            f"role={req.role} target={target}"
        ),
    }


def _parse_json_from_text(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise RuntimeError("模型未返回文本")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:  # noqa: BLE001
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        parsed = json.loads(fenced.group(1))
        if isinstance(parsed, dict):
            return parsed

    brace = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace:
        parsed = json.loads(brace.group(0))
        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError("无法从模型输出解析JSON")


def _run_cli_action(req: ActRequest, cfg: Dict[str, Any]) -> Dict[str, Any]:
    cli_cmd = str(cfg.get("cli_command") or "").strip()
    if not cli_cmd:
        raise RuntimeError("cli_command 未传入")

    cli_timeout_sec = int(cfg.get("cli_timeout_sec") or 20)

    payload = {
        "session_id": req.session_id,
        "player_id": req.player_id,
        "role": req.role,
        "phase": req.phase,
        "visible_state": req.visible_state,
        "prompt_template": req.prompt_template,
        "model_type": MODEL_TYPE,
        "agent_config": cfg,
    }

    proc = subprocess.run(
        cli_cmd,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=cli_timeout_sec,
        shell=True,
        check=False,
    )

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"CLI 返回非0退出码: {proc.returncode}; {stderr}")

    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("CLI 未返回内容")

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("CLI 输出必须是 JSON 对象")
    if not isinstance(data.get("action"), dict):
        raise RuntimeError("CLI 输出缺少 action 对象")

    reasoning = data.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    return {
        "action": data["action"],
        "reasoning": reasoning,
    }


def _detect_provider(cfg: Dict[str, Any]) -> str:
    """Detect the API provider from agent_config fields."""
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider:
        return provider
    api_url = str(cfg.get("api_url") or "").strip().lower()
    if "anthropic" in api_url:
        return "claude"
    if "siliconflow" in api_url:
        return "siliconflow"
    if "bigmodel.cn" in api_url:
        return "glm"
    return "openai"


def _build_prompt(req: ActRequest) -> str:
    return (
        "你是狼人杀子Agent。请只返回JSON，不要输出多余文本。"
        "格式: {\"action\": {\"type\": "
        "\"vote|kill|guard|save|check|shoot|speak\", "
        "\"target\": \"player_id或null\", \"save\": true或false}, "
        "\"reasoning\": \"简短说明\"}.\n"
        f"phase={req.phase}, role={req.role}.\n"
        f"rules_prompt={req.prompt_template}\n"
        f"visible_state={json.dumps(req.visible_state, ensure_ascii=False)}"
    )


def _call_openai_compatible(api_url: str, api_key: str, model_name: str,
                            prompt: str, timeout_sec: int) -> str:
    """Call OpenAI-compatible API (OpenAI / GLM / SiliconFlow)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是狼人杀策略决策助手。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    with httpx.Client(timeout=timeout_sec) as client:
        resp = client.post(api_url, headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "")
    return ""


def _call_claude(api_url: str, api_key: str, model_name: str,
                 prompt: str, timeout_sec: int) -> str:
    """Call Anthropic Claude Messages API."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": model_name,
        "max_tokens": 800,
        "system": "你是狼人杀策略决策助手。",
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    with httpx.Client(timeout=timeout_sec) as client:
        resp = client.post(api_url, headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()
    content_blocks = data.get("content")
    if isinstance(content_blocks, list):
        texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        return "\n".join(texts)
    return ""


def _run_api_action(req: ActRequest, cfg: Dict[str, Any]) -> Dict[str, Any]:
    api_url = str(cfg.get("api_url") or "").strip()
    api_key = str(cfg.get("api_key") or "").strip()
    model_name = str(cfg.get("model_name") or MODEL_TYPE).strip()
    timeout_sec = int(cfg.get("api_timeout_sec") or DEFAULT_API_TIMEOUT_SEC)

    if not api_url or not api_key:
        raise RuntimeError("api_url/api_key 未传入")

    provider = _detect_provider(cfg)
    prompt = _build_prompt(req)

    if provider == "claude":
        content = _call_claude(api_url, api_key, model_name, prompt, timeout_sec)
    else:
        content = _call_openai_compatible(api_url, api_key, model_name, prompt, timeout_sec)

    parsed = _parse_json_from_text(content)
    action = parsed.get("action")
    if not isinstance(action, dict):
        raise RuntimeError("模型输出缺少 action 对象")
    reasoning = parsed.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = str(reasoning)

    return {
        "action": action,
        "reasoning": reasoning,
    }


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_type": MODEL_TYPE,
        "invoke_strategy": "api-default-cli-optional",
    }


@app.post("/act", response_model=ActResponse)
def act(req: ActRequest) -> ActResponse:
    cfg = req.agent_config or {}
    cli_command = str(cfg.get("cli_command") or "").strip()

    if cli_command:
        try:
            decision = _run_cli_action(req, cfg)
            action = decision["action"]
            reasoning = (
                f"[cli:{MODEL_TYPE}] "
                f"{decision.get('reasoning', '')}"
            ).strip()
        except Exception as exc:  # noqa: BLE001
            fallback = _mock_action(req)
            action = fallback["action"]
            reasoning = (
                f"[cli-error:{MODEL_TYPE}] {exc}; "
                f"fallback={fallback.get('reasoning', '')}"
            )
    else:
        try:
            decision = _run_api_action(req, cfg)
            action = decision["action"]
            reasoning = (
                f"[api:{MODEL_TYPE}] "
                f"{decision.get('reasoning', '')}"
            ).strip()
        except Exception as exc:  # noqa: BLE001
            fallback = _mock_action(req)
            action = fallback["action"]
            reasoning = (
                f"[api-error:{MODEL_TYPE}] {exc}; "
                f"fallback={fallback.get('reasoning', '')}"
            )

    return ActResponse(
        action=action,
        reasoning=reasoning,
        timestamp=int(time.time()),
    )
