"""
Portal pages after login: Home, Message log, API keys, Settings, About, Account.

All pages share gateway/base.html (navbar + /static/app.css). Settings is
administrator-only, and every change to credentials or general options requires
the administrator's password again.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.db import backend_name, get_repository
from app.routes.portal import (
    _get_active_apps, _iso, _redirect, _require_account_or_redirect,
    _load_account_from_session, page_ctx, templates,
)
from app.services import access, channels, message_log, secret_store
from app.services.auth_passwords import hash_password, verify_password
from app.version import APP_VERSION

router = APIRouter()


def _page(request: Request, template: str, active: str, **extra: Any):
    """Render a logged-in page, or redirect to login."""
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    return templates.TemplateResponse(template, page_ctx(request, account, active, **extra))


# ---------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------
@router.get("/gateway", response_class=HTMLResponse)
def home(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    docs = get_repository().list_messages(None, None, 100, account_id=str(account["_id"]))
    counts = {"delivered": 0, "failed": 0, "queued": 0}
    for d in docs:
        counts[d.get("status", "queued")] = counts.get(d.get("status", "queued"), 0) + 1
    return templates.TemplateResponse("gateway/home.html", page_ctx(
        request, account, "home",
        channels=channels.all_status(),
        recent=[message_log.public_view(d, include_content=False) for d in docs[:5]],
        recent_total=len(docs), counts=counts,
    ))


# ---------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------
@router.get("/gateway/keys", response_class=HTMLResponse)
def keys_page(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    apps = [
        {"app": app, "last4": t.get("last4") or "", "created_at": _iso(t.get("created_at"))}
        for app, t in sorted(_get_active_apps(account).items())
    ]
    return templates.TemplateResponse("gateway/keys.html", page_ctx(request, account, "keys", active_apps=apps))


# ---------------------------------------------------------------------
# Message log
# ---------------------------------------------------------------------
@router.get("/gateway/messages", response_class=HTMLResponse)
def messages_page(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    qp = request.query_params
    channel = (qp.get("channel") or "").strip() or None
    status = (qp.get("status") or "").strip() or None
    q = (qp.get("q") or "").strip()
    try:
        limit = max(100, min(int(qp.get("limit") or 100), 1000))
    except ValueError:
        limit = 100

    docs = get_repository().list_messages(channel, status, 1000 if q else limit + 1, account_id=str(account["_id"]))
    msgs = [message_log.public_view(d) for d in docs]
    if q:
        needle = q.lower()
        msgs = [
            m for m in msgs
            if any(needle in str(m.get(k) or "").lower() for k in ("to", "subject", "body", "message_id"))
        ]
    more = len(msgs) > limit
    return templates.TemplateResponse("gateway/messages.html", page_ctx(
        request, account, "messages", messages=msgs[:limit], more=more, limit=limit,
        q=q, channel=channel or "", status=status or "",
        content_enabled=message_log.store_content_enabled(),
    ))


@router.get("/gateway/messages/{message_id}", response_class=HTMLResponse)
def message_detail(request: Request, message_id: str):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    repo = get_repository()
    doc = repo.get_message(message_id)
    if not doc or doc.get("account_id") != str(account["_id"]):
        raise HTTPException(status_code=404, detail="Message not found")
    attempts = sorted(repo.list_attempts(message_id), key=lambda a: a.get("attempt_number") or 0)
    return templates.TemplateResponse("gateway/message_detail.html", page_ctx(
        request, account, "messages", m=message_log.public_view(doc), attempts=attempts,
    ))


# ---------------------------------------------------------------------
# About
# ---------------------------------------------------------------------
@router.get("/gateway/about", response_class=HTMLResponse)
def about(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    try:
        db_ok = get_repository().ping()
    except Exception:
        db_ok = False
    base_url = str(request.base_url).rstrip("/")
    return templates.TemplateResponse("gateway/about.html", page_ctx(
        request, account, "about", version=APP_VERSION, backend=backend_name(), db_ok=db_ok,
        store_content=message_log.store_content_enabled(),
        mcp_url=(os.environ.get("PUBLIC_MCP_URL") or f"{base_url}/mcp").strip(),
    ))


# ---------------------------------------------------------------------
# Account (profile + password)
# ---------------------------------------------------------------------
@router.get("/gateway/account", response_class=HTMLResponse)
def account_page(request: Request):
    return _page(request, "gateway/account.html", "account", done=request.query_params.get("done") == "1")


@router.post("/gateway/account/password")
async def change_password(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    form = await request.form()
    current = str(form.get("current") or "")
    new1 = str(form.get("new1") or "")
    new2 = str(form.get("new2") or "")

    def fail(msg: str):
        return templates.TemplateResponse(
            "gateway/account.html", page_ctx(request, account, "account", error=msg), status_code=400
        )

    if not verify_password(current, account.get("password_hash", "")):
        return fail("Your current password is incorrect.")
    if new1 != new2:
        return fail("The new passwords don't match.")
    try:
        new_hash = hash_password(new1)
    except ValueError as e:
        return fail(str(e))
    get_repository().set_password_hash(str(account["_id"]), new_hash)
    return _redirect("/gateway/account?done=1")


# ---------------------------------------------------------------------
# Administrator-only helpers
# ---------------------------------------------------------------------
def _owner_or_error(request: Request, password: Optional[str] = None) -> dict[str, Any]:
    """Session must belong to the administrator; when `password` is given it must match."""
    account = _load_account_from_session(request)
    if not account:
        raise HTTPException(status_code=401, detail="Please log in again.")
    if not access.is_owner(account):
        raise HTTPException(status_code=403, detail="Only an administrator can change settings.")
    if password is not None and not verify_password(password or "", account.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="That password is incorrect.")
    return account


def _forbidden(request: Request, account: dict[str, Any], active: str):
    return templates.TemplateResponse("gateway/forbidden.html", page_ctx(request, account, active), status_code=403)


# ---------------------------------------------------------------------
# Channels (administrator only): one page per channel, several providers each
# ---------------------------------------------------------------------
@router.get("/gateway/channels", response_class=HTMLResponse)
def channels_index():
    return _redirect("/gateway")


@router.get("/gateway/channels/{channel}", response_class=HTMLResponse)
def channel_page(request: Request, channel: str):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    if channel not in channels.CATALOG:
        raise HTTPException(status_code=404, detail="Unknown channel")
    if not access.is_owner(account):
        return _forbidden(request, account, "channels")
    status = channels.channel_status(channel)
    payload = {
        "channel": channel, "label": channels.CHANNEL_LABELS[channel],
        "spec": channels.public_catalog()[channel], "status": status,
    }
    return templates.TemplateResponse(
        "gateway/channel.html",
        page_ctx(request, account, "channels", ch=status, payload=payload, unreadable=secret_store.unreadable_count()),
    )


class AppItem(BaseModel):
    name: str = ""
    token: str = ""


class SaveBody(BaseModel):
    provider: str
    values: dict[str, str] = Field(default_factory=dict)
    password: str = ""
    make_default: Optional[bool] = None
    apps: Optional[list[AppItem]] = None      # Pushover: the complete list of named apps
    default_app: Optional[str] = None


class ProviderBody(BaseModel):
    provider: str
    password: str = ""


class TestBody(BaseModel):
    provider: Optional[str] = None
    to: Optional[str] = None
    app: Optional[str] = None


def _known_channel(channel: str) -> None:
    if channel not in channels.CATALOG:
        raise HTTPException(status_code=404, detail="Unknown channel")


@router.post("/gateway/channels/{channel}/save", include_in_schema=False)
def channel_save(request: Request, channel: str, body: SaveBody):
    _known_channel(channel)
    _owner_or_error(request, body.password)
    result = channels.apply_provider(
        channel, body.provider, body.values, body.make_default,
        apps=[a.model_dump() for a in body.apps] if body.apps is not None else None,
        default_app=body.default_app,
    )
    result["status"] = channels.channel_status(channel)
    return result


@router.post("/gateway/channels/{channel}/default", include_in_schema=False)
def channel_default(request: Request, channel: str, body: ProviderBody):
    _known_channel(channel)
    _owner_or_error(request, body.password)
    result = channels.set_default(channel, body.provider)
    result["status"] = channels.channel_status(channel)
    return result


@router.post("/gateway/channels/{channel}/remove", include_in_schema=False)
def channel_remove(request: Request, channel: str, body: ProviderBody):
    _known_channel(channel)
    _owner_or_error(request, body.password)
    result = channels.remove_provider(channel, body.provider)
    result["status"] = channels.channel_status(channel)
    return result


class DefaultsBody(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    password: str = ""


@router.post("/gateway/channels/{channel}/defaults", include_in_schema=False)
def channel_defaults_save(request: Request, channel: str, body: DefaultsBody):
    _known_channel(channel)
    _owner_or_error(request, body.password)
    result = channels.save_defaults(channel, body.values)
    result["status"] = channels.channel_status(channel)
    return result


@router.post("/gateway/channels/{channel}/test", include_in_schema=False)
def channel_test(request: Request, channel: str, body: TestBody):
    _known_channel(channel)
    _owner_or_error(request)
    return channels.send_test(channel, body.to, body.provider, body.app)


# ---------------------------------------------------------------------
# Settings (administrator only): general options
# ---------------------------------------------------------------------
@router.get("/gateway/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    if not access.is_owner(account):
        return _forbidden(request, account, "settings")
    try:
        db_ok = get_repository().ping()
    except Exception:
        db_ok = False
    return templates.TemplateResponse("gateway/settings.html", page_ctx(
        request, account, "settings", backend=backend_name(), db_ok=db_ok,
        store_content=message_log.store_content_enabled(),
        allow_signups=(secret_store.get_setting(access.SIGNUPS_KEY) or "0") == "1",
    ))


class GeneralBody(BaseModel):
    store_content: bool
    allow_signups: bool
    password: str = ""


class ImportBody(BaseModel):
    text: str
    password: str = ""


@router.post("/gateway/settings/import", include_in_schema=False)
def settings_import(request: Request, body: ImportBody):
    _owner_or_error(request, body.password)
    if len(body.text) > 200_000:
        raise HTTPException(status_code=400, detail="That file is too large to be a .env file.")
    return channels.import_env_text(body.text)


@router.post("/gateway/settings/general", include_in_schema=False)
def settings_general(request: Request, body: GeneralBody):
    _owner_or_error(request, body.password)
    secret_store.set_setting("STORE_MESSAGE_CONTENT", "1" if body.store_content else "0")
    secret_store.set_setting(access.SIGNUPS_KEY, "1" if body.allow_signups else "0")
    return {"ok": True}
