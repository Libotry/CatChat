"""Message format mapping between Feishu and CatChat."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ─── Internal CatChat message representation ─────────────────────

@dataclass
class CatChatMessage:
    """Canonical message format used inside CatChat."""
    player_id: str          # Unique player identifier
    player_name: str        # Display name
    content: str            # Message text
    message_type: str       # "normal" | "action" | "thinking"
    timestamp: str          # ISO 8601 timestamp
    raw: Dict[str, Any] = field(default_factory=dict)  # Original payload


# ─── Feishu post rich text building ─────────────────────────────

@dataclass
class FeishuPostContent:
    """
    Feishu post (rich text) message content.

    Feishu post structure:
    {
        "zh_cn": {
            "title": "...",
            "content": [
                [{"tag": "text", "text": "..."}],
                [{"tag": "at", "user_id": "open_xxx"}],
                [{"tag": "link", "text": "...", "href": "..."}],
            ]
        }
    }
    """
    title: str
    body_lines: List[List[Dict]] = field(default_factory=list)

    def to_feishu(self) -> Dict:
        return {
            "zh_cn": {
                "title": self.title,
                "content": self.body_lines,
            }
        }

    @classmethod
    def text_only(cls, title: str, text: str) -> "FeishuPostContent":
        """Create a simple text-only post."""
        return cls(title=title, body_lines=[[{"tag": "text", "text": text}]])


# ─── Feishu → CatChat ───────────────────────────────────────────

def feishu_event_to_catchat(event_payload: Dict[str, Any]) -> Optional[CatChatMessage]:
    """
    Parse a Feishu im.message.receive_v1 event into CatChatMessage.

    Feishu event structure:
    {
        "schema": "2.0",
        "header": {
            "event_type": "im.message.receive_v1",
            "event_id": "...",
            "create_time": "...",
            "token": "...",
            "app_id": "...",
            "tenant_key": "..."
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "...", "user_id": "...", "chat_id": "..."},
                "sender_type": "user" | "bot",
                "tenant_key": "..."
            },
            "message": {
                "message_id": "...",
                "chat_id": "...",
                "msg_type": "text" | "post" | "image" | "file" | ...,
                "create_time": "...",
                "body": {"content": "..."}
            }
        }
    }
    """
    event = event_payload.get("event", {})
    message = event.get("message", {})
    sender = event.get("sender", {})
    sender_id_info = sender.get("sender_id", {})

    msg_type = message.get("msg_type", "")
    sender_type = sender.get("sender_type", "")

    # Only process text messages
    if msg_type != "text":
        return None

    # Skip bot messages to avoid loops
    if sender_type == "bot":
        return None

    # Parse content
    raw_content = message.get("body", {}).get("content", "")
    text = raw_content.strip() if isinstance(raw_content, str) else ""

    # Extract sender info
    open_id = sender_id_info.get("open_id", "")
    user_id = sender_id_info.get("user_id", "")
    player_id = open_id or user_id

    if not player_id:
        return None

    return CatChatMessage(
        player_id=player_id,
        player_name=player_id,  # Will be enriched with real name later
        content=text,
        message_type="normal",
        timestamp=message.get("create_time", ""),
        raw=event_payload,
    )


# ─── CatChat → Feishu ──────────────────────────────────────────

def catchat_text_to_feishu(content: str) -> str:
    """
    Transform CatChat plain text for Feishu.

    Applies basic text transformations:
    - Markdown-style **bold** → Feishu bold tag
    - @player → Feishu at tag (if we have the open_id)
    """
    # For now, pass through as-is
    return content


def catchat_agent_speech_to_feishu(msg: Dict) -> FeishuPostContent:
    """
    Convert a CatChat agent_speech event to Feishu post.

    Args:
        msg: {
            "player_id": "...",
            "player_name": "...",
            "content": "...",
            "role": "werewolf" | "villager" | ...,
            ...
        }
    """
    player_id = msg.get("player_id", "")
    player_name = msg.get("player_name", player_id)
    content = msg.get("content", "")

    # Emoji prefix based on role
    role = msg.get("role", "")
    role_emoji = {
        "werewolf": "🐺",
        "villager": "🏠",
        "seer": "🔮",
        "witch": "🧪",
        "hunter": "🏹",
        "guard": "🛡️",
        "god": "🐺",
        "judge": "⚖️",
    }.get(role, "🐱")

    title = f"{role_emoji} {player_name}"

    return FeishuPostContent(
        title=title,
        body_lines=[[{"tag": "text", "text": content}]],
    )


def catchat_narration_to_feishu(msg: Dict) -> FeishuPostContent:
    """Convert a CatChat god_narration event to Feishu post."""
    content = msg.get("content", msg.get("narration", ""))
    return FeishuPostContent.text_only(title="📢 法官旁白", text=content)


def catchat_vote_to_feishu(msg: Dict) -> FeishuPostContent:
    """Convert a CatChat vote event to Feishu post."""
    voter = msg.get("player_name", msg.get("player_id", "?"))
    target = msg.get("target_name", msg.get("target_id", "?"))
    content = msg.get("content", f"{voter} 投票给了 {target}")
    return FeishuPostContent.text_only(title="🗳️ 投票", text=content)


def catchat_state_to_feishu(state: Dict) -> List[FeishuPostContent]:
    """
    Convert CatChat room_state to a list of Feishu posts.

    One post for game status, one post per recent speech entry.
    """
    posts = []

    phase = state.get("phase", "")
    round_no = state.get("round_no", 1)
    players: List[Dict] = state.get("players", [])

    # Phase label mapping
    phase_map = {
        "prepare": "🔧 准备阶段",
        "day_announce": "☀️ 白天 · 公告",
        "day_discuss": "☀️ 白天 · 讨论",
        "day_vote": "🗳️ 白天 · 投票",
        "night_wolf": "🌙 夜晚 · 狼人行动",
        "night_guard": "🌙 夜晚 · 守卫行动",
        "night_witch": "🌙 夜晚 · 女巫行动",
        "night_seer": "🌙 夜晚 · 预言家行动",
        "game_over": "🏁 游戏结束",
    }
    phase_text = phase_map.get(phase, phase)

    alive = [p for p in players if p.get("alive")]
    dead = [p for p in players if not p.get("alive")]

    # Status card
    status_lines: List[List[Dict]] = [
        [{"tag": "text", "text": f"第 {round_no} 轮 · {phase_text}"}],
        [
            {"tag": "text", "text": f"存活：{len(alive)} 人"},
            {"tag": "text", "text": "  |  "},
            {"tag": "text", "text": f"淘汰：{len(dead)} 人"},
        ],
    ]

    if alive:
        alive_names = "、".join(
            p.get("nickname", p.get("player_id", "?"))
            for p in alive
        )
        status_lines.append([{"tag": "text", "text": f"存活：{alive_names}"}])

    if dead:
        dead_names = "、".join(
            p.get("nickname", p.get("player_id", "?"))
            for p in dead
        )
        status_lines.append([{"tag": "text", "text": f"淘汰：{dead_names}"}])

    posts.append(FeishuPostContent(title="📊 游戏状态", body_lines=status_lines))

    # Recent speeches (last 5)
    history: List[Dict] = state.get("speech_history", [])[-5:]
    for entry in history:
        event_type = entry.get("event", "")
        if event_type == "agent_speech":
            posts.append(catchat_agent_speech_to_feishu(entry))
        elif event_type in ("god_narration", "narration"):
            posts.append(catchat_narration_to_feishu(entry))
        elif event_type in ("vote", "vote_result"):
            posts.append(catchat_vote_to_feishu(entry))

    return posts


# ─── Feishu interactive card helpers ────────────────────────────

def build_game_status_card(state: Dict) -> Dict:
    """
    Build a Feishu interactive card showing game status.

    Returns a complete Feishu card JSON structure.
    """
    phase = state.get("phase", "")
    round_no = state.get("round_no", 1)
    players: List[Dict] = state.get("players", [])

    phase_map = {
        "prepare": "🔧 准备阶段",
        "day_announce": "☀️ 白天 · 公告",
        "day_discuss": "☀️ 白天 · 讨论",
        "day_vote": "🗳️ 白天 · 投票",
        "night_wolf": "🌙 夜晚 · 狼人",
        "night_guard": "🌙 夜晚 · 守卫",
        "night_witch": "🌙 夜晚 · 女巫",
        "night_seer": "🌙 夜晚 · 预言家",
        "game_over": "🏁 游戏结束",
    }

    alive = [p for p in players if p.get("alive")]
    dead = [p for p in players if not p.get("alive")]

    elements = [
        {
            "tag": "markdown",
            "content": f"**第 {round_no} 轮 · {phase_map.get(phase, phase)}**\n\n"
                        f"🟢 存活 ({len(alive)})："
                        + "、".join(p.get("nickname", "?") for p in alive)
                        if alive else "🟢 存活：无人",
        },
    ]

    if dead:
        elements.append({
            "tag": "markdown",
            "content": f"🔴 淘汰 ({len(dead)})："
                       + "、".join(p.get("nickname", "?") for p in dead),
        })

    # Recent history
    history: List[Dict] = state.get("speech_history", [])[-3:]
    if history:
        elements.append({"tag": "hr"})
        for entry in history[-3:]:
            event = entry.get("event", "")
            player = entry.get("player_name", entry.get("player_id", "?"))
            content = entry.get("content", "")
            if content and event == "agent_speech":
                elements.append({
                    "tag": "markdown",
                    "content": f"**{player}**：{content[:100]}",
                })

    return {
        "config": {"wide_screen_mode": True},
        "elements": elements,
    }
