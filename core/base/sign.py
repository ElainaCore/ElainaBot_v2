#!/usr/bin/env python
"""HTTP 回调签名验证"""

import json

from cryptography.hazmat.primitives.asymmetric import ed25519


def verify_and_respond(raw_body, bot_secret):
    """验证 QQ 开放平台回调签名, 返回响应 JSON 或 None"""
    data = json.loads(raw_body) if isinstance(raw_body, bytes | str) else raw_body
    d = data.get("d") or {}
    token, ts = d.get("plain_token"), d.get("event_ts")
    if not token or ts is None:
        return None
    return json.dumps(generate_signature(bot_secret, str(ts), token))


def generate_signature(bot_secret, event_ts, plain_token):
    """生成 Ed25519 签名"""
    raw = (bot_secret * ((32 // len(bot_secret)) + 1))[:32]
    key = ed25519.Ed25519PrivateKey.from_private_bytes(raw.encode())
    sig = key.sign(f"{event_ts}{plain_token}".encode()).hex()
    return {"plain_token": plain_token, "signature": sig}
