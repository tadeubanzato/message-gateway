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

import pika
from mcp.server.fastmcp import Context, FastMCP

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

mcp = FastMCP(
    name="message-gateway",
    streamable_http_path="/mcp",
    instructions=(
        "Message Gateway sends email, SMS and push notifications through the providers its "
        "administrator connected. Map requests to tools like this: 'send a push notification' -> "
        "send_push; 'send an email' -> send_email; 'send / text an SMS to <number>' -> send_sms; "
        "'send a test email/sms/push' -> send_test; anything else -> send_notification. Each send "
        "waits a few seconds and reports delivered or failed, so you can tell the user the outcome. "
        "Use list_providers to see what is connected (and pass provider= to choose one). If a "
        "channel isn't set up, tell the user the administrator must connect it in the web app. "
        "A send can also fail with 'X is turned off on the gateway': that provider is connected but "
        "an administrator switched it off (Channels > that channel > that provider's card) - tell the "
        "user which provider and that they (or the administrator) can turn it back on there, or pick a "
        "different connected provider with provider=. "
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
        raise _Denied(f"Unknown channel {channel!r}. Use email, sms or push.")
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

    channel: 'email', 'sms' or 'push'.
    to: recipient (email address or phone number, or a list). Omit for push.
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
    out = await asyncio.to_thread(dispatch.send_and_wait, req, ident["account_id"], wait_seconds)
    code = out.pop("status_code", None)
    if not out.get("ok") and code == 409 and channel in channels.CATALOG:
        out["setup_page"] = f"{GATEWAY_BASE_URL}/gateway/channels/{channel}"
        out["hint"] = f"The administrator can connect a {channel} provider at the setup_page URL."
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
    """Which providers you can send with. For each channel: the default provider, the
    connected providers you can pass as `provider` to send_notification, and every
    provider the gateway supports."""
    _who(ctx)
    chans = [_channel(channel)] if channel else list(channels.CATALOG)
    out = {
        ch: {
            "default": channels.default_provider(ch),
            "connected": channels.connected_providers(ch),
            "supported": list(channels.CATALOG[ch]["providers"]),
        }
        for ch in chans
    }
    if "push" in out:
        out["push"]["pushover_apps"] = [a["name"] for a in channels.pushover_apps() if a["set"]]
    return {"ok": True, "channels": out}


@tool()
def get_setup_status(ctx: Context) -> dict:
    """Overall state of the gateway: database, and per channel whether it is ready
    (default provider connected), needs attention, or is not set up. Offline check:
    it does not call the providers (administrators can use check_provider_config)."""
    _who(ctx)
    try:
        db_ok = get_repository().ping()
    except Exception:
        db_ok = False
    return {
        "ok": True, "db_backend": backend_name(), "db_ok": db_ok, "web_app": f"{GATEWAY_BASE_URL}/gateway",
        "credentials_unreadable": secret_store.unreadable_count(),
        "channels": {
            s["channel"]: {
                "state": s["state"], "default": s["default"],
                "connected": [c["name"] for c in s["connected"]],
                "setup_page": f"{GATEWAY_BASE_URL}/gateway/channels/{s['channel']}",
                **({"default_phone_number_set": bool(channels.default_sms_number())} if s["channel"] == "sms" else {}),
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

    channel: 'email', 'sms' or 'push'. status: 'queued', 'delivered' or 'failed'.
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
