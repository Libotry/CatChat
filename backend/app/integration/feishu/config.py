"""Feishu integration configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str
    verification_token: str = ""
    bot_name: str = "CatChat Bot"

    REQUIRED_SCOPES: List[str] = None

    def __post_init__(self):
        if self.REQUIRED_SCOPES is None:
            self.REQUIRED_SCOPES = [
                "im:message",
                "im:message:send_as_bot",
                "im:message.receive_v1",
                "im.chat.member:readonly",
            ]

    @classmethod
    def from_env(cls) -> "FeishuConfig":
        """Load configuration from environment variables."""
        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "").strip()
        bot_name = os.getenv("FEISHU_BOT_NAME", "CatChat Bot").strip()

        if not app_id or not app_secret:
            raise ValueError(
                "FEISHU_APP_ID and FEISHU_APP_SECRET must be set"
            )

        return cls(
            app_id=app_id,
            app_secret=app_secret,
            verification_token=verification_token,
            bot_name=bot_name,
        )

    def is_enabled(self) -> bool:
        """Check if Feishu integration is enabled."""
        return bool(self.app_id and self.app_secret)


# Global config instance (lazy-loaded)
_feishu_config: FeishuConfig | None = None


def get_feishu_config() -> FeishuConfig:
    global _feishu_config
    if _feishu_config is None:
        try:
            _feishu_config = FeishuConfig.from_env()
        except ValueError:
            # Return a disabled config if env vars are not set
            _feishu_config = FeishuConfig(app_id="", app_secret="")
    return _feishu_config
