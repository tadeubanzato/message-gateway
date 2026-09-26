"""
Encrypted settings stored in the database (provider credentials and choices).

Values are encrypted with a Fernet key from app.bootstrap before they reach the
database, so DB exports, backups and Atlas never contain plaintext credentials.

Reads go through a short in-memory cache. The API process and each delivery
worker are separate processes, so a change made in onboarding reaches the
workers within CACHE_TTL_SECONDS without any restart.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app import bootstrap

CACHE_TTL_SECONDS = 5.0

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "data": {}, "backend": None, "version": None}


def _fernet() -> Fernet:
    return Fernet(bootstrap.get("encryption_key").encode("ascii"))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> Optional[str]:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0


def _load() -> dict[str, str]:
    from app.db import get_repository

    rows = get_repository().list_settings()
    out: dict[str, str] = {}
    for key, doc in rows.items():
        plain = decrypt(doc.get("value_enc", ""))
        if plain is not None:
            out[key] = plain
    return out


def get_setting(name: str) -> Optional[str]:
    """Decrypted value for a stored setting, or None. Never raises: if the
    database is unreachable, callers fall back to environment/defaults."""
    now = time.monotonic()
    version = bootstrap.file_version()
    with _lock:
        fresh = (
            now - _cache["at"] < CACHE_TTL_SECONDS and _cache["version"] == version
        )
        if fresh:
            return _cache["data"].get(name)
    try:
        data = _load()
    except Exception:
        return None
    with _lock:
        _cache.update(at=now, data=data, version=version)
    return data.get(name)


def set_setting(name: str, value: str) -> None:
    from app.db import get_repository

    get_repository().set_setting(name, encrypt(value))
    invalidate()


def set_settings(values: dict[str, str]) -> None:
    for k, v in values.items():
        if v is not None and str(v).strip() != "":
            set_setting(k, str(v).strip())


def delete_setting(name: str) -> None:
    from app.db import get_repository

    get_repository().delete_setting(name)
    invalidate()


def stored_setting_names() -> list[str]:
    """Names only, never values — for showing 'configured' state in the UI."""
    try:
        return sorted(_load().keys())
    except Exception:
        return []


def unreadable_count() -> int:
    """Stored settings that can't be decrypted with the current key. Non-zero means
    the encryption key changed (for example the data volume was replaced while the
    database was kept), so those credentials must be entered again."""
    try:
        from app.db import get_repository

        rows = get_repository().list_settings()
        return sum(1 for doc in rows.values() if decrypt(doc.get("value_enc", "")) is None)
    except Exception:
        return 0
