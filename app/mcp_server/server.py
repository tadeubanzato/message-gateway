"""
MCP server: lets an AI agent send messages and inspect the gateway.

Authentication: every request must carry the same X-User-Key and X-API-Token
headers as the HTTP API (see McpAuthMiddleware in auth.py). Tools then run as
that account:
  - send / read tools act on the caller's own messages only
  - queue, dead-letter and provider-check tools are administrator-only

Secrets never pass through this server. Provider credentials are entered in the
web app (Channels pages), not through MCP tools.

The MCP routes are served by the main FastAPI app (same port, path /mcp); see
app/main.py.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import os
import re
from typing import Any, Optional, Union
from urllib.parse import urlsplit

import pika
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app import bootstrap
from app.auth import authenticate
from app.broker import (
    QUEUE_NAMES,
    RABBITMQ_URL,
    dead_letter_queue_name,
    republish_from_dead_letter,
)
from app.db import backend_name, get_repository
from app.schemas import MessageRequest
from app.services import access, channels, dispatch, message_log, secret_store
from app.services.phone import normalize_phone

GATEWAY_BASE_URL = (os.environ.get("GATEWAY_BASE_URL") or "http://localhost:8010").strip().rstrip("/")

MAX_LIST = 100
MAX_CONTENT_CHARS = 2000


def _transport_security() -> TransportSecuritySettings:
    """FastMCP silently rejects any Host header but localhost (HTTP 421 "Invalid Host
    header") unless told otherwise, so a gateway reached as okame.local or by LAN IP could
    never be connected to. /mcp already requires the API key and token, so the Host check
    is off by default. Set MCP_ALLOWED_HOSTS (comma-separated, e.g. "okame.local:*") to
    turn it back on for just those hosts."""
    hosts = [h.strip() for h in (os.environ.get("MCP_ALLOWED_HOSTS") or "").split(",") if h.strip()]
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")],
    )


mcp = FastMCP(
    name="message-gateway",
    streamable_http_path="/mcp",
    transport_security=_transport_security(),
    instructions=(
        "Message Gateway sends email, SMS, push, Telegram and WhatsApp messages through the providers "
        "its administrator connected. Map requests to tools like this: 'send a push notification' -> "
        "send_push; 'send an email' -> send_email; 'send / text an SMS to <number>' -> send_sms; "
        "'send a Telegram message' -> send_telegram; 'send a WhatsApp message' -> send_whatsapp; "
        "'send a test email/sms/push' -> send_test; anything else -> send_notification. Each send "
        "waits a few seconds and reports delivered or failed, so you can tell the user the outcome. "
        "Use list_providers to see what is connected (and pass provider= to choose one) - check it "
        "before offering channel choices, so you only offer channels that are actually set up. If a "
        "channel isn't set up (list_providers -> that channel's state is 'not_set_up' or "
        "'needs_attention', or a send fails with a setup_page in the result), offer to open that "
        "setup_page URL in the user's browser right away (e.g. `open <url>` on macOS, `xdg-open "
        "<url>` on Linux) so they can connect it, rather than just telling them the administrator "
        "must connect it - unless you're running on a different machine than the one they're "
        "browsing from, in which case just give them the address. "
        "A send can also fail with 'X is turned off on the gateway': that provider is connected but "
        "an administrator switched it off (Channels > that channel > that provider's card) - tell the "
        "user which provider and that they (or the administrator) can turn it back on there, or pick a "
        "different connected provider with provider=. "
        "WhatsApp only delivers a free-form message if the recipient has messaged the business number "
        "in the last 24 hours - if a send fails for that reason, tell the user plainly instead of "
        "retrying; there is no template-message fallback here. "
        "Provider credentials are managed in the web app, never through this server: never ask "
        "the user to paste keys or passwords into chat."
    ),
)


# ---------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------
class _Denied(Exception):
    pass


def _who(ctx: Optional[Context]) -> dict[str, Any]:
    """Identity of the caller, from the request headers. Raises _Denied."""
    request = getattr(getattr(ctx, "request_context", None), "request", None) if ctx else None
    if request is None:
        raise _Denied("Unauthorized: could not read the request credentials.")
    ident = authenticate(request.headers.get("x-user-key"), request.headers.get("x-api-token"))
    if ident is None:
        raise _Denied("Unauthorized: invalid or missing X-User-Key / X-API-Token.")
    return ident


def _admin(ctx: Optional[Context]) -> dict[str, Any]:
    ident = _who(ctx)
    if ident["account_id"] != access.owner_id():
        raise _Denied("This tool is for the administrator only. Ask them to run it or to grant you a key.")
    return ident


def tool():
    """Register an MCP tool (sync or async) that turns failures into a clean error result."""
    def deco(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except _Denied as e:
                    return {"ok": False, "error": str(e)}
                except Exception as e:  # noqa: BLE001 - never leak internals to the agent
                    return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            return mcp.tool()(awrapper)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except _Denied as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        return mcp.tool()(wrapper)
    return deco


def _cap(text: Optional[str]) -> Optional[str]:
    if text is None or len(text) <= MAX_CONTENT_CHARS:
        return text
    return text[:MAX_CONTENT_CHARS] + "… (truncated)"


def _view(doc: dict[str, Any], include_content: bool) -> dict[str, Any]:
    out = message_log.public_view(doc, include_content=include_content)
    if include_content:
        out["body"] = _cap(out.get("body"))
    return out


def _channel(channel: str) -> str:
    c = (channel or "").strip().lower()
    if c not in channels.CATALOG:
        raise _Denied(f"Unknown channel {channel!r}. Use email, sms, push, telegram or whatsapp.")
    return c


# ---------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------
@tool()
async def send_notification(
    ctx: Context,
    channel: str,
    to: Union[str, list[str], None] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    template: Optional[str] = None,
    context: Optional[dict] = None,
    provider: Optional[str] = None,
    app: Optional[str] = None,
    email_type: Optional[str] = None,
    wait_seconds: int = 8,
) -> dict:
    """Send a message on any channel (general form). Prefer send_push, send_email,
    send_sms or send_test when they fit. The message is queued, then this waits a few
    seconds and reports 'delivered' or 'failed' with the reason.

    channel: 'email', 'sms', 'push', 'telegram' or 'whatsapp'.
    to: recipient (email address, phone number, or Telegram chat id, or a list). Omit for push. For
        whatsapp, only delivers if this number has messaged the WhatsApp business number in the last
        24 hours.
    subject: required for email; the title for push.
    body: message text. Required unless `template` is given.
    template: name of a server-side template to use instead of `body`.
    context: values for {{context.key}} placeholders in the body/template.
    provider: which connected provider to use (see list_providers).
    app: push only. Which named Pushover app to send from.
    email_type: 'txt' (default) or 'html'.
    wait_seconds: how long to wait for the delivery result (0 = don't wait).
    """
    ch = _channel(channel)
    recips = _recipients(to)
    if ch == "sms" and recips:
        recips = [_phone(n) for n in (recips if isinstance(recips, list) else [recips])]
        recips = recips[0] if len(recips) == 1 else recips
    return await _send(ctx, wait_seconds, channel=ch, to=recips, subject=subject, body=body, template=template,
                 context=context or {}, provider=provider, app=app, emailType=email_type)


def _phone(number: str) -> str:
    """Normalize a phone number typed any way; the country code is required."""
    try:
        return normalize_phone(number)
    except ValueError as e:
        raise _Denied(f"{e} Ask the user for the full number including the country code.")


def _recipients(to: Union[str, list[str], None]) -> Optional[Union[str, list[str]]]:
    """Accept one address/number, a list, or a comma-separated string."""
    if to is None:
        return None
    parts = to if isinstance(to, list) else str(to).split(",")
    cleaned = [p.strip() for p in parts if p and p.strip()]
    if not cleaned:
        return None
    return cleaned[0] if len(cleaned) == 1 else cleaned


async def _send(ctx: Context, wait_seconds: int, **fields: Any) -> dict:
    """Queue a message for the caller's account, then wait briefly for the result."""
    ident = _who(ctx)
    channel = fields.get("channel")
    try:
        req = MessageRequest(**{k: v for k, v in fields.items() if v is not None})
    except ValueError as e:
        msg = "; ".join(x.get("msg", "").removeprefix("Value error, ") for x in e.errors()) if hasattr(e, "errors") else str(e)
        return {"ok": False, "error": msg}
    out = await asyncio.to_thread(dispatch.send_and_wait, req, ident["account_id"], wait_seconds, source="mcp")
    code = out.pop("status_code", None)
    if not out.get("ok") and code == 409 and channel in channels.CATALOG:
        out["setup_page"] = f"{GATEWAY_BASE_URL}/gateway/channels/{channel}"
        out["hint"] = (
            f"No {channel} provider is connected yet. Offer to open the setup_page URL in the "
            "user's browser now (e.g. `open <url>` / `xdg-open <url>`) so they can connect one, "
            "unless you're running on a different machine than the one they're browsing from."
        )
    if out.get("status") == "failed":
        out["hint"] = "Use get_message for the full delivery attempts."
    return out


@tool()
async def send_push(
    ctx: Context,
    body: str,
    title: Optional[str] = None,
    app: Optional[str] = None,
    provider: Optional[str] = None,
    url: Optional[str] = None,
    wait_seconds: int = 8,
) -> dict:
    """Send a PUSH NOTIFICATION to the user's phone/device. Use this when the user says
    "send a push notification", "notify me", "ping my phone" and the like.

    body: the notification text.
    title: optional headline.
    app: which named Pushover app to send from (see list_providers -> pushover_apps).
        Default: the gateway's default app.
    provider: which connected push provider to use (default: the channel default).
    url: optional link to attach.
    wait_seconds: how long to wait for the delivery result (0 = don't wait).
    """
    return await _send(ctx, wait_seconds, channel="push", body=body, subject=title, app=app,
                 provider=provider, url=url)


@tool()
async def send_email(
    ctx: Context,
    to: Union[str, list[str]],
    subject: str,
    body: str,
    html: bool = False,
    provider: Optional[str] = None,
    wait_seconds: int = 8,
) -> dict:
    """Send an EMAIL. Use this when the user says "send an email to ...".

    to: recipient address, or a list of addresses.
    subject: subject line. body: the message (plain text, or HTML if html=true).
    provider: which connected email provider to use (default: the channel default).
    wait_seconds: how long to wait for the delivery result (0 = don't wait).
    """
    return await _send(ctx, wait_seconds, channel="email", to=_recipients(to), subject=subject, body=body,
                 emailType="html" if html else "txt", provider=provider)


@tool()
async def send_sms(
    ctx: Context,
    body: str,
    to: Union[str, list[str], None] = None,
    provider: Optional[str] = None,
    wait_seconds: int = 8,
) -> dict:
    """Send an SMS text message. Use this when the user says "send an SMS to +1 555...",
    "text this number" and the like.

    body: the message text.
    to: phone number including the country code, in any format ("+1 (555) 123-4567"
        works). Leave it out to use the gateway's default phone number, if one is set.
        If the user gave a number without a country code, ask for it.
    provider: which connected SMS provider to use (default: the channel default).
    wait_seconds: how long to wait for the delivery result (0 = don't wait).
    """
    recips = _recipients(to)
    if recips is None:
        if not channels.default_sms_number():
            return {"ok": False, "error": "Say which phone number to text (with country code), or ask the administrator to set a default phone number in the web app (Channels > SMS)."}
        return await _send(ctx, wait_seconds, channel="sms", body=body, provider=provider)  # API fills in the default
    numbers = [_phone(n) for n in (recips if isinstance(recips, list) else [recips])]
    return await _send(ctx, wait_seconds, channel="sms", to=numbers[0] if len(numbers) == 1 else numbers,
                       body=body, provider=provider)


@tool()
async def send_telegram(
    ctx: Context,
    body: str,
    to: Union[str, list[str], None] = None,
    provider: Optional[str] = None,
    wait_seconds: int = 8,
) -> dict:
    """Send a Telegram message via a bot. Use this when the user says "send a Telegram
    message", "message me on Telegram" and the like.

    body: the message text.
    to: a chat id (see list_providers -> telegram, or tell the user to use "Find chat IDs" in
        the web app under Channels > Telegram). Leave it out to use the gateway's default chat
        ID, if one is set. Telegram bots can't message someone who has never messaged the bot
        first - if sending fails because there's no chat ID, tell the user to open their bot in
        Telegram and send it any message once.
    provider: which connected Telegram provider to use (default: the channel default).
    wait_seconds: how long to wait for the delivery result (0 = don't wait).
    """
    recips = _recipients(to)
    if recips is None:
        if not channels.default_telegram_chat_id():
            return {"ok": False, "error": "Say which chat ID to message, or ask the administrator to set a default chat ID in the web app (Channels > Telegram)."}
        return await _send(ctx, wait_seconds, channel="telegram", body=body, provider=provider)  # API fills in the default
    return await _send(ctx, wait_seconds, channel="telegram", to=recips, body=body, provider=provider)


@tool()
async def send_whatsapp(
    ctx: Context,
    body: str,
    to: Union[str, list[str], None] = None,
    provider: Optional[str] = None,
    account: Optional[str] = None,
    wait_seconds: int = 8,
) -> dict:
    """Send a WhatsApp message via the WhatsApp Business Platform or a connected Gakai server. Use this when the user says
    "send a WhatsApp message", "WhatsApp me" and the like.

    body: the message text.
    to: phone number including the country code, in any format ("+1 (555) 123-4567" works).
        Leave it out to use the gateway's default recipient, if one is set. WhatsApp only
        delivers a free-form message like this if that person has messaged the business's
        WhatsApp number in the last 24 hours - if the send fails for that reason, tell the user
        plainly; there's no template-message fallback here.
    provider: which connected WhatsApp provider to use (default: the channel default).
    account: Gakai only: which connected WhatsApp account to send from, by its account id
        (see list_providers -> gakai_accounts). Leave it out for the default account.
    wait_seconds: how long to wait for the delivery result (0 = don't wait).
    """
    recips = _recipients(to)
    if recips is None:
        if not channels.default_whatsapp_number():
            return {"ok": False, "error": "Say which phone number to message (with country code), or ask the administrator to set a default recipient in the web app (Channels > WhatsApp)."}
        return await _send(ctx, wait_seconds, channel="whatsapp", body=body, provider=provider, app=account)  # API fills in the default
    numbers = [_phone(n) for n in (recips if isinstance(recips, list) else [recips])]
    return await _send(ctx, wait_seconds, channel="whatsapp", to=numbers[0] if len(numbers) == 1 else numbers,
                       body=body, provider=provider, app=account)


@tool()
async def send_test(
    ctx: Context,
    channel: str = "push",
    to: Optional[str] = None,
    provider: Optional[str] = None,
    app: Optional[str] = None,
    wait_seconds: int = 10,
) -> dict:
    """Send a short TEST message to check a channel works. Use this when the user says
    "send a test email", "test push", "send me a test SMS" and the like.

    channel: 'push' (default), 'email' or 'sms'.
    to: for email, defaults to the user's own account email; for sms, defaults to the
        gateway's default phone number (or give one with country code); ignored for push.
    provider / app: optionally test a specific provider or Pushover app.
    """
    ident = _who(ctx)
    ch = _channel(channel)
    text = "Test message from Message Gateway, sent by your AI agent. If you can read this, it works."
    if ch == "push":
        return await _send(ctx, wait_seconds, channel="push", body=text, subject="Message Gateway test",
                           app=app, provider=provider, meta={"test": True})
    if ch == "email":
        target = (to or "").strip()
        if not target:
            acct = get_repository().find_account_by_id(ident["account_id"]) or {}
            target = acct.get("email", "")
        if not target:
            return {"ok": False, "error": "Say which email address to send the test to."}
        return await _send(ctx, wait_seconds, channel="email", to=_recipients(target), subject="Message Gateway test",
                           body=text, emailType="txt", provider=provider, meta={"test": True})
    if not (to or "").strip():
        if not channels.default_sms_number():
            return {"ok": False, "error": "Say which phone number to send the test to (with country code), or ask the administrator to set a default phone number."}
        return await _send(ctx, wait_seconds, channel="sms", body=text, provider=provider, meta={"test": True})  # default number
    return await _send(ctx, wait_seconds, channel="sms", to=_phone(to), body=text, provider=provider, meta={"test": True})


# ---------------------------------------------------------------------
# Discover
# ---------------------------------------------------------------------
@tool()
def list_providers(ctx: Context, channel: Optional[str] = None) -> dict:
    """Which channels you can send on right now, and which still need setup. For each
    channel: its state, the default provider, the connected providers you can pass as
    `provider` to send_notification, and every provider the gateway supports. Use this
    before offering channel choices for a send, so you only offer what's actually set up.

    If a channel's state is 'not_set_up' or 'needs_attention', it has a `setup_page`
    URL - offer to open that in the user's browser (e.g. `open <url>` on macOS,
    `xdg-open` on Linux, `start` on Windows) rather than just naming the address,
    unless you're running on a different machine than the one they're browsing from."""
    _who(ctx)
    chans = [_channel(channel)] if channel else list(channels.CATALOG)
    out = {}
    for ch in chans:
        state = channels.channel_status(ch)["state"]
        entry = {
            "state": state,
            "default": channels.default_provider(ch),
            "connected": channels.connected_providers(ch),
            "supported": list(channels.CATALOG[ch]["providers"]),
        }
        if state in ("not_set_up", "needs_attention"):
            entry["setup_page"] = f"{GATEWAY_BASE_URL}/gateway/channels/{ch}"
        out[ch] = entry
    if "push" in out:
        out["push"]["pushover_apps"] = [a["name"] for a in channels.pushover_apps() if a["set"]]
    if "whatsapp" in out:
        out["whatsapp"]["gakai_accounts"] = [{"id": a["id"], "label": a["label"], "default": a["is_default"]} for a in channels.gakai_account_list() if a["set"]]
    return {"ok": True, "channels": out}


def _database_details() -> dict[str, Any]:
    """Which backend is active and where it points, no credentials. Read fresh every
    call - db_backend/mongodb_uri live in bootstrap.json (mtime-checked, see
    app.bootstrap) and settings go through a 5s cache that's invalidated on write, so
    a backend switch or a provider change made in the web app shows up immediately,
    not just after a restart."""
    backend = backend_name()
    if backend == "atlas":
        uri = bootstrap.get("mongodb_uri") or ""
        parsed = urlsplit(uri)
        return {"backend": "mongodb", "host": parsed.hostname, "database": bootstrap.get("mongodb_db")}
    path = os.environ.get("SQLITE_PATH", "/app/data/gateway.db").strip() or "/app/data/gateway.db"
    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        size_bytes = None
    return {"backend": "sqlite", "path": path, "size_bytes": size_bytes}


@tool()
def get_setup_status(ctx: Context) -> dict:
    """Overall state of the gateway: database (backend, and mongodb/sqlite details),
    and per channel whether it is ready (default provider connected), needs attention,
    or is not set up. Always computed fresh - never cached - so it reflects whatever
    was just changed in the web app. Offline check: it does not call the providers
    (administrators can use check_provider_config)."""
    _who(ctx)
    try:
        db_ok = get_repository().ping()
    except Exception:
        db_ok = False
    return {
        "ok": True, "db_backend": backend_name(), "db_ok": db_ok, "database": _database_details(),
        "web_app": f"{GATEWAY_BASE_URL}/gateway",
        "credentials_unreadable": secret_store.unreadable_count(),
        "channels": {
            s["channel"]: {
                "state": s["state"], "default": s["default"],
                "connected": [c["name"] for c in s["connected"]],
                "setup_page": f"{GATEWAY_BASE_URL}/gateway/channels/{s['channel']}",
                **({"default_phone_number_set": bool(channels.default_sms_number())} if s["channel"] == "sms" else {}),
                **({"default_chat_id_set": bool(channels.default_telegram_chat_id())} if s["channel"] == "telegram" else {}),
                **({"default_phone_number_set": bool(channels.default_whatsapp_number())} if s["channel"] == "whatsapp" else {}),
            }
            for s in channels.all_status()
        },
    }


@tool()
def get_setup_instructions(ctx: Context, provider: str) -> dict:
    """What a provider needs and where to find it. The administrator enters these
    values in the web app (Channels pages). Never ask the user to paste secrets in chat."""
    _who(ctx)
    name = (provider or "").strip().lower()
    for ch, spec in channels.CATALOG.items():
        info = spec["providers"].get(name)
        if info:
            return {
                "ok": True, "provider": name, "channel": ch, "label": info["label"],
                "where_to_find_it": info["help"],
                "open_in_browser": [{"label": l["label"], "url": l["url"]} for l in info.get("links", [])],
                "fields": [{"label": f["label"], "secret": bool(f.get("secret")), "optional": bool(f.get("optional"))}
                           for f in info["fields"]],
                "enter_them_here": f"{GATEWAY_BASE_URL}/gateway/channels/{ch}",
                "note": "Only the gateway administrator can connect providers, in the browser.",
            }
    return {"ok": False, "error": f"Unknown provider {provider!r}."}


@tool()
def get_health(ctx: Context) -> dict:
    """Gateway health: database and message queue reachability."""
    _who(ctx)
    try:
        db_ok = get_repository().ping()
    except Exception:
        db_ok = False
    rabbit_ok = False
    try:
        conn = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        rabbit_ok = conn.is_open
        conn.close()
    except Exception:
        rabbit_ok = False
    return {"ok": db_ok and rabbit_ok, "db_backend": backend_name(), "db_ok": db_ok, "rabbitmq_ok": rabbit_ok}


# ---------------------------------------------------------------------
# Your messages
# ---------------------------------------------------------------------
@tool()
def list_recent_messages(
    ctx: Context,
    channel: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    search: Optional[str] = None,
    include_content: bool = False,
) -> dict:
    """Your recent messages, newest first (only messages sent with your key's account).

    channel: 'email', 'sms', 'push', 'telegram' or 'whatsapp'. status: 'queued', 'delivered' or 'failed'.
    search: match recipient, subject, text or message id.
    include_content: also return subject and body (off by default to keep results small).
    """
    ident = _who(ctx)
    limit = max(1, min(int(limit), MAX_LIST))
    docs = get_repository().list_messages(
        channel.strip().lower() if channel else None, status.strip().lower() if status else None,
        1000 if search else limit, account_id=ident["account_id"],
    )
    msgs = [_view(d, include_content or bool(search)) for d in docs]
    if search:
        needle = search.strip().lower()
        msgs = [m for m in msgs if any(needle in str(m.get(k) or "").lower() for k in ("to", "subject", "body", "message_id"))]
        if not include_content:
            for m in msgs:
                m.pop("body", None), m.pop("subject", None)
    return {"ok": True, "count": len(msgs[:limit]), "messages": msgs[:limit]}


@tool()
def get_message(ctx: Context, message_id: str, include_content: bool = True) -> dict:
    """One of your messages with its delivery attempts (provider, result, errors)."""
    ident = _who(ctx)
    repo = get_repository()
    doc = repo.get_message(message_id)
    if not doc or doc.get("account_id") != ident["account_id"]:
        return {"ok": False, "error": "Message not found."}
    attempts = [
        {k: v for k, v in a.items() if k not in ("_id", "created_at")}
        for a in sorted(repo.list_attempts(message_id), key=lambda a: a.get("attempt_number") or 0)
    ]
    return {"ok": True, "message": _view(doc, include_content), "delivery_attempts": attempts}


@tool()
def list_delivery_attempts(ctx: Context, message_id: str) -> dict:
    """Delivery attempts for one of your messages (same data as get_message)."""
    ident = _who(ctx)
    repo = get_repository()
    doc = repo.get_message(message_id)
    if not doc or doc.get("account_id") != ident["account_id"]:
        return {"ok": False, "error": "Message not found."}
    attempts = [{k: v for k, v in a.items() if k not in ("_id", "created_at")} for a in repo.list_attempts(message_id)]
    return {"ok": True, "attempts": attempts}


# ---------------------------------------------------------------------
# Administrator tools
# ---------------------------------------------------------------------
@tool()
def check_provider_config(ctx: Context, channel: str, provider: Optional[str] = None) -> dict:
    """ADMIN ONLY. Verify a provider's saved credentials by making a lightweight
    authenticated call to its API. `provider` defaults to the channel's default."""
    _admin(ctx)
    channel = _channel(channel)
    return {**channels.safe_check(channel, (provider or "").strip().lower() or None)}


def _rabbitmq_management_queue_info(queue_name: str) -> dict:
    """Fetch a single queue's stats from the RabbitMQ management HTTP API."""
    import base64
    import urllib.parse
    import urllib.request
    from urllib.parse import urlparse

    parsed = urlparse(RABBITMQ_URL)
    host = parsed.hostname or "localhost"
    mgmt_port = int(os.environ.get("RABBITMQ_MANAGEMENT_PORT", "15672"))
    user = parsed.username or "guest"
    password = parsed.password or "guest"
    # RABBITMQ_URL encodes the default vhost as "/%2F"; decode it first so the
    # quote() below doesn't double-encode it into %252F (which 404s).
    vhost = urllib.parse.unquote((parsed.path or "/").lstrip("/")) or "/"

    url = (
        f"http://{host}:{mgmt_port}/api/queues/"
        f"{urllib.parse.quote(vhost, safe='')}/{urllib.parse.quote(queue_name, safe='')}"
    )
    req = urllib.request.Request(url)
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


@tool()
def get_queue_status(ctx: Context) -> dict:
    """ADMIN ONLY. Depth and consumer count of every delivery queue and dead-letter queue."""
    _admin(ctx)
    result: dict[str, Any] = {}
    for chan, qname in QUEUE_NAMES.items():
        for label, name in ((chan, qname), (f"{chan}_dlq", dead_letter_queue_name(chan))):
            try:
                info = _rabbitmq_management_queue_info(name)
                result[label] = {"messages": info.get("messages"), "consumers": info.get("consumers")}
            except Exception as e:  # noqa: BLE001
                result[label] = {"error": type(e).__name__}
    return {"ok": True, "queues": result}


@tool()
def list_dead_letters(ctx: Context, channel: Optional[str] = None, limit: int = 20, include_content: bool = False) -> dict:
    """ADMIN ONLY. Peek at permanently failed messages without removing them. Message
    text is hidden unless include_content is true."""
    _admin(ctx)
    chans = [_channel(channel)] if channel else list(QUEUE_NAMES.keys())
    limit = max(1, min(int(limit), 50))
    results: dict[str, list] = {}
    conn = None
    try:
        conn = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        ch = conn.channel()
        for chan in chans:
            peeked = []
            for _ in range(limit):
                method, _props, raw = ch.basic_get(queue=dead_letter_queue_name(chan), auto_ack=False)
                if method is None:
                    break
                try:
                    m = json.loads(raw.decode("utf-8", errors="replace"))
                    item = {k: m.get(k) for k in ("message_id", "channel", "to", "provider", "created_at", "app")}
                    if include_content:
                        item["subject"], item["body"] = m.get("subject"), _cap(m.get("body"))
                except Exception:
                    item = {"error": "unreadable message"}
                peeked.append(item)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)  # a peek, not a consume
            results[chan] = peeked
        return {"ok": True, "dead_letters": results}
    finally:
        if conn is not None:
            conn.close()


@tool()
def retry_dead_letter(ctx: Context, channel: str, message_id: str) -> dict:
    """ADMIN ONLY. Put a permanently failed message back on its queue for another
    delivery attempt."""
    _admin(ctx)
    channel = _channel(channel)
    dlq_name = dead_letter_queue_name(channel)
    conn = None
    try:
        conn = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        ch = conn.channel()
        found = None
        found_tag = None
        passed = []
        while True:  # scan for the id, returning everything else to the queue
            method, _props, raw = ch.basic_get(queue=dlq_name, auto_ack=False)
            if method is None:
                break
            try:
                parsed = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                parsed = None
            if parsed and parsed.get("message_id") == message_id:
                found, found_tag = parsed, method.delivery_tag
                break
            passed.append(method.delivery_tag)
        for tag in passed:
            ch.basic_nack(delivery_tag=tag, requeue=True)
        if found is None:
            return {"ok": False, "error": f"message_id {message_id!r} not found in the {channel} dead-letter queue."}
        ch.basic_ack(delivery_tag=found_tag)
        found.pop("_attempt_count", None)
        republish_from_dead_letter(channel, found)
        try:  # the log said "failed"; it is queued again now
            get_repository().update_message_fields(message_id, {"status": "queued", "last_error": None})
        except Exception:
            pass
        return {"ok": True, "message_id": message_id, "requeued": True}
    finally:
        if conn is not None:
            conn.close()


RABBITMQ_LOG_BASE = os.environ.get("RABBITMQ_LOG_BASE", "/data/rabbitmq/log").strip() or "/data/rabbitmq/log"


@tool()
def get_broker_log(ctx: Context, lines: int = 200) -> dict:
    """ADMIN ONLY. Tail RabbitMQ's own log file - broker-level history (startup,
    crashes, disk/memory alarms, refused connections) that get_health and
    get_queue_status can't show, since they only report current up/down and queue
    depth. RabbitMQ itself has no message history to query - once a worker consumes
    and acks a message it's gone from the broker for good; that history lives in the
    message log instead (list_recent_messages, get_message, list_delivery_attempts).

    lines: how many lines to return from the end of the log (capped at 1000)."""
    _admin(ctx)
    lines = max(1, min(int(lines), 1000))
    try:
        names = [f for f in os.listdir(RABBITMQ_LOG_BASE) if f.endswith(".log")]
    except OSError as e:
        return {"ok": False, "error": f"Can't read {RABBITMQ_LOG_BASE!r}: {e}."}
    if not names:
        return {"ok": False, "error": f"No .log files in {RABBITMQ_LOG_BASE!r} yet."}
    names.sort(key=lambda f: os.path.getmtime(os.path.join(RABBITMQ_LOG_BASE, f)), reverse=True)
    path = os.path.join(RABBITMQ_LOG_BASE, names[0])
    with open(path, "r", errors="replace") as f:
        tail = f.readlines()[-lines:]
    return {"ok": True, "file": names[0], "lines": len(tail), "log": "".join(tail)}


_QUERYABLE_COLLECTIONS = {"messages", "attempts"}
_JS_OPERATORS = {"$where", "$function", "$accumulator"}


def _has_js_operator(value: Any) -> bool:
    """Recursively reject Mongo operators that run server-side JS - this tool is a
    read-only inspection shortcut, not a way to execute code in the database."""
    if isinstance(value, dict):
        return any(k in _JS_OPERATORS or _has_js_operator(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_js_operator(v) for v in value)
    return False


@tool()
def query_database(ctx: Context, collection: str, filter: Optional[dict] = None,
                   projection: Optional[list[str]] = None, sort: Optional[list[list]] = None,
                   limit: int = 25) -> dict:
    """ADMIN ONLY. MongoDB (atlas) backend only. A raw, read-only Mongo query against
    the gateway's own message log, for troubleshooting beyond what list_recent_messages
    / get_message cover - compound filters, date ranges, cross-account look-ups. On the
    sqlite backend this returns an error; use list_recent_messages / get_message there
    instead - same data, no query language needed for a database this size.

    collection: 'messages' or 'attempts' (delivery attempts). No other collection is
        exposed here - accounts, settings and provider credentials never are, even to
        an administrator, through this tool.
    filter: a MongoDB filter document, e.g. {"status": "failed", "channel": "sms"}.
        Operators that run server-side code ($where, $function, $accumulator) are
        rejected.
    projection: field names to return (default: everything except encrypted content).
    sort: e.g. [["created_at", -1]].
    limit: capped at 100.
    """
    _admin(ctx)
    if backend_name() != "atlas":
        return {"ok": False, "error": "query_database needs the MongoDB (atlas) backend. This gateway "
                "is on sqlite - use list_recent_messages / get_message instead."}
    coll = (collection or "").strip().lower()
    if coll not in _QUERYABLE_COLLECTIONS:
        return {"ok": False, "error": f"Unknown or unavailable collection {collection!r}. "
                f"Available: {sorted(_QUERYABLE_COLLECTIONS)}."}
    flt = filter or {}
    if _has_js_operator(flt):
        return {"ok": False, "error": "Operators that run server-side code ($where, $function, "
                "$accumulator) aren't allowed."}
    limit = max(1, min(int(limit), 100))

    from app.db.atlas_repository import AtlasRepository

    repo = get_repository()
    if not isinstance(repo, AtlasRepository):
        return {"ok": False, "error": "Not connected to MongoDB."}
    mongo_db = repo.mongo_database()

    proj = None
    if projection:
        proj = {f: 1 for f in projection}
        proj["_id"] = 1
    cursor = mongo_db[coll].find(flt, proj, limit=limit, max_time_ms=5000)
    if sort:
        cursor = cursor.sort([(f, d) for f, d in sort])
    docs = []
    for d in cursor:
        d["_id"] = str(d.get("_id"))
        for k in [k for k in d if k.endswith("_enc")]:
            d.pop(k, None)  # message content stays encrypted - never decrypted for this tool
        docs.append(d)
    return {"ok": True, "collection": coll, "count": len(docs), "documents": docs}
