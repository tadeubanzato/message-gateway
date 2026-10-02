"""
Detect keys that don't match what the database was written with.

Two secrets live outside the database, in the data volume's bootstrap.json (or the
environment): the key that encrypts saved settings, and the secret that hashes API
tokens. If the volume is replaced while the database is kept, or a second install
shares the database with its own keys, the saved credentials can't be decrypted and
old API tokens stop working. Nothing used to say so; this does.

- Settings: any stored setting that doesn't decrypt with the current key.
- API tokens: a fingerprint of the token secret is stored in the database the first
  time it's seen; a different secret later means older tokens are invalid.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Any

from app import bootstrap
from app.services import secret_store

log = logging.getLogger("gateway.keys")

TOKEN_CHECK = "_TOKEN_SECRET_CHECK"       # internal rows start with "_"
_CACHE_SECONDS = 20.0
_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "value": None}


def fingerprint(value: str) -> str:
    """Short, non-reversible label to compare keys across installs."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:8]


def _token_check_value() -> str:
    secret = bootstrap.get("token_hmac_secret").encode("utf-8")
    return hmac.new(secret, b"message-gateway-token-check", hashlib.sha256).hexdigest()


def _pinned(env_name: str) -> bool:
    return bool((os.environ.get(env_name) or "").strip())


def _scan() -> dict[str, Any]:
    from app.db import get_repository

    rows = get_repository().list_settings()
    user_rows = {k: v for k, v in rows.items() if not k.startswith("_")}
    unreadable = sorted(k for k, d in user_rows.items() if secret_store.decrypt(d.get("value_enc", "")) is None)

    token_mismatch = False
    if len(unreadable) < len(user_rows) or not user_rows:   # the encryption key works, so the stored check is readable
        stored = secret_store.get_setting(TOKEN_CHECK)
        expected = _token_check_value()
        if stored is None:
            try:
                secret_store.set_setting(TOKEN_CHECK, expected)
            except Exception:
                pass
        elif stored != expected:
            token_mismatch = True

    return {
        "unreadable": len(unreadable), "unreadable_names": unreadable, "token_mismatch": token_mismatch,
        "encryption_key": fingerprint(bootstrap.get("encryption_key")),
        "token_secret": fingerprint(bootstrap.get("token_hmac_secret")),
        "encryption_key_pinned": _pinned("SETTINGS_ENCRYPTION_KEY"),
        "token_secret_pinned": _pinned("TOKEN_HMAC_SECRET"),
    }


def status(force: bool = False) -> dict[str, Any]:
    """Cached for a few seconds: this runs on page loads. Never raises."""
    now = time.monotonic()
    with _lock:
        if not force and _cache["value"] is not None and now - _cache["at"] < _CACHE_SECONDS:
            return _cache["value"]
    try:
        value = _scan()
    except Exception:
        value = {"unreadable": 0, "unreadable_names": [], "token_mismatch": False,
                 "encryption_key": "", "token_secret": "", "encryption_key_pinned": False, "token_secret_pinned": False}
    with _lock:
        _cache.update(at=now, value=value)
    return value


def has_problem(s: dict[str, Any]) -> bool:
    return bool(s["unreadable"] or s["token_mismatch"])


def purge_unreadable() -> int:
    """Delete stored settings that can't be decrypted (the admin chose to re-enter them)."""
    from app.db import get_repository

    names = status(force=True)["unreadable_names"]
    repo = get_repository()
    for n in names:
        repo.delete_setting(n)
    secret_store.invalidate()
    status(force=True)
    return len(names)


def accept_token_secret() -> None:
    """Record the current token secret as the expected one (older tokens stay invalid)."""
    secret_store.set_setting(TOKEN_CHECK, _token_check_value())
    status(force=True)


def log_startup() -> None:
    s = status(force=True)
    if s["unreadable"]:
        log.warning(
            "%d saved setting(s) can't be decrypted with this install's encryption key (%s): %s. The data volume "
            "was probably replaced, or another install with a different key shares this database. Pin the right "
            "SETTINGS_ENCRYPTION_KEY in .env, or enter the credentials again (Settings > Encryption keys).",
            s["unreadable"], s["encryption_key"], ", ".join(s["unreadable_names"][:8]),
        )
    if s["token_mismatch"]:
        log.warning(
            "This install's token secret (%s) differs from the one the database's API tokens were created with, so "
            "existing API tokens will be rejected. Pin the original TOKEN_HMAC_SECRET in .env, or create new API keys.",
            s["token_secret"],
        )
