"""
First-run onboarding: pick a database, enter provider credentials, send a test.

Credentials entered here are stored encrypted in the database (see
app.services.secret_store), so a fresh install needs no .env editing.

Security model: these endpoints are open only while setup is incomplete (a fresh
install, reachable from localhost by default). Once /setup/finish is called they
all return 403. Stored secret values are never returned — only which settings
are configured.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import bootstrap
from app.db import backend_name, get_repository, reset_repository
from app.services import channels, secret_store

router = APIRouter()

SETUP_COMPLETE_KEY = "SETUP_COMPLETE"
_PAGE = os.path.join(os.path.dirname(__file__), "..", "templates", "setup", "index.html")

def _is_complete() -> bool:
    try:
        return secret_store.get_setting(SETUP_COMPLETE_KEY) == "1"
    except Exception:
        return False


def _require_open() -> None:
    if _is_complete():
        raise HTTPException(status_code=403, detail="Setup is already complete.")


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------
@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/gateway/login" if _is_complete() else "/setup", status_code=307)


@router.get("/setup", include_in_schema=False)
def setup_page():
    if _is_complete():
        return RedirectResponse("/gateway/login", status_code=307)
    return FileResponse(_PAGE, media_type="text/html")


# ---------------------------------------------------------------------
# JSON API used by the setup page
# ---------------------------------------------------------------------
@router.get("/setup/state", include_in_schema=False)
def setup_state():
    _require_open()
    try:
        db_ok = get_repository().ping()
    except Exception:
        db_ok = False
    configured = set(secret_store.stored_setting_names()) if db_ok else set()
    catalog = channels.public_catalog()
    for ch, spec in channels.CATALOG.items():
        catalog[ch]["selected"] = secret_store.get_setting(spec["selector"]) if db_ok else None
    return {
        "backend": backend_name(),
        "db_ok": db_ok,
        "mongodb_db": bootstrap.get("mongodb_db"),
        "configured_settings": sorted(configured),
        "catalog": catalog,
    }


class DatabaseChoice(BaseModel):
    backend: str = Field(pattern="^(sqlite|atlas)$")
    mongodb_uri: Optional[str] = None
    mongodb_db: Optional[str] = "relay_gateway"


@router.post("/setup/database", include_in_schema=False)
def setup_database(body: DatabaseChoice):
    _require_open()
    if body.backend == "atlas":
        from app.db.atlas_repository import test_connection

        ok, msg = test_connection(body.mongodb_uri or "", body.mongodb_db or "")
        if not ok:
            return {"ok": False, "error": msg}
        bootstrap.update(
            db_backend="atlas",
            mongodb_uri=(body.mongodb_uri or "").strip(),
            mongodb_db=(body.mongodb_db or "").strip(),
        )
    else:
        bootstrap.update(db_backend="sqlite")
    reset_repository()
    secret_store.invalidate()
    try:
        ok = get_repository().ping()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Database not reachable ({type(e).__name__})."}
    return {"ok": ok, "backend": backend_name(), "already_set_up": _is_complete()}


class ProviderChoice(BaseModel):
    channel: str
    provider: str
    values: dict[str, str] = Field(default_factory=dict)


@router.post("/setup/provider", include_in_schema=False)
def setup_provider(body: ProviderChoice):
    _require_open()
    return channels.apply_provider(body.channel, body.provider, body.values)


class TestSend(BaseModel):
    channel: str
    to: Optional[str] = None


@router.post("/setup/test", include_in_schema=False)
def setup_test(body: TestSend):
    _require_open()
    return channels.send_test(body.channel, body.to)


class Finish(BaseModel):
    store_content: bool = True


@router.post("/setup/finish", include_in_schema=False)
def setup_finish(body: Finish):
    _require_open()
    secret_store.set_setting("STORE_MESSAGE_CONTENT", "1" if body.store_content else "0")
    secret_store.set_setting(SETUP_COMPLETE_KEY, "1")
    return {"ok": True, "next": "/gateway/register"}
