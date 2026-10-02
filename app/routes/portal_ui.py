"""
Portal pages after login: Home, Message log, API keys, Settings, About, Account.

All pages share gateway/base.html (navbar + /static/app.css). Settings is
administrator-only (the logged-in session is enough; no password is asked again).
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
from app.services import access, cf_access, channels, db_switch, dispatch, email_templates, key_check, message_log, secret_store
from app.services.phone import normalize_phone
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
    return templates.TemplateResponse("gateway/home.html", page_ctx(
        request, account, "home",
        channels=channels.all_status(),
        trend=message_log.channel_trend(str(account["_id"]), days=14),
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
    hide_tests = qp.get("tests") == "hide"
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
    if hide_tests:
        msgs = [m for m in msgs if not m.get("is_test")]
    more = len(msgs) > limit
    return templates.TemplateResponse("gateway/messages.html", page_ctx(
        request, account, "messages", messages=msgs[:limit], more=more, limit=limit,
        q=q, channel=channel or "", status=status or "", hide_tests=hide_tests,
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
    ctx = page_ctx(
        request, account, "about", version=APP_VERSION, backend=backend_name(), db_ok=db_ok,
        store_content=message_log.store_content_enabled(),
    )
    ctx["mcp_url"] = f"{ctx['base_url']}/mcp"
    ctx["mcp_prompt"] = (
        f"Connect to my self-hosted Message Gateway's MCP server at {ctx['mcp_url']} "
        f"(X-User-Key: {ctx['user_key']}, X-API-Token: ask me for one, or create one at {ctx['base_url']}/gateway/keys). "
        "Once connected, use it to send messages, check delivery status, review recent messages, "
        "and troubleshoot why a message didn't send."
    )
    return templates.TemplateResponse("gateway/about.html", ctx)


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
def _owner_or_error(request: Request) -> dict[str, Any]:
    """The logged-in session must belong to the administrator."""
    account = _load_account_from_session(request)
    if not account:
        raise HTTPException(status_code=401, detail="Please log in again.")
    if not access.is_owner(account):
        raise HTTPException(status_code=403, detail="Only an administrator can change settings.")
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
    make_default: Optional[bool] = None
    apps: Optional[list[AppItem]] = None      # Pushover: the complete list of named apps
    default_app: Optional[str] = None


class ProviderBody(BaseModel):
    provider: str


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
    _owner_or_error(request)
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
    _owner_or_error(request)
    result = channels.set_default(channel, body.provider)
    result["status"] = channels.channel_status(channel)
    return result


@router.post("/gateway/channels/{channel}/remove", include_in_schema=False)
def channel_remove(request: Request, channel: str, body: ProviderBody):
    _known_channel(channel)
    _owner_or_error(request)
    result = channels.remove_provider(channel, body.provider)
    result["status"] = channels.channel_status(channel)
    return result


class ProviderEnabledBody(BaseModel):
    provider: str
    enabled: bool


@router.post("/gateway/channels/{channel}/provider/enabled", include_in_schema=False)
def channel_set_provider_enabled(request: Request, channel: str, body: ProviderEnabledBody):
    """Turn one connected provider on or off. While off, the API and MCP tools
    refuse to send through it - explicitly requested or picked as the channel's
    default (see enqueue_message). The provider stays connected and configured."""
    _known_channel(channel)
    _owner_or_error(request)
    if body.provider not in channels.CATALOG[channel]["providers"]:
        raise HTTPException(status_code=404, detail="Unknown provider.")
    channels.set_provider_enabled(channel, body.provider, body.enabled)
    return {"ok": True, "status": channels.channel_status(channel)}


class DefaultsBody(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


@router.post("/gateway/channels/{channel}/defaults", include_in_schema=False)
def channel_defaults_save(request: Request, channel: str, body: DefaultsBody):
    _known_channel(channel)
    _owner_or_error(request)
    result = channels.save_defaults(channel, body.values)
    result["status"] = channels.channel_status(channel)
    return result


@router.post("/gateway/channels/telegram/find-chats", include_in_schema=False)
def telegram_find_chats(request: Request):
    """Recent chat ids the saved bot has been messaged from - Telegram bots can't look this up
    any other way, so this is the only way to find one short of asking the user to read raw JSON."""
    _owner_or_error(request)
    return channels.telegram_recent_chats()


@router.post("/gateway/channels/{channel}/test", include_in_schema=False)
def channel_test(request: Request, channel: str, body: TestBody):
    _known_channel(channel)
    account = _owner_or_error(request)
    to = (body.to or "").strip() or None
    if channel == "email" and not to:
        to = account.get("email")                       # "send me a test": default to the administrator's own address
    if channel == "sms" and not to and not channels.default_sms_number():
        return {"ok": False, "error": "Enter a phone number to send the test to, or set a default phone number on this page."}
    if channel == "sms" and to:
        try:
            to = normalize_phone(to)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    if channel == "telegram" and not to and not channels.default_telegram_chat_id():
        return {"ok": False, "error": "Enter a chat ID to send the test to, or set a default chat ID on this page."}
    if channel == "whatsapp" and not to and not channels.default_whatsapp_number():
        return {"ok": False, "error": "Enter a phone number to send the test to, or set a default recipient on this page."}
    if channel == "whatsapp" and to:
        try:
            to = normalize_phone(to)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
    try:
        req = dispatch.build_test_request(channel, to, body.provider, body.app)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    result = dispatch.send_and_wait(req, str(account["_id"]), wait_seconds=10, source="portal")
    if result.get("status") == "queued":
        return {"ok": True, "note": "Sent. It is still being delivered; check the message log."}
    return {"ok": bool(result.get("ok")), "error": channels.friendly(result.get("error")) if not result.get("ok") else None}


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
        request, account, "settings", backend=backend_name(), db_ok=db_ok, db=db_switch.describe_current(),
        store_content=message_log.store_content_enabled(),
        allow_signups=(secret_store.get_setting(access.SIGNUPS_KEY) or "0") == "1",
        public_base_url_override=(secret_store.get_setting("PUBLIC_BASE_URL") or "").strip(),
        public_base_url_env_locked=bool((os.environ.get("PUBLIC_BASE_URL") or "").strip()),
        detected_base_url=str(request.base_url).rstrip("/"),
        sso=cf_access.settings(), aud_last4=channels.mask_tail(cf_access.settings()["aud"]),
        keys=key_check.status(force=True),
    ))


class GeneralBody(BaseModel):
    store_content: bool
    allow_signups: bool


class DbTarget(BaseModel):
    backend: str
    mongodb_uri: str = ""
    mongodb_db: str = "relay_gateway"
    copy: bool = True
    use_existing: bool = False


@router.post("/gateway/settings/database/check", include_in_schema=False)
def settings_database_check(request: Request, body: DbTarget):
    _owner_or_error(request)
    return db_switch.check_target(body.backend, body.mongodb_uri, body.mongodb_db)


@router.post("/gateway/settings/database/switch", include_in_schema=False)
def settings_database_switch(request: Request, body: DbTarget):
    _owner_or_error(request)
    return db_switch.switch_database(body.backend, body.mongodb_uri, body.mongodb_db, body.copy, body.use_existing)


class ImportBody(BaseModel):
    text: str


@router.post("/gateway/settings/import", include_in_schema=False)
def settings_import(request: Request, body: ImportBody):
    _owner_or_error(request)
    if len(body.text) > 200_000:
        raise HTTPException(status_code=400, detail="That file is too large to be a .env file.")
    return channels.import_env_text(body.text)


@router.post("/gateway/settings/general", include_in_schema=False)
def settings_general(request: Request, body: GeneralBody):
    _owner_or_error(request)
    secret_store.set_setting("STORE_MESSAGE_CONTENT", "1" if body.store_content else "0")
    secret_store.set_setting(access.SIGNUPS_KEY, "1" if body.allow_signups else "0")
    return {"ok": True}


class PublicBaseUrlBody(BaseModel):
    url: str = ""


@router.post("/gateway/settings/public-url", include_in_schema=False)
def settings_public_base_url(request: Request, body: PublicBaseUrlBody):
    _owner_or_error(request)
    if os.environ.get("PUBLIC_BASE_URL", "").strip():
        raise HTTPException(status_code=400, detail="PUBLIC_BASE_URL is set in the environment and overrides this - remove it from .env to manage it here.")
    url = body.url.strip().rstrip("/")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Must start with http:// or https://.")
    if url:
        secret_store.set_setting("PUBLIC_BASE_URL", url)
    else:
        secret_store.delete_setting("PUBLIC_BASE_URL")
    return {"ok": True, "url": url}


# ---------------------------------------------------------------------
# Settings: single sign-on with Cloudflare Access (Zero Trust), portal only
# ---------------------------------------------------------------------
class SsoBody(BaseModel):
    enabled: bool
    team: str = ""
    aud: str = ""
    password_login: bool = True


def _check_sso_request(request: Request, team: str, aud: str) -> str:
    """Verify the Cloudflare Access token on THIS request; return the email it proves."""
    token = request.headers.get(cf_access.JWT_HEADER)
    if not token:
        raise HTTPException(status_code=400, detail=(
            "This request didn't come through Cloudflare Access (no token). Put the portal behind an Access "
            "application for /gateway first, then reload this page from the protected address."))
    try:
        return str(cf_access.verify(token, team, aud).get("email") or "").strip().lower()
    except cf_access.AccessError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/gateway/settings/sso/test", include_in_schema=False)
def settings_sso_test(request: Request, body: SsoBody):
    _owner_or_error(request)
    team = cf_access.normalize_team(body.team)
    if not cf_access.valid_team(team):
        raise HTTPException(status_code=400, detail="The team domain must look like your-team.cloudflareaccess.com.")
    aud = body.aud.strip() or cf_access.settings()["aud"]  # blank = keep the saved one
    if not aud:
        raise HTTPException(status_code=400, detail="The Application Audience (AUD) tag is required.")
    return {"ok": True, "email": _check_sso_request(request, team, aud)}


@router.post("/gateway/settings/sso", include_in_schema=False)
def settings_sso_save(request: Request, body: SsoBody):
    account = _owner_or_error(request)
    # The AUD tag is never sent back to the browser; leaving the box blank keeps the saved one.
    team, aud = cf_access.normalize_team(body.team), body.aud.strip() or cf_access.settings()["aud"]
    if team and not cf_access.valid_team(team):
        raise HTTPException(status_code=400, detail="The team domain must look like your-team.cloudflareaccess.com.")
    # The details are always stored (encrypted, in the database) so they survive a reload,
    # even before Cloudflare Access is switched on.
    for key, value in ((cf_access.TEAM_KEY, team), (cf_access.AUD_KEY, aud)):
        secret_store.set_setting(key, value) if value else secret_store.delete_setting(key)
    secret_store.set_setting(cf_access.PASSWORD_KEY, "1" if body.password_login else "0")
    if not body.enabled:
        secret_store.set_setting(cf_access.ENABLED_KEY, "0")
        return {"ok": True, "enabled": False}
    if not team or not aud:
        secret_store.set_setting(cf_access.ENABLED_KEY, "0")
        raise HTTPException(status_code=400, detail="Saved, but not turned on: the team domain and AUD tag are both required.")
    # Never let the administrator lock themselves out: turning SSO on needs proof that Cloudflare
    # Access already signs THIS administrator in.
    try:
        email = _check_sso_request(request, team, aud)
        if email != str(account.get("email") or "").strip().lower():
            raise HTTPException(status_code=400, detail=f"Cloudflare Access signed you in as {email}, which is not the administrator's email ({account.get('email')}).")
    except HTTPException as e:
        secret_store.set_setting(cf_access.ENABLED_KEY, "0")
        raise HTTPException(status_code=400, detail=f"Details saved, but Cloudflare Access is not turned on yet: {e.detail}")
    secret_store.set_setting(cf_access.ENABLED_KEY, "1")
    return {"ok": True, "enabled": True, "team": team}


# ---------------------------------------------------------------------
# Email template builder (administrator only, and only once Email is set up)
# ---------------------------------------------------------------------
def _email_ready() -> bool:
    return bool(channels.connected_providers("email"))


def _templates_owner(request: Request) -> dict[str, Any]:
    account = _owner_or_error(request)
    if not _email_ready():
        raise HTTPException(status_code=409, detail="Set up Email first.")
    return account


@router.get("/gateway/templates", response_class=HTMLResponse)
def templates_index():
    # Email is the only template type so far; this is where a channel picker goes later.
    return _redirect("/gateway/templates/email")


@router.get("/gateway/templates/email", response_class=HTMLResponse)
def email_templates_page(request: Request):
    redirect, account = _require_account_or_redirect(request)
    if redirect:
        return redirect
    assert account is not None
    if not access.is_owner(account):
        return _forbidden(request, account, "templates")
    if not _email_ready():
        return _redirect("/gateway/channels/email")
    resp = templates.TemplateResponse("gateway/email_templates.html", page_ctx(
        request, account, "templates", payload={"templates": email_templates.list_templates(),
                 "base_url": page_ctx(request, account)["base_url"], "user_key": account.get("user_key", "")},
    ))
    resp.headers["Cache-Control"] = "no-store"   # the page's script changes often; never show a stale copy
    return resp


@router.get("/gateway/templates/email/item/{ref}", include_in_schema=False)
def email_template_get(request: Request, ref: str):
    _templates_owner(request)
    t = email_templates.get(ref)
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


class TemplateBody(BaseModel):
    id: Optional[str] = None
    name: str
    html: str = ""
    txt: str = ""


@router.post("/gateway/templates/email/save", include_in_schema=False)
def email_template_save(request: Request, body: TemplateBody):
    _templates_owner(request)
    try:
        t = email_templates.save(body.id, body.name, body.html, body.txt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "template": t, "templates": email_templates.list_templates()}


class TemplateName(BaseModel):
    id: str


@router.post("/gateway/templates/email/delete", include_in_schema=False)
def email_template_delete(request: Request, body: TemplateName):
    _templates_owner(request)
    if not email_templates.delete(body.id):
        raise HTTPException(status_code=404, detail="No saved copy of that template to delete.")
    return {"ok": True, "templates": email_templates.list_templates()}


# ---------------------------------------------------------------------
# Encryption keys (administrator only)
# ---------------------------------------------------------------------
@router.post("/gateway/settings/keys/purge", include_in_schema=False)
def keys_purge(request: Request):
    _owner_or_error(request)
    return {"ok": True, "removed": key_check.purge_unreadable()}


@router.post("/gateway/settings/keys/accept-token", include_in_schema=False)
def keys_accept_token(request: Request):
    _owner_or_error(request)
    key_check.accept_token_secret()
    return {"ok": True}
