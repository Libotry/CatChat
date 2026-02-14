from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import DefaultDict, Dict, Set

from fastapi import WebSocket


class WSConnectionManager:
    def __init__(self) -> None:
        self._connections: DefaultDict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, room_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[room_id].add(websocket)

    async def disconnect(self, room_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if room_id in self._connections and websocket in self._connections[room_id]:
                self._connections[room_id].remove(websocket)

    async def broadcast(self, room_id: str, event: str, payload: Dict) -> None:
        message = json.dumps({"event": event, "payload": payload}, ensure_ascii=False)
        stale = []
        async with self._lock:
            conns = list(self._connections.get(room_id, set()))
        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)
        if stale:
            async with self._lock:
                for ws in stale:
                    self._connections[room_id].discard(ws)


ws_manager = WSConnectionManager()
