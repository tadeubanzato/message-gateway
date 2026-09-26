"""
Data-access layer, backend-selectable: sqlite (default) or atlas.

The choice comes from app.bootstrap (DB_BACKEND env override, otherwise the
bootstrap file written by onboarding). Both backends expose the same
document-shaped repository interface (see base.py) so the rest of the app
(auth, portal routes, message/attempt logging) never needs to know which one
is active.

The repository is rebuilt automatically if the configuration changes, so a
backend switch made during onboarding reaches every process (API and workers)
without a restart.
"""

from __future__ import annotations

from app import bootstrap
from app.db.base import Repository

_repo: Repository | None = None
_repo_key: tuple[str, str, str] | None = None


def _config_key() -> tuple[str, str, str]:
    backend = (bootstrap.get("db_backend") or "sqlite").lower()
    return backend, bootstrap.get("mongodb_uri"), bootstrap.get("mongodb_db")


def get_repository() -> Repository:
    """Return the process-wide repository for the currently configured backend."""
    global _repo, _repo_key
    key = _config_key()
    if _repo is not None and _repo_key == key:
        return _repo

    backend, uri, db_name = key
    if backend == "atlas":
        from app.db.atlas_repository import AtlasRepository

        repo: Repository = AtlasRepository(uri, db_name)
    elif backend == "sqlite":
        from app.db.sqlite_repository import SqliteRepository

        repo = SqliteRepository()
    else:
        raise SystemExit(f"Unsupported db_backend={backend!r}. Use 'sqlite' or 'atlas'.")

    repo.ensure_indexes()
    _repo, _repo_key = repo, key
    return repo


def reset_repository() -> None:
    global _repo, _repo_key
    _repo, _repo_key = None, None


def backend_name() -> str:
    return _config_key()[0]
