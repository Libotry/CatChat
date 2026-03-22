from __future__ import annotations

import asyncio
import json
from datetime import datetime
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import room_manager
from app.config.frontend_profile_env import load_frontend_profile
from app.api.rest import router as rest_router
from app.websocket.handler import ws_manager
from app.api.mcp_routes import router as mcp_router
from app.api.autonomous_chat_routes import router as autonomous_chat_router
from app.api.autonomous_chat_ws import autonomous_chat_websocket_endpoint

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Werewolf Backend",
    version="0.1.0",
    description="State-machine driven werewolf backend for standard 12-player game.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(mcp_router)
app.include_router(autonomous_chat_router)

# ─── Feishu Integration ──────────────────────────────────────────

_feishu_initialized = False


@app.on_event("startup")
async def feishu_startup() -> None:
    """Initialize Feishu integration on app startup."""
    global _feishu_initialized

    try:
        from app.integration.feishu.config import get_feishu_config
        from app.integration.feishu.feishu_router import router as feishu_router
        from app.integration.feishu.feishu_client import FeishuClient
        from app.integration.feishu.token_manager import get_token_manager
        from app.integration.feishu.feishu_handler import FeishuHandler, set_feishu_handler
        from app.integration.feishu.ws_bridge import setup_feishu_bridge
    except Exception as e:
        logger.warning("Feishu integration module not available: %s", e)
        return

    config = get_feishu_config()
    if not config.is_enabled():
        logger.info("Feishu integration disabled (FEISHU_APP_ID not set)")
        return

    try:
        # Register routes
        app.include_router(feishu_router)
        logger.info("Feishu router registered")

        # Initialize handler
        tm = get_token_manager(config.app_id, config.app_secret)
        client = FeishuClient(tm)
        handler = FeishuHandler(
            feishu_client=client,
            room_manager=room_manager,
            ws_manager=ws_manager,
        )
        set_feishu_handler(handler)
        logger.info("Feishu handler initialized for app_id=%s", config.app_id)

        # Start WS bridge (subscribes to CatChat events → forwards to Feishu)
        await setup_feishu_bridge(handler, room_manager, ws_manager)
        logger.info("Feishu WS bridge started")

        _feishu_initialized = True

    except Exception:
        logger.exception("Failed to initialize Feishu integration")


# ─── Health ────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "werewolf-backend",
        "summary": room_manager.health_summary(),
        "auto_bootstrap": getattr(app.state, "auto_bootstrap", {"status": "not-run"}),
        "feishu": {"initialized": _feishu_initialized},
    }


@app.on_event("startup")
async def auto_bootstrap_on_startup() -> None:
    try:
        auto_bootstrap_enabled = str(
            os.getenv("CATCHAT_AUTO_BOOTSTRAP_ON_STARTUP", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not auto_bootstrap_enabled:
            app.state.auto_bootstrap = {
                "status": "skipped",
                "reason": "auto_bootstrap_disabled",
            }
            return

        profile = load_frontend_profile()
        cats = profile.get("cats") if isinstance(profile, dict) else []
        if not isinstance(cats, list) or len(cats) == 0:
            app.state.auto_bootstrap = {
                "status": "skipped",
                "reason": "no_cats_in_env",
            }
            return

        result = await asyncio.to_thread(room_manager.bootstrap_from_frontend_env)
        bootstrap_result = result.get("bootstrap") if isinstance(result, dict) else {}
        bootstrap_mode = None
        if isinstance(bootstrap_result, dict):
            bootstrap_mode = (
                bootstrap_result.get("startup_mode")
                or bootstrap_result.get("mode")
            )
            if bootstrap_mode is None and bootstrap_result.get("reused") is True:
                bootstrap_mode = "reused"
        app.state.auto_bootstrap = {
            "status": "ok",
            "room_id": result.get("room_id"),
            "player_count": result.get("player_count"),
            "source": result.get("source"),
            "bootstrap_mode": bootstrap_mode,
            "bootstrap": bootstrap_result
            if isinstance(bootstrap_result, dict)
            else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("auto bootstrap from env failed")
        app.state.auto_bootstrap = {
            "status": "failed",
            "error": str(exc),
        }


@app.on_event("shutdown")
async def cleanup_on_shutdown() -> None:
    try:
        await asyncio.to_thread(room_manager.shutdown_cleanup)
        logger.info(
            "graceful shutdown cleanup completed: child agent ports released"
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("shutdown cleanup failed: %s", exc)

    # Stop Feishu WS bridge
    try:
        from app.integration.feishu.ws_bridge import get_feishu_ws_bridge
        bridge = get_feishu_ws_bridge()
        if bridge:
            await bridge.stop()
    except Exception:
        pass


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


@app.websocket("/ws/chat/{room_id}")
async def autonomous_chat_ws(websocket: WebSocket, room_id: str) -> None:
    """自主聊天 WebSocket 端点 - 实时接收 AI 猫对话消息"""
    await autonomous_chat_websocket_endpoint(websocket, room_id)
