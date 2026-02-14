from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

import httpx

from app.agent.fallback_strategies import FallbackStrategies


@dataclass(slots=True)
class AgentRegistration:
    player_id: str
    ipc_endpoint: str
    model_type: str
    timeout_sec: int = 15
    online: bool = True
    failed_count: int = 0
    entrusted: bool = False
    registered_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class SchedulerMetrics:
    total_calls: int = 0
    timeout_calls: int = 0
    error_calls: int = 0
    total_latency_ms: float = 0.0
    by_error_type: Dict[str, int] = field(default_factory=dict)

    def observe_success(self, latency_ms: float) -> None:
        self.total_calls += 1
        self.total_latency_ms += latency_ms

    def observe_timeout(self) -> None:
        self.total_calls += 1
        self.timeout_calls += 1
        self.by_error_type["timeout"] = self.by_error_type.get("timeout", 0) + 1

    def observe_error(self, error_name: str) -> None:
        self.total_calls += 1
        self.error_calls += 1
        self.by_error_type[error_name] = self.by_error_type.get(error_name, 0) + 1

    def to_dict(self) -> dict:
        avg = 0.0 if self.total_calls == 0 else self.total_latency_ms / self.total_calls
        timeout_rate = 0.0 if self.total_calls == 0 else self.timeout_calls / self.total_calls
        return {
            "total_calls": self.total_calls,
            "timeout_calls": self.timeout_calls,
            "error_calls": self.error_calls,
            "avg_latency_ms": round(avg, 2),
            "timeout_rate": round(timeout_rate, 4),
            "by_error_type": self.by_error_type,
        }


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: Dict[str, AgentRegistration] = {}

    def register(self, player_id: str, ipc_endpoint: str, model_type: str, timeout_sec: int = 15) -> AgentRegistration:
        reg = AgentRegistration(
            player_id=player_id,
            ipc_endpoint=ipc_endpoint.rstrip("/"),
            model_type=model_type,
            timeout_sec=timeout_sec,
            online=True,
            failed_count=0,
            entrusted=False,
        )
        self._agents[player_id] = reg
        return reg

    def get(self, player_id: str) -> Optional[AgentRegistration]:
        return self._agents.get(player_id)

    def all(self) -> Dict[str, AgentRegistration]:
        return self._agents

    def mark_offline(self, player_id: str) -> None:
        agent = self._agents.get(player_id)
        if not agent:
            return
        agent.online = False
        agent.entrusted = True


class AgentScheduler:
    """God-agent scheduling core with timeout/circuit-breaker/fallback."""

    def __init__(self, fallback: Optional[FallbackStrategies] = None, debug_mode: bool = False) -> None:
        self.registry = AgentRegistry()
        self.fallback = fallback or FallbackStrategies()
        self.metrics = SchedulerMetrics()
        self.audit_logs: list[dict] = []
        self.debug_mode = debug_mode

    async def trigger_agent_action(
        self,
        *,
        player_id: str,
        session_id: str,
        role: str,
        phase: str,
        visible_state: dict,
        prompt_template: str,
        strategy_name: str,
    ) -> dict:
        agent = self.registry.get(player_id)
        if not agent or not agent.online:
            return self._fallback_action(strategy_name, visible_state, "offline")

        payload = {
            "session_id": session_id,
            "player_id": player_id,
            "role": role,
            "phase": phase,
            "visible_state": visible_state,
            "prompt_template": prompt_template,
        }

        if self.debug_mode:
            self.audit_logs.append(
                {
                    "event": "god_visible_state",
                    "player_id": player_id,
                    "phase": phase,
                    "visible_state": visible_state,
                    "ts": datetime.utcnow().isoformat(),
                }
            )

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=agent.timeout_sec) as client:
                resp = await client.post(f"{agent.ipc_endpoint}/act", json=payload)
            latency_ms = (time.perf_counter() - start) * 1000

            if resp.status_code >= 400:
                self.metrics.observe_error(f"http_{resp.status_code}")
                return self._handle_failure(agent, strategy_name, visible_state, f"http_{resp.status_code}")

            data = self._validate_and_parse(resp.json())
            self.metrics.observe_success(latency_ms)
            agent.failed_count = 0

            self.audit_logs.append(
                {
                    "event": "agent_response",
                    "player_id": player_id,
                    "phase": phase,
                    "model_type": agent.model_type,
                    "latency_ms": round(latency_ms, 2),
                    "request": self._desensitize_payload(payload),
                    "response": self._desensitize_payload(data),
                    "ts": datetime.utcnow().isoformat(),
                }
            )
            return data
        except httpx.TimeoutException:
            self.metrics.observe_timeout()
            return self._handle_failure(agent, strategy_name, visible_state, "timeout")
        except Exception as exc:  # noqa: BLE001
            self.metrics.observe_error(type(exc).__name__)
            return self._handle_failure(agent, strategy_name, visible_state, type(exc).__name__)

    def _handle_failure(self, agent: AgentRegistration, strategy_name: str, context: dict, reason: str) -> dict:
        agent.failed_count += 1
        if agent.failed_count >= 2:
            agent.online = False
            agent.entrusted = True
        return self._fallback_action(strategy_name, context, reason)

    def _fallback_action(self, strategy_name: str, context: dict, reason: str) -> dict:
        action = self.fallback.get_action(strategy_name, context)
        action["fallback_reason"] = reason
        self.audit_logs.append(
            {
                "event": "fallback_action",
                "strategy": strategy_name,
                "reason": reason,
                "response": self._desensitize_payload(action),
                "ts": datetime.utcnow().isoformat(),
            }
        )
        return action

    @staticmethod
    def _validate_and_parse(data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("invalid response format")
        action = data.get("action")
        if not isinstance(action, dict):
            raise ValueError("missing action")
        target = action.get("target")
        if target is not None and not isinstance(target, str):
            raise ValueError("action.target must be string or null")

        reasoning = data.get("reasoning", "")
        if isinstance(reasoning, str) and len(reasoning) > 300:
            data["reasoning"] = reasoning[:300]
        if isinstance(data.get("reasoning"), str):
            data["reasoning"] = AgentScheduler._sanitize_text(data["reasoning"])
        return data

    @staticmethod
    def _sanitize_text(text: str) -> str:
        banned = ["api_key", "password", "secret", "token"]
        cleaned = text
        for word in banned:
            cleaned = cleaned.replace(word, "***")
            cleaned = cleaned.replace(word.upper(), "***")
        return cleaned[:300]

    @staticmethod
    def _desensitize_payload(payload: dict) -> dict:
        text = str(payload)
        if len(text) > 1000:
            return {"truncated": text[:1000] + "..."}
        return payload
