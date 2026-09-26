"""
API auth dependency (X-User-Key / X-API-Token headers), backend-agnostic —
uses the repository interface rather than importing a specific DB driver.
"""

from __future__ import annotations

from fastapi import Header, HTTPException
from starlette.status import HTTP_401_UNAUTHORIZED

from app.db import get_repository
from app.services.auth_tokens import hmac_token_hash_hex


def require_api_key(
    x_user_key: str | None = Header(default=None, alias="X-User-Key"),
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
) -> dict:
    user_key = (x_user_key or "").strip()
    raw_token = (x_api_token or "").strip()

    if not user_key or not raw_token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Unauthorized (missing X-User-Key or X-API-Token)",
        )

    token_hash = hmac_token_hash_hex(raw_token)
    repo = get_repository()
    account = repo.find_account_by_active_token_hash(user_key, token_hash)
    if not account:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    token_id = None
    try:
        tokens = ((account.get("services") or {}).get("message-gateway") or {}).get("tokens") or []
        for t in tokens:
            if isinstance(t, dict) and t.get("token_hash") == token_hash and t.get("revoked_at") is None:
                token_id = t.get("token_id")
                break
    except Exception:
        token_id = None

    return {
        "account_id": str(account["_id"]),
        "user_key": account.get("user_key") or user_key,
        "token_id": token_id,
    }
