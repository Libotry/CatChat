from __future__ import annotations

import os
import time
from typing import Any, Dict

from fastapi import FastAPI
from pydantic import BaseModel


class ActRequest(BaseModel):
    session_id: str
    player_id: str
    role: str
    phase: str
    visible_state: Dict[str, Any]
    prompt_template: str


class ActResponse(BaseModel):
    action: Dict[str, Any]
    reasoning: str = ""
    timestamp: int


app = FastAPI(title="Cat Agent Template", version="0.1.0")

MODEL_TYPE = os.getenv("MODEL_TYPE", "mock-cat")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_type": MODEL_TYPE}


@app.post("/act", response_model=ActResponse)
def act(req: ActRequest) -> ActResponse:
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

    reasoning = f"[{MODEL_TYPE}] phase={req.phase} role={req.role} target={target}"
    return ActResponse(
        action={"type": action_type, "target": target, "save": req.phase == "night_witch" and bool(target)},
        reasoning=reasoning,
        timestamp=int(time.time()),
    )
