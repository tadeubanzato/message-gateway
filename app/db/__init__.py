"""
Data-access layer, backend-selectable via DB_BACKEND=sqlite|atlas.

Both backends expose the same document-shaped repository interface (see base.py)
so the rest of the app (auth, portal routes, message/attempt logging) never
needs to know which one is active.
"""

from __future__ import annotations

import os

from app.db.base import Repository

_BACKEND = os.environ.get("DB_BACKEND", "sqlite").strip().lower() or "sqlite"

_repo: Repository | None = None


def get_repository() -> Repository:
    """Return the process-wide singleton repository for the configured backend."""
    global _repo
    if _repo is not None:
        return _repo

    if _BACKEND == "atlas":
        from app.db.atlas_repository import AtlasRepository

        _repo = AtlasRepository()
    elif _BACKEND == "sqlite":
        from app.db.sqlite_repository import SqliteRepository

        _repo = SqliteRepository()
    else:
        raise SystemExit(
            f"Unsupported DB_BACKEND={_BACKEND!r}. Use 'sqlite' or 'atlas'."
        )

    _repo.ensure_indexes()
    return _repo


def backend_name() -> str:
    return _BACKEND
