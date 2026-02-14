from __future__ import annotations

import asyncio
import json

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
    return {"status": "ok"}


@app.websocket("/ws/{room_id}/{player_id}")
async def room_ws(websocket: WebSocket, room_id: str, player_id: str) -> None:
    await ws_manager.connect(room_id, websocket)
    try:
        room = room_manager.must_get_room(room_id)
        room.engine.mark_online_status(player_id, True)
        await ws_manager.broadcast(room_id, "presence", {"player_id": player_id, "online": True})

        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            event = msg.get("event")
            payload = msg.get("payload", {})

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
            await ws_manager.broadcast(room_id, "state", state)

            if state["game_over"]:
                room_manager.remove_room_if_game_over(room_id)

    except WebSocketDisconnect:
        room = room_manager.get_room(room_id)
        if room:
            room.engine.mark_online_status(player_id, False)
            await ws_manager.broadcast(room_id, "presence", {"player_id": player_id, "online": False})
        await ws_manager.disconnect(room_id, websocket)
    except Exception as exc:
        await websocket.send_json({"event": "error", "payload": {"message": str(exc)}})
        await asyncio.sleep(0.1)
        await ws_manager.disconnect(room_id, websocket)
