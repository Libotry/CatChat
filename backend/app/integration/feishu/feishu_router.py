"""FastAPI router for Feishu integration endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .chatroom_bridge import chatroom_bridge
from .config import get_feishu_config
from .feishu_client import FeishuClient
from .feishu_handler import get_feishu_handler, FeishuHandler
from .token_manager import get_token_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feishu", tags=["feishu"])


def require_handler() -> FeishuHandler:
    """Dependency: ensure Feishu handler is initialized."""
    handler = get_feishu_handler()
    if handler is None:
        raise HTTPException(
            status_code=500,
            detail="Feishu integration not initialized. "
                   "Ensure FEISHU_APP_ID and FEISHU_APP_SECRET are set.",
        )
    return handler


# ─── Webhook ──────────────────────────────────────────────────

@router.post("/webhook")
async def feishu_webhook(
    request: Request,
    x_lark_request_timestamp: str | None = Header(None, alias="X-Lark-Request-Timestamp"),
    x_lark_signature: str | None = Header(None, alias="X-Lark-Signature"),
    handler: FeishuHandler = Depends(require_handler),
) -> Dict[str, Any]:
    """
    Feishu event subscription webhook endpoint.

    This endpoint receives:
    - URL verification challenges (setup phase)
    - im.message.receive_v1 events
    - im.message.reaction_v1 events

    Feishu requires: HTTP 200 within 200ms + {"code": 0}
    """
    body = await request.body()
    headers = {
        "x-lark-request-timestamp": x_lark_request_timestamp or "",
        "x-lark-signature": x_lark_signature or "",
    }
    return await handler.handle_webhook(body, headers)


# ─── Bot Info ─────────────────────────────────────────────────

@router.get("/bot")
async def get_bot_info(handler: FeishuHandler = Depends(require_handler)) -> Dict[str, Any]:
    """Get bot profile information."""
    config = get_feishu_config()
    if not config.is_enabled():
        raise HTTPException(status_code=400, detail="Feishu integration not enabled")

    tm = get_token_manager(config.app_id, config.app_secret)
    client = FeishuClient(tm)
    try:
        info = await client.get_bot_info()
        return info.get("data", info)
    except Exception as e:
        logger.exception("Failed to get bot info")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Room Bindings ────────────────────────────────────────────

@router.post("/bind")
async def bind_room(
    feishu_chat_id: str,
    catchat_room_id: str,
    handler: FeishuHandler = Depends(require_handler),
) -> Dict[str, Any]:
    """
    Bind a Feishu group to a CatChat room.

    After binding, messages from the Feishu group will be forwarded
    to the CatChat room, and CatChat events will be pushed to the group.

    Query params:
      feishu_chat_id:  The Feishu group chat ID
      catchat_room_id: The CatChat room ID
    """
    config = get_feishu_config()
    if not config.is_enabled():
        raise HTTPException(status_code=400, detail="Feishu not enabled")

    # Get bot's own open_id
    try:
        tm = get_token_manager(config.app_id, config.app_secret)
        client = FeishuClient(tm)
        bot_info = await client.get_bot_info()
        bot_open_id = (
            bot_info.get("data", {}).get("bot", {}).get("open_id", "")
            or bot_info.get("open_id", "")
        )
    except Exception as e:
        logger.warning("Could not get bot open_id: %s", e)
        bot_open_id = ""

    await chatroom_bridge.bind(
        feishu_chat_id=feishu_chat_id,
        catchat_room_id=catchat_room_id,
        feishu_bot_open_id=bot_open_id,
    )
    logger.info("Bound feishu_chat=%s → catchat_room=%s", feishu_chat_id, catchat_room_id)
    return {
        "status": "ok",
        "feishu_chat_id": feishu_chat_id,
        "catchat_room_id": catchat_room_id,
    }


@router.delete("/bind/{feishu_chat_id}")
async def unbind_room(
    feishu_chat_id: str,
    handler: FeishuHandler = Depends(require_handler),
) -> Dict[str, Any]:
    """Unbind a Feishu group from its CatChat room."""
    removed = await chatroom_bridge.unbind(feishu_chat_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Binding not found")
    return {"status": "ok", "feishu_chat_id": feishu_chat_id}


@router.get("/bindings")
async def list_bindings() -> Dict[str, Any]:
    """List all active Feishu ↔ CatChat room bindings."""
    bindings = chatroom_bridge.list_all()
    return {
        "count": len(bindings),
        "bindings": [
            {
                "feishu_chat_id": b.feishu_chat_id,
                "catchat_room_id": b.catchat_room_id,
                "mode": b.mode,
                "last_message_at": b.last_message_at,
                "created_at": b.created_at,
            }
            for b in bindings
        ],
    }


# ─── Manual Send (for testing / debugging) ──────────────────────

@router.post("/send")
async def send_message(
    receive_id: str,
    content: str,
    receive_id_type: str = "chat_id",
    handler: FeishuHandler = Depends(require_handler),
) -> Dict[str, Any]:
    """
    Manually send a message to a Feishu recipient.

    Useful for testing and debugging.

    Query params:
      receive_id:       Target ID (chat_id / open_id / user_id)
      content:          Message text
      receive_id_type:  One of "chat_id", "open_id", "user_id"
    """
    config = get_feishu_config()
    if not config.is_enabled():
        raise HTTPException(status_code=400, detail="Feishu not enabled")

    try:
        tm = get_token_manager(config.app_id, config.app_secret)
        client = FeishuClient(tm)
        msg_id = await client.send_text(receive_id, receive_id_type, content)
        return {"status": "sent", "message_id": msg_id}
    except Exception as e:
        logger.exception("Failed to send message")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Health Check ─────────────────────────────────────────────

@router.get("/health")
async def feishu_health() -> Dict[str, Any]:
    """Check Feishu integration health status."""
    config = get_feishu_config()
    bindings = chatroom_bridge.count()
    handler = get_feishu_handler()

    return {
        "enabled": config.is_enabled(),
        "app_id_set": bool(config.app_id),
        "bindings_count": bindings,
        "handler_initialized": handler is not None,
    }
