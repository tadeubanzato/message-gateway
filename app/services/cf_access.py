"""
Optional single sign-on for the web portal through Cloudflare Access (Zero Trust).

When enabled in Settings, Cloudflare authenticates the person in front of the portal and
forwards a signed JWT in the `Cf-Access-Jwt-Assertion` header. We verify that JWT
(signature against the team's published keys, issuer, audience, expiry) and log in the
portal account with the same email. Password login keeps working unless the
administrator turns it off. The API and MCP endpoints are not affected: they keep using
X-User-Key / X-API-Token.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.request
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services import secret_store

ENABLED_KEY = "CF_ACCESS_ENABLED"
TEAM_KEY = "CF_ACCESS_TEAM_DOMAIN"
AUD_KEY = "CF_ACCESS_AUD"
PASSWORD_KEY = "CF_ACCESS_PASSWORD_LOGIN"  # "0" = Cloudflare Access only

JWT_HEADER = "Cf-Access-Jwt-Assertion"
_TEAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.cloudflareaccess\.com$")
_KEYS_TTL = 3600
_LEEWAY = 60
log = logging.getLogger("uvicorn.error")
_keys_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class AccessError(Exception):
    """A problem with the Cloudflare Access token or setup (safe to show the administrator)."""


def normalize_team(value: str) -> str:
    v = (value or "").strip().lower()
    v = re.sub(r"^https?://", "", v).split("/")[0]
    if v and "." not in v:
        v += ".cloudflareaccess.com"
    return v


def valid_team(team: str) -> bool:
    return bool(_TEAM_RE.match(team or ""))


def settings() -> dict[str, Any]:
    return {
        "enabled": (secret_store.get_setting(ENABLED_KEY) or "0") == "1",
        "team": (secret_store.get_setting(TEAM_KEY) or "").strip(),
        "aud": (secret_store.get_setting(AUD_KEY) or "").strip(),
        "password_login": (secret_store.get_setting(PASSWORD_KEY) or "1") != "0",
    }


def disabled_by_env() -> bool:
    """Emergency switch: CF_ACCESS_DISABLE=1 in .env turns Cloudflare Access sign-in off and
    brings password login back, in case the settings ever lock the administrator out."""
    return (os.environ.get("CF_ACCESS_DISABLE") or "").strip().lower() in ("1", "true", "yes")


def is_active() -> bool:
    s = settings()
    return s["enabled"] and valid_team(s["team"]) and bool(s["aud"]) and not disabled_by_env()


def password_login_allowed() -> bool:
    return not is_active() or settings()["password_login"]


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _fetch_keys(team: str) -> dict[str, Any]:
    cached = _keys_cache.get(team)
    if cached and time.time() - cached[0] < _KEYS_TTL:
        return cached[1]
    try:
        with urllib.request.urlopen(f"https://{team}/cdn-cgi/access/certs", timeout=6) as r:
            doc = json.loads(r.read(200_000))
    except Exception as e:
        raise AccessError(f"Could not fetch Cloudflare's keys for {team}: {e}") from e
    keys = {k["kid"]: k for k in doc.get("keys", []) if k.get("kty") == "RSA" and k.get("kid")}
    if not keys:
        raise AccessError(f"{team} returned no signing keys. Check the team domain.")
    _keys_cache[team] = (time.time(), keys)
    return keys


def verify(token: str, team: str, aud: str) -> dict[str, Any]:
    """Return the verified claims of a Cloudflare Access JWT, or raise AccessError."""
    try:
        h_b64, p_b64, s_b64 = token.split(".")
        header = json.loads(_b64(h_b64))
        claims = json.loads(_b64(p_b64))
        sig = _b64(s_b64)
    except Exception:
        raise AccessError("Malformed Cloudflare Access token.")
    if header.get("alg") != "RS256":
        raise AccessError("Unsupported token algorithm.")
    jwk = _fetch_keys(team).get(header.get("kid"))
    if jwk is None:
        _keys_cache.pop(team, None)  # keys may have rotated: refetch once
        jwk = _fetch_keys(team).get(header.get("kid"))
    if jwk is None:
        raise AccessError("Token was signed by an unknown key.")
    pub = rsa.RSAPublicNumbers(
        int.from_bytes(_b64(jwk["e"]), "big"), int.from_bytes(_b64(jwk["n"]), "big"),
    ).public_key()
    try:
        pub.verify(sig, f"{h_b64}.{p_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise AccessError("Token signature is invalid.")
    now = time.time()
    if claims.get("iss") != f"https://{team}":
        raise AccessError("Token was issued by a different Cloudflare team.")
    token_aud = claims.get("aud")
    if aud not in (token_aud if isinstance(token_aud, list) else [token_aud]):
        raise AccessError("Token is for a different Access application (audience mismatch).")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - _LEEWAY:
        raise AccessError("Token has expired.")
    if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now + _LEEWAY:
        raise AccessError("Token is not valid yet.")
    return claims


def check_request(token: Optional[str]) -> tuple[Optional[str], str]:
    """(email, "") when Cloudflare Access signed the person in, else (None, why-not).
    The reason is also logged, so a refused sign-in can be diagnosed."""
    if not is_active():
        return None, ""
    if not token:
        reason = "the request carried no Cf-Access-Jwt-Assertion header (is this path behind the Access application?)"
    else:
        s = settings()
        try:
            email = str(verify(token, s["team"], s["aud"]).get("email") or "").strip().lower()
            if email:
                return email, ""
            reason = "the Access token has no email claim"
        except AccessError as e:
            reason = str(e)
    log.warning("Cloudflare Access sign-in refused: %s", reason)
    return None, reason


def verified_email(token: Optional[str]) -> Optional[str]:
    """Email of the person Cloudflare Access authenticated, or None (also when SSO is off)."""
    return check_request(token)[0]
