"""
Bootstrap settings: the few values that cannot live in the database itself.

- encryption_key     Fernet key that encrypts provider credentials stored in the DB
- token_hmac_secret  secret used to HMAC-hash API tokens
- db_backend         "sqlite" or "atlas"
- mongodb_uri/_db    Atlas connection details (only when db_backend="atlas")

They are stored in a JSON file inside the data volume, created automatically
on first start (mode 0600), so a fresh install needs no manual configuration.
An environment variable with the matching name always takes precedence, which
keeps existing .env based installs working unchanged.

The API process and the delivery workers all start together, so creation is
serialized with a file lock to avoid two processes generating different keys.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import tempfile
from typing import Any, Optional

from cryptography.fernet import Fernet

DATA_DIR = os.environ.get("DATA_DIR", "/app/data").strip() or "/app/data"
BOOTSTRAP_PATH = os.path.join(DATA_DIR, "bootstrap.json")
_LOCK_PATH = os.path.join(DATA_DIR, ".bootstrap.lock")

# bootstrap key -> environment variable that overrides it
_ENV_OVERRIDES = {
    "encryption_key": "SETTINGS_ENCRYPTION_KEY",
    "token_hmac_secret": "TOKEN_HMAC_SECRET",
    "db_backend": "DB_BACKEND",
    "mongodb_uri": "MONGODB_URI",
    "mongodb_db": "MONGODB_DB",
}

_cache: dict[str, Any] = {"mtime": None, "data": None}


def _generate_defaults() -> dict[str, Any]:
    return {
        "encryption_key": Fernet.generate_key().decode("ascii"),
        "token_hmac_secret": secrets.token_urlsafe(48),
        "db_backend": "sqlite",
        "mongodb_uri": "",
        "mongodb_db": "relay_gateway",
    }


def _write_atomic(data: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".bootstrap-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, BOOTSTRAP_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_or_create() -> dict[str, Any]:
    """Read the file, creating/upgrading it if needed. Caller must hold the lock."""
    if os.path.exists(BOOTSTRAP_PATH):
        with open(BOOTSTRAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # fill in keys added by newer versions without touching existing ones
        missing = {k: v for k, v in _generate_defaults().items() if k not in data}
        if missing:
            data.update(missing)
            _write_atomic(data)
        return data
    data = _generate_defaults()
    _write_atomic(data)
    return data


class _Lock:
    def __enter__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._f = open(_LOCK_PATH, "a+")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._f, fcntl.LOCK_UN)
        self._f.close()


def _load() -> dict[str, Any]:
    """Cached read; only takes the lock when the file is missing or changed."""
    mtime = file_version()
    if mtime is not None and _cache["mtime"] == mtime and _cache["data"] is not None:
        return _cache["data"]
    with _Lock():
        data = _read_or_create()
    _cache["mtime"] = file_version()
    _cache["data"] = data
    return data


def get(name: str) -> str:
    """Effective value: environment override first, then the bootstrap file."""
    env_name = _ENV_OVERRIDES[name]
    env_val = (os.environ.get(env_name) or "").strip()
    if env_val:
        return env_val
    return str(_load().get(name) or "").strip()


def update(**values: str) -> None:
    """Persist new bootstrap values (used by onboarding to choose the DB)."""
    unknown = set(values) - set(_ENV_OVERRIDES)
    if unknown:
        raise ValueError(f"Unknown bootstrap keys: {sorted(unknown)}")
    with _Lock():
        data = _read_or_create()
        data.update(values)
        _write_atomic(data)
    _cache["mtime"] = None


def file_version() -> Optional[int]:
    """Changes whenever the bootstrap file is rewritten (any process)."""
    try:
        return os.stat(BOOTSTRAP_PATH).st_mtime_ns
    except FileNotFoundError:
        return None


def data_is_persistent() -> bool:
    """True when the data directory is a mounted volume (so it survives the
    container being removed). False when running without one, e.g. a plain
    `docker run`: everything would be lost with the container."""
    try:
        return os.path.ismount(DATA_DIR)
    except OSError:
        return False
