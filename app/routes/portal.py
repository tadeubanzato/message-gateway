"""
Portal routes: login/register/account/token management.

Adapted from the old codebase's routes/gateway_portal.py to use the
Repository interface instead of importing a Mongo driver directly, so this
works unchanged against either DB_BACKEND.

Preserves the raw-token-never-persisted security fix from the old codebase:
raw tokens are shown exactly once (via token_once.html) at creation/rotation
time and never written to the database.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER
from starlette.templating import Jinja2Templates

from app import bootstrap
from app.db import get_repository
from app.db.base import DuplicateEmailError, DuplicateUserKeyError
from app.services import access, message_log
from app.services.auth_passwords import hash_password, verify_password
from app.services.auth_tokens import generate_raw_token, hmac_token_hash_hex, new_user_key, token_last4

router = APIRouter()

TEMPLATES_DIR = os.environ.get("TEMPLATES_DIR", "/app/app/templates").strip() or "/app/app/templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

SESSION_COOKIE_NAME = (os.environ.get("PORTAL_SESSION_COOKIE", "portal_sid") or "portal_sid").strip()
SESSION_TTL_HOURS = int((os.environ.get("PORTAL_SESSION_TTL_HOURS", "24") or "24").strip())
TEST_USER_KEY = (os.environ.get("PORTAL_TEST_USER_KEY", "") or "").strip()

DEFAULT_TOKEN_APP = "default"


def _repo():
    return get_repository()


def page_ctx(request: Request, account: dict[str, Any], active: str = "", **extra: Any) -> dict[str, Any]:
    """Variables every logged-in page needs for the shared layout/navbar."""
    return {
        "request": request, "active": active,
        "user_name": account.get("name", ""), "user_email": account.get("email", ""),
        "user_key": account.get("user_key", ""), "is_owner": access.is_owner(account),
        "base_url": str(request.base_url).rstrip("/"),
        "warn_not_persistent": not bootstrap.data_is_persistent(),
        **extra,
    }


def _login_ctx(request: Request, **extra: Any) -> dict[str, Any]:
    return {
        "request": request, "test_user_key_set": bool(TEST_USER_KEY), "test_user_key": TEST_USER_KEY,
        "signups_open": access.signups_open(), **extra,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt_utc(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v.strip():
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    return None


def _iso(v: Any) -> str:
    dt = _dt_utc(v)
    return dt.isoformat() if dt else ""


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=HTTP_303_SEE_OTHER)


def _set_session_cookie(resp, sid: str) -> None:
    resp.set_cookie(
        key=SESSION_COOKIE_NAME, value=sid, httponly=True, secure=False,
        samesite="lax", path="/", max_age=int(SESSION_TTL_HOURS * 3600),
    )


def _clear_session_cookie(resp) -> None:
    resp.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def _normalize_app(app: Any) -> str:
    a = (str(app or "")).strip().lower()
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")
    cleaned = "".join(c for c in a if c in allowed).strip("._-")
    return cleaned[:40] if cleaned else DEFAULT_TOKEN_APP


def _create_session(account_id: str) -> str:
    sid = "ps_" + secrets.token_urlsafe(32)
    now = _utcnow()
    _repo().create_session({
        "session_id": sid, "account_id": account_id,
        "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat(),
    })
    return sid


def _load_account_from_session(request: Request) -> Optional[dict[str, Any]]:
    sid = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not sid:
        return None
    sess = _repo().find_session(sid)
    if not sess:
        return None
    expires_at = _dt_utc(sess.get("expires_at"))
    if not expires_at or expires_at <= _utcnow():
        _repo().delete_session(sid)
        return None
    acct_id = sess.get("account_id")
    if not acct_id:
        _repo().delete_session(sid)
        return None
    acct = _repo().find_account_by_id(str(acct_id))
    if not acct:
        _repo().delete_session(sid)
        return None
    return acct


def _require_account_or_redirect(request: Request) -> Tuple[Optional[RedirectResponse], Optional[dict[str, Any]]]:
    acct = _load_account_from_session(request)
    if not acct:
        resp = _redirect("/gateway/login")
        _clear_session_cookie(resp)
        return resp, None
    return None, acct


def _create_token_doc(raw: str, app: str) -> dict[str, Any]:
    return {
        "token_id": secrets.token_hex(8), "app": app,
        "token_hash": hmac_token_hash_hex(raw), "last4": token_last4(raw),
        "created_at": _utcnow().isoformat(), "revoked_at": None,
    }


def _get_active_apps(account: dict[str, Any]) -> Dict[str, dict[str, Any]]:
    svc = ((account.get("services") or {}).get("message-gateway") or {})
    tokens = svc.get("tokens") or []
    apps: Dict[str, dict[str, Any]] = {}
    for t in tokens:
        if not isinstance(t, dict) or t.get("revoked_at") not in (None, ""):
            continue
        app = _normalize_app(t.get("app") or DEFAULT_TOKEN_APP)
        created = _dt_utc(t.get("created_at")) or datetime(1970, 1, 1, tzinfo=timezone.utc)
        prev = apps.get(app)
        if not prev or created > (_dt_utc(prev.get("created_at")) or datetime(1970, 1, 1, tzinfo=timezone.utc)):
            apps[app] = t
    return apps


# ---------------------------------------------------------------------
# LOGIN / LOGOUT
# ---------------------------------------------------------------------
@router.get("/gateway/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not access.has_accounts():
        return _redirect("/setup")  # fresh install: create the administrator in setup first
    return templates.TemplateResponse("gateway/login.html", _login_ctx(request))


@router.post("/gateway/login")
async def login_submit(request: Request):
    form = await request.form()
    email = str(form.get("email") or "").strip().lower()
    password = str(form.get("password") or "").strip()

    if not email or "@" not in email:
        return templates.TemplateResponse(
            "gateway/login.html", _login_ctx(request, error="Valid email is required.", email=email), status_code=400,
        )

    account = _repo().find_account_by_email(email)
    if not account or not verify_password(password, account.get("password_hash", "")):
        return templates.TemplateResponse(
            "gateway/login.html", _login_ctx(request, error="Invalid email or password.", email=email), status_code=401,
        )

    sid = _create_session(str(account["_id"]))
    resp = _redirect("/gateway")
    _set_session_cookie(resp, sid)
    return resp


@router.post("/gateway/login/test")
def login_as_test_user(request: Request):
    if not TEST_USER_KEY:
        return templates.TemplateResponse(
            "gateway/login.html",
            {"request": request, "test_user_key_set": False, "error": "PORTAL_TEST_USER_KEY is not set."},
            status_code=500,
        )
    account = _repo().find_account_by_user_key(TEST_USER_KEY)
    if not account:
        return templates.TemplateResponse(
            "gateway/login.html",
            {"request": request, "test_user_key_set": True, "test_user_key": TEST_USER_KEY,
             "error": f"No account found for user_key={TEST_USER_KEY}."},
            status_code=404,
        )
    sid = _create_session(str(account["_id"]))
    resp = _redirect("/gateway")
    _set_session_cookie(resp, sid)
    return resp


@router.post("/gateway/logout")
def logout(request: Request):
    sid = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if sid:
        _repo().delete_session(sid)
    resp = _redirect("/gateway/login")
    _clear_session_cookie(resp)
    return resp


# ---------------------------------------------------------------------
# REGISTER
# ---------------------------------------------------------------------
class AccountError(Exception):
    """A validation problem with an account being created (safe to show the user)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def create_account(name: str, email: str, password: str, password2: str, *, make_admin: bool) -> tuple[dict[str, Any], str, str]:
    """Validate and create an account with its first API key and a login session.
    Returns (account, raw_token, session_id). Used by the sign-up form and by the
    first-run setup, so both apply the same rules."""
    name = (name or "").strip()
    email = (email or "").strip().lower()
    password = (password or "").strip()
    if not name:
        raise AccountError("Name is required.")
    if not email or "@" not in email:
        raise AccountError("Valid email is required.")
    if not password:
        raise AccountError("Password is required.")
    if password != (password2 or "").strip():
        raise AccountError("Passwords do not match.")
    try:
        pw_hash = hash_password(password)
    except ValueError as e:
        raise AccountError(str(e))

    repo = _repo()
    last_err: Optional[Exception] = None
    for _ in range(10):
        user_key = new_user_key()
        doc = {
            "name": name, "email": email, "user_key": user_key, "password_hash": pw_hash,
            "services": {"message-gateway": {"service": "message-gateway", "tokens": []}},
            "created_at": _utcnow().isoformat(),
        }
        try:
            account_id = repo.insert_account(doc)
        except DuplicateEmailError:
            raise AccountError("That email is already registered.", 409)
        except DuplicateUserKeyError:
            continue
        except Exception as e:  # noqa: BLE001
            last_err = e
            break
        if make_admin:
            access.claim_owner(account_id)
        raw = generate_raw_token()
        repo.push_token(account_id, _create_token_doc(raw, DEFAULT_TOKEN_APP))
        return {**doc, "_id": account_id}, raw, _create_session(account_id)
    raise AccountError("Failed to create account." + (f" ({last_err})" if last_err else ""), 500)


