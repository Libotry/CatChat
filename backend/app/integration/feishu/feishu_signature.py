"""Feishu request signature verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Tuple


def verify_feishu_signature(
    sign: str,
    timestamp: str,
    secret: str,
) -> bool:
    """
    Verify Feishu request signature.

    Algorithm:
    1. Take timestamp + "\n" + APP_SECRET as the string to sign
    2. HMAC-SHA256 with APP_SECRET as key
    3. Base64 encode

    Args:
        sign: Signature from Feishu request header
        timestamp: Timestamp from Feishu request header
        secret: APP Secret

    Returns:
        True if signature is valid, False otherwise
    """
    if not sign or not timestamp or not secret:
        return False

    try:
        string_to_sign = f"{timestamp}\n{secret}"
        hashed = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected = base64.b64encode(hashed).decode("utf-8")
        return hmac.compare_digest(expected, sign)
    except Exception:
        return False


def verify_request(
    body: bytes,
    timestamp: str,
    sign: str,
    secret: str,
    tolerance_sec: int = 60,
) -> Tuple[bool, str]:
    """
    Comprehensive Feishu request verification.

    Verifies both timestamp freshness and signature validity.

    Args:
        body: Raw request body bytes
        timestamp: X-Lark-Request-Timestamp header value
        sign: X-Lark-Signature header value
        secret: APP Secret
        tolerance_sec: Allowed time drift in seconds (default 60s)

    Returns:
        Tuple of (success, message)
    """
    # Validate timestamp
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False, "invalid_timestamp"

    # Check for replay attack (timestamp too old or in future)
    current_time = time.time()
    if abs(current_time - ts) > tolerance_sec:
        return False, "timestamp_expired"

    # Verify signature
    if not verify_feishu_signature(sign, timestamp, secret):
        return False, "signature_mismatch"

    return True, "ok"
