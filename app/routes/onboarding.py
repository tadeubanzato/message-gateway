"""
First-run setup: choose where data is stored, then create the administrator.

That is all the unauthenticated setup does. Providers (email, SMS, push) are
connected afterwards on the Channels pages, by the logged-in administrator, so
credentials never pass through an unauthenticated endpoint.

Security model: these endpoints work only while no account exists (and, when
install.sh set MG_SETUP_TOKEN, only with that token). Once the administrator is
created every one of them returns 403, except /setup/mcp-command, which exists
specifically to hand off the MCP connection info for the account /setup/admin
just created - see its docstring. If the database is configured as Atlas but
unreachable, setup stays locked rather than open, so a temporary outage can't
be used to repoint the gateway at another database.
"""

from __future__ import annotations

import hmac
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import bootstrap
from app.db import backend_name, get_repository, reset_repository
from app.routes.portal import AccountError, _set_session_cookie, create_account
from app.services import access, secret_store
from app.services.env import public_base_url

router = APIRouter()

_PAGE = os.path.join(os.path.dirname(__file__), "..", "templates", "setup", "index.html")

# One-time handoff of the freshly created administrator's MCP connection info, for
# install.sh/an agent to run `claude mcp add` itself instead of the human copying it
# from the browser (see /setup/mcp-command below). In memory only, this process only
# - never written to disk or the database, same as the raw API token itself, which
# exists in plaintext only for the one response that creates it.
_MCP_HANDOFF_TTL_SECONDS = 600
_mcp_handoff: dict = {"data": None, "expires_at": 0.0}


def _is_complete() -> bool:
    """Setup is complete once an account (the administrator) exists."""
    try:
        return access.has_accounts()
    except Exception:
        return bool(bootstrap.get("mongodb_uri"))  # fail closed when a remote DB is configured


def _require_setup_token(request: Request) -> None:
    # install.sh sets MG_SETUP_TOKEN and prints a link ending in #token=... : setup
    # (and the MCP handoff below) then works from any computer on the network, but
    # only for whoever has that link. Without a token configured (plain
    # `docker compose up`, which listens on localhost only) it is open as before.
    expected = (os.getenv("MG_SETUP_TOKEN") or "").strip()
    if expected and not hmac.compare_digest(request.headers.get("x-setup-token", ""), expected):
        raise HTTPException(status_code=401, detail="This needs the link the installer printed (it ends in #token=...).")


def _require_open(request: Request) -> None:
    if _is_complete():
        raise HTTPException(status_code=403, detail="Setup is already complete.")
    _require_setup_token(request)


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
    _mcp_handoff.update(
        data={
            "url": f"{public_base_url(request)}/mcp",
            "headers": {"X-User-Key": account["user_key"], "X-API-Token": raw_token},
        },
        expires_at=time.monotonic() + _MCP_HANDOFF_TTL_SECONDS,
    )
    resp = JSONResponse({
        "ok": True, "user_key": account["user_key"], "raw_token": raw_token, "next": "/gateway",
    })
    _set_session_cookie(resp, sid)
    return resp


@router.get("/setup/mcp-command", include_in_schema=False)
def setup_mcp_command(request: Request):
    """One-time handoff of the administrator's MCP connection info, right after
    /setup/admin creates the account - so install.sh/an agent can run
    `claude mcp add` itself instead of the human copying the command from the
    browser. Returns 404 once claimed, after 10 minutes unclaimed, or if no account
    was just created in this process: this is a handoff for the moment setup
    finishes, not a standing way to fetch a key."""
    _require_setup_token(request)
    data = _mcp_handoff["data"]
    if data is None or time.monotonic() > _mcp_handoff["expires_at"]:
        raise HTTPException(status_code=404, detail="Nothing to hand off. Get the command from the Keys page instead.")
    _mcp_handoff.update(data=None, expires_at=0.0)  # one-time: gone after this read
    return {"ok": True, **data}
