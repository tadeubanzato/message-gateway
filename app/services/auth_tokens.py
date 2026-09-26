"""
API token generation + HMAC hashing.

The HMAC secret is generated automatically on first start (app.bootstrap);
a TOKEN_HMAC_SECRET environment variable still overrides it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from app import bootstrap

_SECRET_BYTES = bootstrap.get("token_hmac_secret").encode("utf-8")

USER_KEY_PREFIX = "gw_user_"
TOKEN_PREFIX = "gw_tok_"


def new_user_key() -> str:
    return USER_KEY_PREFIX + secrets.token_hex(4)


def generate_raw_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_last4(raw_token: str) -> str:
    return (raw_token or "")[-4:]


def hmac_token_hash_hex(raw_token: str) -> str:
    mac = hmac.new(_SECRET_BYTES, raw_token.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()