@router.get("/gateway/register", response_class=HTMLResponse)
def register_page(request: Request):
    if not access.has_accounts():
        return _redirect("/setup")  # the first (administrator) account is created in setup
    if not access.signups_open():
        return templates.TemplateResponse("gateway/register.html", {"request": request, "closed": True}, status_code=403)
    return templates.TemplateResponse("gateway/register.html", {"request": request})


@router.post("/gateway/register", response_class=HTMLResponse)
async def register_submit(request: Request):
    if not access.has_accounts():
        return _redirect("/setup")
    if not access.signups_open():
        return templates.TemplateResponse("gateway/register.html", {"request": request, "closed": True}, status_code=403)
    form = await request.form()
    try:
        account, raw, sid = create_account(
            str(form.get("name") or ""), str(form.get("email") or ""),
            str(form.get("password") or ""), str(form.get("password2") or ""), make_admin=False,
        )
    except AccountError as e:
        return templates.TemplateResponse(
            "gateway/register.html",
            {"request": request, "error": e.message, "name": str(form.get("name") or ""), "email": str(form.get("email") or "")},
            status_code=e.status,
        )
    resp = templates.TemplateResponse(
        "gateway/token_once.html",
        page_ctx(request, account, "keys", warning="Here are the credentials for your first key, named 'default'.", raw_token=raw),
    )
    _set_session_cookie(resp, sid)
    return resp


@router.post("/gateway/services/message-gateway/tokens")
async def create_or_rotate_token(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None

    form = await request.form()
    app = _normalize_app(form.get("app"))
    account_id = str(account["_id"])

    _repo().revoke_tokens(account_id, lambda t: _normalize_app(t.get("app") or DEFAULT_TOKEN_APP) == app)

    raw = generate_raw_token()
    _repo().push_token(account_id, _create_token_doc(raw, app))

    return templates.TemplateResponse(
        "gateway/token_once.html",
        page_ctx(request, account, "keys", warning=f"Here is the new token for the key named '{app}'.", raw_token=raw),
    )


@router.post("/gateway/services/message-gateway/apps/revoke")
async def revoke_application(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None

    form = await request.form()
    app = _normalize_app(form.get("app"))
    account_id = str(account["_id"])
    _repo().revoke_tokens(account_id, lambda t: _normalize_app(t.get("app") or DEFAULT_TOKEN_APP) == app)

    return _redirect("/gateway/keys")
