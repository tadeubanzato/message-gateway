"""
First-run setup: choose where data is stored, then create the administrator.

That is all the unauthenticated setup does. Providers (email, SMS, push) are
connected afterwards on the Channels pages, by the logged-in administrator, so
credentials never pass through an unauthenticated endpoint.

Security model: these endpoints work only while no account exists (and, when
install.sh set MG_SETUP_TOKEN, only with that token). Once the
administrator is created every one of them returns 403. If the database is
configured as Atlas but unreachable, setup stays locked rather than open, so a
temporary outage can't be used to repoint the gateway at another database.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import bootstrap
from app.db import backend_name, get_repository, reset_repository
from app.routes.portal import AccountError, _set_session_cookie, create_account
from app.services import access, secret_store

router = APIRouter()

_PAGE = os.path.join(os.path.dirname(__file__), "..", "templates", "setup", "index.html")


def _is_complete() -> bool:
    """Setup is complete once an account (the administrator) exists."""
    try:
        return access.has_accounts()
    except Exception:
        return bool(bootstrap.get("mongodb_uri"))  # fail closed when a remote DB is configured


def _require_open(request: Request) -> None:
    if _is_complete():
        raise HTTPException(status_code=403, detail="Setup is already complete.")
    # install.sh sets MG_SETUP_TOKEN and prints a link ending in #token=... : the
    # setup page then works from any computer on the network, but only for whoever
    # has that link. Without a token configured (plain `docker compose up`, which
    # listens on localhost only) setup is open as before.
    expected = (os.getenv("MG_SETUP_TOKEN") or "").strip()
    if expected and not hmac.compare_digest(request.headers.get("x-setup-token", ""), expected):
        raise HTTPException(status_code=401, detail="This setup page needs the link the installer printed (it ends in #token=...).")


# ---------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------
@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/gateway" if _is_complete() else "/setup", status_code=307)


@router.get("/setup", include_in_schema=False)
def setup_page():
    if _is_complete():
        return RedirectResponse("/gateway", status_code=307)
    return FileResponse(_PAGE, media_type="text/html")


# ---------------------------------------------------------------------
# JSON API used by the setup page
# ---------------------------------------------------------------------
@router.get("/setup/state", include_in_schema=False)
def setup_state(request: Request):
    _require_open(request)
    try:
        db_ok = get_repository().ping()
    except Exception:
        db_ok = False
    return {"backend": backend_name(), "db_ok": db_ok, "mongodb_db": bootstrap.get("mongodb_db")}


class DatabaseChoice(BaseModel):
    backend: str = Field(pattern="^(sqlite|atlas)$")
    mongodb_uri: Optional[str] = None
    mongodb_db: Optional[str] = "relay_gateway"


@router.post("/setup/database", include_in_schema=False)
def setup_database(body: DatabaseChoice, request: Request):
    _require_open(request)
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
    # An existing gateway database (accounts already there) means we are just
    # reconnecting to it: nothing more to set up.
    return {"ok": ok, "backend": backend_name(), "already_set_up": _is_complete()}


class AdminBody(BaseModel):
    name: str
    email: str
    password: str
    password2: str
    store_content: bool = True


@router.post("/setup/admin", include_in_schema=False)
def setup_admin(body: AdminBody, request: Request):
    _require_open(request)
    try:
        account, raw_token, sid = create_account(
            body.name, body.email, body.password, body.password2, make_admin=True
        )
    except AccountError as e:
        return JSONResponse({"ok": False, "error": e.message}, status_code=e.status)
    secret_store.set_setting("STORE_MESSAGE_CONTENT", "1" if body.store_content else "0")
    resp = JSONResponse({
        "ok": True, "user_key": account["user_key"], "raw_token": raw_token, "next": "/gateway",
    })
    _set_session_cookie(resp, sid)
    return resp
