"""
Change where the gateway keeps its data (Settings > Database): local SQLite or MongoDB Atlas,
or a different Atlas cluster/database.

Moving is a copy, not just a switch: accounts, saved settings (still encrypted with the same
key), message history and the current login session are copied into the new database, the copy
is verified, and only then does the gateway start using it. The old database is never modified
or deleted. If the target already holds a gateway's data we refuse to mix the two; the caller
may instead switch to that existing data (which signs everyone out).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from app import bootstrap
from app.db import backend_name, get_repository, reset_repository
from app.db.base import Repository
from app.services import secret_store

VERIFY_TABLES = ("accounts", "messages", "attempts", "settings")


def _make_repo(backend: str, uri: str, db_name: str) -> Repository:
    if backend == "atlas":
        from app.db.atlas_repository import AtlasRepository

        return AtlasRepository(uri, db_name)
    from app.db.sqlite_repository import SqliteRepository

    return SqliteRepository()


def _is_current(backend: str, uri: str, db_name: str) -> bool:
    if backend != backend_name():
        return False
    return backend == "sqlite" or (uri == bootstrap.get("mongodb_uri") and db_name == bootstrap.get("mongodb_db"))


def describe_current() -> dict[str, Any]:
    """What the Settings page shows about the current database (never the full connection string)."""
    backend = backend_name()
    uri = bootstrap.get("mongodb_uri")
    host = urlparse(uri.replace("mongodb+srv://", "https://").replace("mongodb://", "https://")).hostname if uri else None
    tail = uri[-4:] if len(uri) >= 12 else None
    return {
        "backend": backend, "db_name": bootstrap.get("mongodb_db") if backend == "atlas" else None,
        "db_host": host if backend == "atlas" else None, "db_tail": tail if backend == "atlas" else None,
        "locked_by": bootstrap.db_env_locked(),
    }


def _validate(backend: str, uri: str, db_name: str) -> Optional[str]:
    if backend not in ("sqlite", "atlas"):
        return "Choose SQLite or MongoDB Atlas."
    if backend == "atlas":
        from app.db.atlas_repository import test_connection

        ok, msg = test_connection(uri, db_name)
        if not ok:
            return msg
    return None


def check_target(backend: str, uri: str, db_name: str) -> dict[str, Any]:
    """Can we reach the target, and does it already hold a gateway's data?"""
    uri, db_name = (uri or "").strip(), (db_name or "").strip()
    if bootstrap.db_env_locked():
        return {"ok": False, "error": "The database is set by an environment variable (" + ", ".join(bootstrap.db_env_locked()) + "). Remove it from your .env file and recreate the container to change it here."}
    if _is_current(backend, uri, db_name):
        return {"ok": False, "error": "That is the database you're already using."}
    err = _validate(backend, uri, db_name)
    if err:
        return {"ok": False, "error": err}
    try:
        n = _make_repo(backend, uri, db_name).count_accounts()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not read that database ({type(e).__name__})."}
    return {"ok": True, "has_data": n > 0, "accounts": n}


def switch_database(backend: str, uri: str, db_name: str, copy: bool, use_existing: bool) -> dict[str, Any]:
    uri, db_name = (uri or "").strip(), (db_name or "").strip()
    checked = check_target(backend, uri, db_name)
    if not checked["ok"]:
        return checked
    target = _make_repo(backend, uri, db_name)
    target.ensure_indexes()
    current = get_repository()

    copied: dict[str, int] = {}
    if checked["has_data"]:
        if not use_existing:
            return {"ok": False, "needs_confirm": True, "error": (
                f"That database already contains a gateway ({checked['accounts']} account"
                f"{'' if checked['accounts'] == 1 else 's'}). Your data won't be copied into it. "
                "You can switch to it anyway, and you'll use its accounts and settings instead.")}
    elif copy:
        data = current.export_data()
        target.import_data(data)
        want, got = current.counts(), target.counts()
        bad = [t for t in VERIFY_TABLES if got.get(t) != want.get(t)]
        if bad:
            return {"ok": False, "error": "The copy didn't match the original (" + ", ".join(bad) + "), so nothing was changed. Your current database is untouched."}
        copied = {t: got[t] for t in VERIFY_TABLES}

    previous = {"db_backend": bootstrap.get("db_backend"), "mongodb_uri": bootstrap.get("mongodb_uri"), "mongodb_db": bootstrap.get("mongodb_db")}
    bootstrap.update(db_backend=backend, mongodb_uri=uri if backend == "atlas" else "",
                     mongodb_db=db_name if backend == "atlas" else (previous["mongodb_db"] or "relay_gateway"))
    reset_repository(); secret_store.invalidate()
    try:
        if not get_repository().ping():
            raise RuntimeError("no ping")
    except Exception:
        bootstrap.update(**previous); reset_repository(); secret_store.invalidate()
        return {"ok": False, "error": "The new database didn't respond, so the gateway went back to the previous one."}
    try:
        from app.services import access
        access.ensure_roles()
    except Exception:
        pass
    return {"ok": True, "backend": backend_name(), "copied": copied,
            "signed_out": bool(checked["has_data"])}
