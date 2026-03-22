"""Feishu event handler - core business logic for the Feishu bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .chatroom_bridge import chatroom_bridge, RoomBinding
from .feishu_client import FeishuClient, FeishuAPIError
from .feishu_signature import verify_request
from .message_mapper import (
    CatChatMessage,
    catchat_state_to_feishu,
    catchat_agent_speech_to_feishu,
    catchat_narration_to_feishu,
    feishu_event_to_catchat,
    build_game_status_card,
)
from .config import get_feishu_config

logger = logging.getLogger(__name__)

# Type alias for push callbacks
PushCallback = Callable[[RoomBinding, Dict], Coroutine[Any, Any, None]]


class FeishuHandler:
    """
    Core event handler for Feishu ↔ CatChat bridging.

    Responsibilities:
    1. Verify and parse incoming Feishu webhook events
    2. Route user messages to CatChat rooms
    3. Forward CatChat AI events back to the Feishu group
    4. Manage command handling (/status, /help, etc.)
    """

    def __init__(
        self,
        feishu_client: FeishuClient,
        room_manager: Any,
        ws_manager: Any,
    ):
        self.client = feishu_client
        self.room_manager = room_manager
        self.ws_manager = ws_manager
        # Callbacks invoked when CatChat pushes a new event
        self._push_callbacks: List[PushCallback] = []

    def register_push_callback(self, cb: PushCallback) -> None:
        self._push_callbacks.append(cb)

    # ─── Webhook Entry Point ────────────────────────────────────

    async def handle_webhook(
        self,
        body: bytes,
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Main webhook handler — called by feishu_router.

        Returns an HTTP response dict for FastAPI to return.
        Feishu requires a 200 response within 200ms.
        """
        # 1. Parse JSON
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.error("Webhook received invalid JSON")
            return {"code": 0}  # Still 200 to stop Feishu retries

        # 2. URL verification challenge (Feishu setup phase)
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            logger.info("URL verification challenge received")
            return {"code": 0, "challenge": challenge}

        # 3. Signature verification
        config = get_feishu_config()
        timestamp = headers.get("x-lark-request-timestamp", "")
        sign = headers.get("x-lark-signature", "")

        if config.verification_token:
            ok, reason = verify_request(
                body, timestamp, sign, config.verification_token
            )
            if not ok:
                logger.warning("Signature verification failed: %s", reason)
                return {"code": 401, "msg": "unauthorized"}

        # 4. Route event (async to not block)
        event_type = (
            payload.get("header", {}).get("event_type", "")
            or payload.get("event", {}).get("type", "")
        )

        if event_type == "im.message.receive_v1":
            asyncio.create_task(self._on_message(payload))
        elif event_type == "im.message.reaction_v1":
            asyncio.create_task(self._on_reaction(payload))
        else:
            logger.debug("Unhandled event type: %s", event_type)

        # Acknowledge immediately
        return {"code": 0}

    # ─── Message Events ─────────────────────────────────────────

    async def _on_message(self, payload: Dict) -> None:
        """Process an incoming Feishu message."""
        try:
            await self._process_message(payload)
        except Exception:
            logger.exception("Error processing Feishu message")

    async def _process_message(self, payload: Dict) -> None:
        """Actual message processing logic."""
        event = payload.get("event", {})
        message = event.get("message", {})
        chat_id = message.get("chat_id", "")
        raw_content = message.get("body", {}).get("content", "{}")
        sender_info = event.get("sender", {}).get("sender_id", {})

        # Parse content
        try:
            content_obj = json.loads(raw_content)
        except Exception:
            content_obj = {"text": raw_content}

        text = content_obj.get("text", "").strip()

        # Get binding
        binding = chatroom_bridge.get_by_feishu(chat_id)
        if binding is None:
            # Unknown chat — could auto-create a room here
            logger.debug("No binding for feishu chat_id=%s", chat_id)
            return

        # Skip messages from the bot itself
        sender_open_id = sender_info.get("open_id", "")
        if sender_open_id == binding.feishu_bot_open_id:
            return

        binding.touch()

        # Command handling
        if text.startswith("/") or text.startswith("！"):
            await self._handle_command(binding, sender_open_id, text, message.get("message_id", ""))
            return

        # Forward to CatChat
        catchat_msg = feishu_event_to_catchat(payload)
        if catchat_msg:
            # Enrich with real name if we can
            try:
                user_info = await self.client.get_user_info(sender_open_id)
                name = (
                    user_info.get("data", {})
                    .get("name")
                    or user_info.get("data", {})
                    .get("en_name")
                )
                if name:
                    catchat_msg.player_name = name
            except Exception as e:
                logger.debug("Could not fetch user name: %s", e)

            await self._forward_to_catchat(binding, catchat_msg)

    # ─── Reaction Events ────────────────────────────────────────

    async def _on_reaction(self, payload: Dict) -> None:
        """Handle message reactions (optional enhancement)."""
        event = payload.get("event", {})
        reaction_type = event.get("reaction_type", {})
        emoji_type = reaction_type.get("emoji_type", "")
        message_id = event.get("message", {}).get("message_id", "")
        chat_id = event.get("message", {}).get("chat_id", "")
        logger.debug(
            "Reaction: emoji=%s on message=%s in chat=%s",
            emoji_type, message_id, chat_id,
        )

    # ─── Commands ──────────────────────────────────────────────

    async def _handle_command(
        self,
        binding: RoomBinding,
        sender_open_id: str,
        text: str,
        message_id: str,
    ) -> None:
        """
        Handle slash commands sent in the Feishu group.

        Commands:
          /help, !帮助       → Show help
          /status, !状态     → Show game status
          /start, !开始      → Start game
          /next, !下一阶段   → Advance phase
          /who               → List players
        """
        cmd = text.lstrip("/！").strip().lower()
        chat_id = binding.feishu_chat_id

        if cmd in ("help", "帮助"):
            await self._cmd_help(chat_id)
        elif cmd in ("status", "状态", "state"):
            await self._cmd_status(binding)
        elif cmd in ("start", "开始", "游戏开始"):
            await self._cmd_start(binding)
        elif cmd in ("next", "下一阶段", "下一轮", "advance"):
            await self._cmd_advance(binding)
        elif cmd in ("who", "玩家", "players"):
            await self._cmd_players(binding)
        elif cmd in ("绑定", "bind"):
            await self.client.send_text(
                chat_id, "chat_id",
                "用法：/绑定 <room_id>\n例如：/绑定 my-room-1",
            )
        elif cmd.startswith("绑定 "):
            room_id = cmd[2:].strip()
            await self._cmd_bind(binding, room_id)
        else:
            await self.client.send_text(
                chat_id, "chat_id",
                f"未知命令：{cmd}\n发送 /帮助 查看可用命令",
            )

    async def _cmd_help(self, chat_id: str) -> None:
        help_text = (
            "🐱 **CatChat 飞书 Bot 命令**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "/帮助      — 显示此帮助\n"
            "/状态      — 查看当前游戏状态\n"
            "/开始      — 开始游戏\n"
            "/下一阶段  — 推进到下一阶段\n"
            "/玩家      — 查看当前玩家列表\n"
            "/绑定 <id> — 绑定 CatChat 房间\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "也可以直接发消息，AI 猫猫们会看到并回复！"
        )
        await self.client.send_text(chat_id, "chat_id", help_text)

    async def _cmd_status(self, binding: RoomBinding) -> None:
        """Push current game state as a Feishu card."""
        room = self._get_room(binding.catchat_room_id)
        if room is None:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                "❌ 房间不存在或未启动",
            )
            return

        state = room.engine.public_state()
        card = build_game_status_card(state)
        try:
            await self.client.send_interactive(
                binding.feishu_chat_id, "chat_id", card
            )
        except FeishuAPIError as e:
            logger.error("Failed to send status card: %s", e)
            # Fallback to text posts
            posts = catchat_state_to_feishu(state)
            for post in posts[:3]:  # Limit to 3 messages
                try:
                    await self.client.send_post(
                        binding.feishu_chat_id, "chat_id", post.to_feishu()
                    )
                except Exception:
                    pass

    async def _cmd_start(self, binding: RoomBinding) -> None:
        room = self._get_room(binding.catchat_room_id)
        if room is None:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                "❌ 房间不存在",
            )
            return
        try:
            room.engine.start_game()
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                "🎮 游戏开始！",
            )
        except Exception as e:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                f"❌ 启动失败：{e}",
            )

    async def _cmd_advance(self, binding: RoomBinding) -> None:
        room = self._get_room(binding.catchat_room_id)
        if room is None:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                "❌ 房间不存在",
            )
            return
        try:
            room.engine.timeout_autorun_current_phase()
            state = room.engine.public_state()
            posts = catchat_state_to_feishu(state)[:2]
            for post in posts:
                await self.client.send_post(
                    binding.feishu_chat_id, "chat_id", post.to_feishu()
                )
        except Exception as e:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                f"❌ 推进失败：{e}",
            )

    async def _cmd_players(self, binding: RoomBinding) -> None:
        room = self._get_room(binding.catchat_room_id)
        if room is None:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                "❌ 房间不存在",
            )
            return
        state = room.engine.public_state()
        players = state.get("players", [])
        if not players:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                "暂无玩家",
            )
            return
        lines = ["🐱 **玩家列表**：\n"]
        for p in players:
            status = "🟢 存活" if p.get("alive") else "⚫ 淘汰"
            lines.append(f"• {p.get('nickname', p.get('player_id', '?'))} — {status}")
        await self.client.send_text(
            binding.feishu_chat_id, "chat_id",
            "\n".join(lines),
        )

    async def _cmd_bind(self, binding: RoomBinding, room_id: str) -> None:
        room = self._get_room(room_id)
        if room is None:
            await self.client.send_text(
                binding.feishu_chat_id, "chat_id",
                f"❌ CatChat 房间不存在：{room_id}",
            )
            return
        await chatroom_bridge.bind(
            feishu_chat_id=binding.feishu_chat_id,
            catchat_room_id=room_id,
            feishu_bot_open_id=binding.feishu_bot_open_id,
            mode="werewolf",
        )
        await self.client.send_text(
            binding.feishu_chat_id, "chat_id",
            f"✅ 已绑定 CatChat 房间：{room_id}",
        )

    # ─── CatChat → Feishu Push ─────────────────────────────────

    async def on_catchat_event(self, binding: RoomBinding, event: Dict) -> None:
        """
        Called by the WebSocket bridge when CatChat pushes an event.

        Converts CatChat events to Feishu messages.
        """
        event_type = event.get("event", "")
        payload = event.get("payload", {})
        chat_id = binding.feishu_chat_id

        try:
            if event_type == "phase_changed":
                phase = payload.get("phase", "")
                await self.client.send_text(
                    chat_id, "chat_id",
                    f"⏰ 阶段切换：{phase}",
                )

            elif event_type == "room_state":
                posts = catchat_state_to_feishu(payload)
                for post in posts[:5]:  # Rate limit: max 5 messages
                    try:
                        await self.client.send_post(
                            chat_id, "chat_id", post.to_feishu()
                        )
                        await asyncio.sleep(0.3)  # Brief delay between messages
                    except FeishuAPIError as e:
                        logger.error("Feishu send error: code=%s msg=%s", e.code, e.msg)
                        break

            elif event_type == "agent_speech":
                post = catchat_agent_speech_to_feishu(payload)
                await self.client.send_post(chat_id, "chat_id", post.to_feishu())

            elif event_type in ("god_narration", "narration"):
                post = catchat_narration_to_feishu(payload)
                await self.client.send_post(chat_id, "chat_id", post.to_feishu())

            elif event_type in ("vote_result", "vote"):
                voter = payload.get("player_name", "?")
                target = payload.get("target_name", payload.get("target_id", "?"))
                content = payload.get("content", f"{voter} → {target}")
                await self.client.send_text(chat_id, "chat_id", f"🗳️ {content}")

            elif event_type == "game_over":
                winner = payload.get("winner", "")
                await self.client.send_text(
                    chat_id, "chat_id",
                    f"🏁 游戏结束！胜利阵营：{winner}",
                )

            elif event_type == "fool_reveal":
                content = payload.get("content", "")
                if content:
                    await self.client.send_text(
                        chat_id, "chat_id", f"🎭 {content}",
                    )

        except Exception:
            logger.exception("Error forwarding CatChat event to Feishu")

    # ─── Forward to CatChat ─────────────────────────────────────

    async def _forward_to_catchat(
        self,
        binding: RoomBinding,
        msg: CatChatMessage,
    ) -> None:
        """
        Inject a user's Feishu message into the CatChat room.

        Implementation depends on CatChat's room architecture.
        This sends the message through the WebSocket broadcast.
        """
        room = self._get_room(binding.catchat_room_id)
        if room is None:
            logger.warning(
                "Cannot forward message: room %s not found",
                binding.catchat_room_id,
            )
            return

        # Inject into the speech history / event stream
        # The exact mechanism depends on CatChat's room API
        try:
            await self.ws_manager.broadcast(
                binding.catchat_room_id,
                "feishu_message",
                {
                    "player_id": msg.player_id,
                    "player_name": msg.player_name,
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "source": "feishu",
                },
            )
            logger.info(
                "Forwarded feishu message from %s to room %s",
                msg.player_name, binding.catchat_room_id,
            )
        except Exception:
            logger.exception("Failed to forward feishu message to CatChat")

    # ─── Utilities ─────────────────────────────────────────────

    def _get_room(self, room_id: str):
        """Get a CatChat room by ID, or None."""
        try:
            return self.room_manager.get_room(room_id)
        except Exception:
            return None


# ─── Module-level singleton ──────────────────────────────────────

_feishu_handler: Optional[FeishuHandler] = None


def get_feishu_handler() -> Optional[FeishuHandler]:
    return _feishu_handler


def set_feishu_handler(handler: FeishuHandler) -> None:
    global _feishu_handler
    _feishu_handler = handler
