from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.core.models import Role


class FallbackStrategies:
    """Fallback strategy plugin registry.

    Supports dynamic loading from JSON file and built-in defaults.
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, Callable[[dict], dict]] = {
            "default": self._default_action,
            "night_wolf": self._night_wolf_action,
            "night_guard": self._night_guard_action,
            "night_witch": self._night_witch_action,
            "night_seer": self._night_seer_action,
            "day_vote": self._day_vote_action,
            "day_discuss": self._day_discuss_action,
            "hunter_shot": self._hunter_shot_action,
        }
        self._templates: Dict[str, dict] = {}

    def load_templates(self, file_path: str) -> None:
        path = Path(file_path)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            self._templates = data

    def get_action(self, strategy_name: str, context: dict) -> dict:
        if strategy_name in self._templates:
            return self._render_template(self._templates[strategy_name], context)
        fn = self._strategies.get(strategy_name, self._strategies["default"])
        return fn(context)

    def _render_template(self, template: dict, context: dict) -> dict:
        result = dict(template)
        alive = context.get("alive_players", [])
        if result.get("action", {}).get("target") == "__RANDOM_ALIVE__" and alive:
            result["action"]["target"] = random.choice(alive)
        return result

    @staticmethod
    def _default_action(context: dict) -> dict:
        return {
            "action": {"type": "skip", "target": None},
            "reasoning": "fallback/default",
        }

    @staticmethod
    def _night_wolf_action(context: dict) -> dict:
        alive = context.get("alive_players", [])
        non_wolf = [pid for pid in alive if pid not in context.get("wolf_team", [])]
        target = random.choice(non_wolf) if non_wolf else None
        return {
            "action": {"type": "kill", "target": target},
            "reasoning": "fallback/wolf_random",
        }

    @staticmethod
    def _night_guard_action(context: dict) -> dict:
        alive = context.get("alive_players", [])
        last_guard_target = context.get("last_guard_target")
        candidates = [pid for pid in alive if pid != last_guard_target] or alive
        target = random.choice(candidates) if candidates else None
        return {
            "action": {"type": "guard", "target": target},
            "reasoning": "fallback/guard_random",
        }

    @staticmethod
    def _night_witch_action(context: dict) -> dict:
        wolf_target = context.get("wolf_target")
        role_capability = context.get("role_capability", {})
        if wolf_target and role_capability.get("can_use_antidote", False):
            return {
                "action": {"type": "save", "target": wolf_target, "save": True},
                "reasoning": "fallback/witch_save",
            }
        alive = context.get("alive_players", [])
        poison_target = random.choice(alive) if alive and role_capability.get("can_use_poison", False) else None
        return {
            "action": {"type": "poison", "target": poison_target, "save": False},
            "reasoning": "fallback/witch_skip_or_poison",
        }

    @staticmethod
    def _night_seer_action(context: dict) -> dict:
        alive = context.get("alive_players", [])
        me = context.get("player_id")
        candidates = [pid for pid in alive if pid != me]
        target = random.choice(candidates) if candidates else None
        return {
            "action": {"type": "check", "target": target},
            "reasoning": "fallback/seer_random",
        }

    @staticmethod
    def _day_vote_action(context: dict) -> dict:
        alive = context.get("alive_players", [])
        me = context.get("player_id")
        candidates = [pid for pid in alive if pid != me]
        target = random.choice(candidates) if candidates else None
        return {
            "action": {"type": "vote", "target": target},
            "reasoning": "fallback/vote_random",
        }

    @staticmethod
    def _day_discuss_action(context: dict) -> dict:
        role = context.get("role", Role.VILLAGER.value)
        return {
            "action": {"type": "speak", "target": None},
            "reasoning": f"我是{role}，当前信息有限，先观察发言逻辑。",
        }

    @staticmethod
    def _hunter_shot_action(context: dict) -> dict:
        alive = context.get("alive_players", [])
        me = context.get("player_id")
        candidates = [pid for pid in alive if pid != me]
        target = random.choice(candidates) if candidates else None
        return {
            "action": {"type": "shoot", "target": target},
            "reasoning": "fallback/hunter_random",
        }
