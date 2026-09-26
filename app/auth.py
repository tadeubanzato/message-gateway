"""
API auth dependency (X-User-Key / X-API-Token headers), backend-agnostic —
uses the repository interface rather than importing a specific DB driver.
"""

from __future__ import annotations

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from starlette.status import HTTP_401_UNAUTHORIZED

from app.db import get_repository
from app.services.auth_tokens import hmac_token_hash_hex


def authenticate(user_key: str | None, raw_token: str | None) -> dict | None:
    """Validate an X-User-Key / X-API-Token pair. Returns the caller's identity
    ({account_id, user_key, token_id}) or None. Used by the HTTP API and MCP."""
    user_key = (user_key or "").strip()
    raw_token = (raw_token or "").strip()
    if not user_key or not raw_token:
        return None

    token_hash = hmac_token_hash_hex(raw_token)
    account = get_repository().find_account_by_active_token_hash(user_key, token_hash)
    if not account:
        return None

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


_user_key_scheme = APIKeyHeader(
    name="X-User-Key", auto_error=False, scheme_name="UserKey",
    description="Your account identifier (starts with `gw_user_`). Sign in to the web app, open **API keys** and copy it from the top of the page. It does not change.",
)
_api_token_scheme = APIKeyHeader(
    name="X-API-Token", auto_error=False, scheme_name="ApiToken",
    description="Secret for one API key (starts with `gw_tok_`). On the **API keys** page, create a key and copy its token right away: it is shown only once. Lost it? Use Replace token.",
)


def require_api_key(
    x_user_key: str | None = Security(_user_key_scheme),
    x_api_token: str | None = Security(_api_token_scheme),
) -> dict:
    if not (x_user_key or "").strip() or not (x_api_token or "").strip():
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Unauthorized (missing X-User-Key or X-API-Token)",
        )
    identity = authenticate(x_user_key, x_api_token)
    if identity is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return identity
