from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.api.deps import room_manager
from app.api.rest import router as rest_router
from app.websocket.handler import ws_manager

app = FastAPI(
    title="Werewolf Backend",
    version="0.1.0",
    description="State-machine driven werewolf backend for standard 12-player game.",
)

app.include_router(rest_router)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "werewolf-backend",
        "summary": room_manager.health_summary(),
    }


def _active_players_for_phase(room) -> list[str]:
    phase = room.engine.snapshot.phase
    players = room.engine.snapshot.players.values()
    if phase.value == "night_wolf":
        return [p.player_id for p in players if p.alive and p.role and p.role.value == "werewolf"]
    if phase.value == "night_guard":
        return [p.player_id for p in players if p.alive and p.role and p.role.value == "guard"]
    if phase.value == "night_witch":
        return [p.player_id for p in players if p.alive and p.role and p.role.value == "witch"]
    if phase.value == "night_seer":
        return [p.player_id for p in players if p.alive and p.role and p.role.value == "seer"]
    if phase.value == "day_vote":
        return [p.player_id for p in players if p.alive and p.can_vote]
    if phase.value == "day_discuss":
        return [p.player_id for p in players if p.alive]
    return []


def _god_view_for_phase(room) -> dict | None:
    if room.engine.snapshot.phase.value != "night_wolf":
        return None

    votes = room.engine.snapshot.round_context.night_actions.wolf_votes
    if not votes:
        return None

    counter: dict[str, int] = {}
    for target in votes.values():
        counter[target] = counter.get(target, 0) + 1
    consensus_target = max(counter, key=counter.get) if counter else None

    return {
        "wolf_targets": votes,
        "consensus_target": consensus_target,
    }


@app.websocket("/ws/{room_id}/{player_id}")
async def room_ws(websocket: WebSocket, room_id: str, player_id: str) -> None:
    await ws_manager.connect(room_id, player_id, websocket)
    try:
        room = room_manager.must_get_room(room_id)
        room.engine.mark_online_status(player_id, True)
        await ws_manager.broadcast(
            room_id,
            "agent_status_update",
            {
                "player_id": player_id,
                "status": "online",
                "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                "error_msg": None,
            },
        )

        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            event = msg.get("event") or msg.get("type")
            payload = msg.get("payload", {})

            if event == "subscribe":
                view_mode = msg.get("view_mode", "god")
                await ws_manager.update_view_mode(websocket, view_mode)
                await ws_manager.send_event(
                    websocket,
                    "subscribed",
                    {
                        "room_id": room_id,
                        "player_id": player_id,
                        "view_mode": view_mode,
                    },
                )
                await ws_manager.broadcast_room_state(room_id, room.engine.public_state())
                continue

            if event == "change_view":
                view_mode = msg.get("mode", "god")
                await ws_manager.update_view_mode(websocket, view_mode)
                await ws_manager.send_event(
                    websocket,
                    "view_changed",
                    {
                        "room_id": room_id,
                        "player_id": player_id,
                        "view_mode": view_mode,
                    },
                )
                await ws_manager.broadcast_room_state(room_id, room.engine.public_state())
                continue

            if event == "advance":
                room.engine.timeout_autorun_current_phase()
            elif event == "night_action":
                room.engine.submit_night_action(
                    actor_id=payload["player_id"],
                    target_id=payload.get("target_id"),
                    save=payload.get("save"),
                )
            elif event == "vote":
                room.engine.submit_vote(payload["player_id"], payload.get("target_id"))
            elif event == "hunter_shot":
                room.engine.submit_hunter_shot(payload["player_id"], payload["target_id"])

            state = room.engine.public_state()
            await ws_manager.broadcast_room_state(room_id, state)
            await ws_manager.broadcast_phase_changed(
                room_id=room_id,
                payload={
                    "phase": state["phase"],
                    "remaining_time": 60,
                    "active_players": _active_players_for_phase(room),
                },
                god_view=_god_view_for_phase(room),
            )

            if state["game_over"]:
                room_manager.remove_room_if_game_over(room_id)

    except WebSocketDisconnect:
        room = room_manager.get_room(room_id)
        if room:
            room.engine.mark_online_status(player_id, False)
            await ws_manager.broadcast(
                room_id,
                "agent_status_update",
                {
                    "player_id": player_id,
                    "status": "offline",
                    "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                    "error_msg": None,
                },
            )
        await ws_manager.disconnect(room_id, websocket)
    except Exception as exc:
        await websocket.send_json({"event": "error", "payload": {"message": str(exc)}})
        await asyncio.sleep(0.1)
        await ws_manager.disconnect(room_id, websocket)
