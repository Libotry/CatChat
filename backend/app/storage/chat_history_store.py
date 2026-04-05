"""
Chat History Store - JSONL 持久化层

每条消息追加到 chat_history/{room_id}.jsonl，
启动时回放日志重建内存状态，支持完整性校验。

目录结构：
  {project_root}/
    chat_history/
      {room_id}.jsonl     ← 追加日志
      {room_id}.checksum  ← SHA256 校验文件（每 N 条刷新一次）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 项目根目录（backend/ 的上一级）
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_HISTORY_DIR = _PROJECT_ROOT / "chat_history"
_CHECKSUM_FLUSH_INTERVAL = 50  # 每 N 条刷新一次 checksum


@dataclass
class PersistedMessage:
    """持久化消息记录"""
    sender_id: str
    sender_name: str
    content: str
    timestamp: str  # ISO format
    message_type: str = "normal"
    turn: int = 0
    msg_hash: str = ""  # SHA256(content + timestamp)

    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "timestamp": self.timestamp,
            "message_type": self.message_type,
            "turn": self.turn,
            "msg_hash": self.msg_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PersistedMessage":
        return cls(
            sender_id=d["sender_id"],
            sender_name=d["sender_name"],
            content=d["content"],
            timestamp=d["timestamp"],
            message_type=d.get("message_type", "normal"),
            turn=d.get("turn", 0),
            msg_hash=d.get("msg_hash", ""),
        )


class ChatHistoryStore:
    """
    JSONL 追加日志持久化。

    核心保证：
    1. append 是追加写（不 overwrite），崩溃安全
    2. 启动时 replay 完整日志，可重建内存状态
    3. checksum 文件用于完整性校验
    """

    def __init__(self, history_dir: Optional[Path] = None):
        self.history_dir = history_dir or _HISTORY_DIR
        self.history_dir.mkdir(parents=True, exist_ok=True)
        # 每个 room 一把锁，避免多线程/并发写冲突
        self._locks: dict[str, threading.Lock] = {}
        self._lock_guard = threading.Lock()
        # 写计数：每 _CHECKSUM_FLUSH_INTERVAL 条刷新 checksum
        self._write_counts: dict[str, int] = {}
        logger.info(f"[chat-history] Store initialized at {self.history_dir}")

    def _get_lock(self, room_id: str) -> threading.Lock:
        with self._lock_guard:
            if room_id not in self._locks:
                self._locks[room_id] = threading.Lock()
            return self._locks[room_id]

    def _jsonl_path(self, room_id: str) -> Path:
        return self.history_dir / f"{room_id}.jsonl"

    def _checksum_path(self, room_id: str) -> Path:
        return self.history_dir / f"{room_id}.checksum"

    def _compute_msg_hash(self, content: str, timestamp: str) -> str:
        return hashlib.sha256(f"{content}|{timestamp}".encode()).hexdigest()[:16]

    # ──────────────────────────────────────────────────────────────
    # 追加写
    # ──────────────────────────────────────────────────────────────

    def append(
        self,
        room_id: str,
        sender_id: str,
        sender_name: str,
        content: str,
        message_type: str = "normal",
        turn: int = 0,
    ) -> PersistedMessage:
        """
        追加一条消息到 JSONL 文件，线程安全。
        """
        lock = self._get_lock(room_id)
        with lock:
            ts = datetime.utcnow().isoformat()
            msg = PersistedMessage(
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                timestamp=ts,
                message_type=message_type,
                turn=turn,
                msg_hash=self._compute_msg_hash(content, ts),
            )

            path = self._jsonl_path(room_id)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")

            # 计数，达到阈值则刷新 checksum
            count = self._write_counts.get(room_id, 0) + 1
            self._write_counts[room_id] = count
            if count % _CHECKSUM_FLUSH_INTERVAL == 0:
                self._refresh_checksum(room_id)

            logger.debug(f"[chat-history][{room_id}] appended msg (turn={turn})")
            return msg

    # ──────────────────────────────────────────────────────────────
    # 回放读取
    # ──────────────────────────────────────────────────────────────

    def replay(self, room_id: str, limit: int = 0) -> List[PersistedMessage]:
        """
        回放指定 room 的完整历史（可选 limit 限制条数）。

        用于：启动时重建内存状态 / 新猫加入时拉取历史
        """
        path = self._jsonl_path(room_id)
        if not path.exists():
            return []

        messages: List[PersistedMessage] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.append(PersistedMessage.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"[chat-history][{room_id}] corrupt line skipped: {e}")
        except Exception as e:
            logger.error(f"[chat-history][{room_id}] replay failed: {e}")

        if limit > 0:
            messages = messages[-limit:]

        logger.info(f"[chat-history][{room_id}] replayed {len(messages)} messages")
        return messages

    def get_recent(self, room_id: str, limit: int = 20) -> List[PersistedMessage]:
        """读取最近 N 条（用于 MCP 工具返回）"""
        return self.replay(room_id, limit=limit)

    # ──────────────────────────────────────────────────────────────
    # 完整性校验
    # ──────────────────────────────────────────────────────────────

    def _refresh_checksum(self, room_id: str) -> None:
        """对当前 JSONL 文件内容计算 SHA256，写入 .checksum 文件"""
        path = self._jsonl_path(room_id)
        if not path.exists():
            return

        try:
            with open(path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            cs_path = self._checksum_path(room_id)
            with open(cs_path, "w", encoding="utf-8") as f:
                f.write(file_hash)
            logger.debug(f"[chat-history][{room_id}] checksum refreshed")
        except Exception as e:
            logger.warning(f"[chat-history][{room_id}] checksum refresh failed: {e}")

    def verify_integrity(self, room_id: str) -> bool:
        """验证 JSONL 文件与 checksum 是否匹配"""
        cs_path = self._checksum_path(room_id)
        jsonl_path = self._jsonl_path(room_id)

        if not cs_path.exists():
            logger.warning(f"[chat-history][{room_id}] no checksum file, skipping verify")
            return True  # 没有 checksum 文件，跳过

        if not jsonl_path.exists():
            return False

        try:
            with open(cs_path, "r", encoding="utf-8") as f:
                expected = f.read().strip()
            with open(jsonl_path, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
            ok = actual == expected
            if ok:
                logger.info(f"[chat-history][{room_id}] integrity OK")
            else:
                logger.error(f"[chat-history][{room_id}] INTEGRITY MISMATCH!")
            return ok
        except Exception as e:
            logger.error(f"[chat-history][{room_id}] verify failed: {e}")
            return False

    # ──────────────────────────────────────────────────────────────
    # 统计
    # ──────────────────────────────────────────────────────────────

    def get_stats(self, room_id: str) -> dict:
        """返回 room 的存储统计"""
        path = self._jsonl_path(room_id)
        if not path.exists():
            return {"exists": False, "msg_count": 0, "file_size_bytes": 0}

        stat = path.stat()
        messages = self.replay(room_id, limit=0)
        return {
            "exists": True,
            "msg_count": len(messages),
            "file_size_bytes": stat.st_size,
            "integrity": self.verify_integrity(room_id),
        }


# 全局单例
_store_instance: Optional[ChatHistoryStore] = None


def get_chat_history_store() -> ChatHistoryStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = ChatHistoryStore()
    return _store_instance


def reset_chat_history_store() -> None:
    global _store_instance
    _store_instance = None
