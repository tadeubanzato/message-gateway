"""
MCP server — one merged server exposing three tool categories:
  1. Agent-facing send tool
  2. Setup wizard tools (install-time, config-driven, never accepts secrets as arguments)
  3. Operator/debug tools (queue status, message/attempt history, dead letters, retry)

Runs as its own process using FastMCP's streamable-http transport, reachable
at PUBLIC_MCP_URL (default http://localhost:8010/mcp) per the handoff spec.

Config-reload note: check_provider_config assumes the user has already run
`docker compose restart` after editing .env (per the resolved, deliberately
NOT-Docker-socket-based design) — this server does not and must not attempt
to restart the stack itself.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import pika
from mcp.server.fastmcp import FastMCP

from app.broker import (
    QUEUE_NAMES,
    RABBITMQ_URL,
    dead_letter_queue_name,
    republish_from_dead_letter,
)
from app.db import backend_name, get_repository
from app.schemas import MessageEnqueued, MessageRequest
from app.services.email import get_email_provider
from app.services.email import list_providers as list_email_providers
from app.services.push import get_push_provider
from app.services.push import list_providers as list_push_providers
from app.services.sms import get_sms_provider
from app.services.sms import list_providers as list_sms_providers

mcp = FastMCP(name="relay-gateway", streamable_http_path="/mcp")

GATEWAY_BASE_URL = os.environ.get("GATEWAY_BASE_URL", "http://localhost:8000").strip() or "http://localhost:8000"

_SETUP_INSTRUCTIONS: dict[str, dict[str, Any]] = {
    "mailjet": {
        "env_vars": ["MAILJET_API_KEY", "MAILJET_SECRET_KEY", "MAILJET_FROM_EMAIL"],
        "instructions": (
            "Sign in at app.mailjet.com -> Account Settings -> REST API -> API Key "
            "Management. Copy the API Key and Secret Key, and set a verified sender "
            "address as MAILJET_FROM_EMAIL. Set MAILJET_API_KEY, MAILJET_SECRET_KEY, "
            "and MAILJET_FROM_EMAIL in your .env file."
        ),
    },
    "sendgrid": {
        "env_vars": ["SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL"],
        "instructions": (
            "Sign in at app.sendgrid.com -> Settings -> API Keys -> Create API Key "
            "(Full Access or Mail Send). Verify a sender identity for SENDGRID_FROM_EMAIL. "
            "Set SENDGRID_API_KEY and SENDGRID_FROM_EMAIL in your .env file."
        ),
    },
    "twilio": {
        "env_vars": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"],
        "instructions": (
            "Sign in at console.twilio.com -> copy your Account SID and Auth Token "
            "from the dashboard. Buy or use an existing Twilio phone number for "
            "TWILIO_FROM_NUMBER. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and "
            "TWILIO_FROM_NUMBER in your .env file."
        ),
    },
    "infobip": {
        "env_vars": ["INFOBIP_API_KEY", "INFOBIP_BASE_URL", "INFOBIP_FROM"],
        "instructions": (
            "Sign in at portal.infobip.com -> API Keys -> create a key. Your "
            "personalized base URL is shown on the API Keys page. Set INFOBIP_API_KEY, "
            "INFOBIP_BASE_URL, and INFOBIP_FROM (your registered sender) in your .env file."
        ),
    },
    "local_modem": {
        "env_vars": ["SMS_API_BASE_URL", "SMS_API_TOKEN"],
        "instructions": (
            "This provider expects a self-hosted HTTP API in front of ModemManager/mmcli "
            "on your own network (e.g. a Raspberry Pi with a USB modem). Set "
            "SMS_API_BASE_URL to that service's address (e.g. http://<device-ip>:8666) "
            "and SMS_API_TOKEN to its bearer token in your .env file."
        ),
    },
    "pushover": {
        "env_vars": ["PUSHOVER_USER_KEY", "PUSHOVER_APPS", "PUSHOVER_APPTOKEN_DEFAULT"],
        "instructions": (
            "Sign in at pushover.net -> copy your User Key. Create an Application at "
            "pushover.net/apps/build to get an API token. Set PUSHOVER_USER_KEY and "
            "PUSHOVER_APPTOKEN_DEFAULT in your .env file. If you want multiple named "
            "apps, set PUSHOVER_APPS=\"name:ENV_VAR,name2:ENV_VAR2\" mapping app names "
            "to your own env var names, and define each of those env vars with its "
            "own Pushover application token."
        ),
    },
    "ntfy": {
        "env_vars": ["NTFY_TOPIC", "NTFY_SERVER_URL"],
        "instructions": (
            "ntfy is free and open source. Pick a hard-to-guess topic name (e.g. "
            "relay-gateway-yourname) and set NTFY_TOPIC to it. Leave NTFY_SERVER_URL "
            "unset to use the public ntfy.sh instance, or point it at your own "
            "self-hosted ntfy server. Subscribe to the topic in the ntfy app to "
            "receive notifications."
        ),
    },
}

_PROVIDER_GETTERS = {
    "email": get_email_provider,
    "sms": get_sms_provider,
    "push": get_push_provider,
}

_PROVIDER_LISTERS = {
    "email": list_email_providers,
    "sms": list_sms_providers,
    "push": list_push_providers,
}


# ---------------------------------------------------------------------
# 1. Agent-facing: send
# ---------------------------------------------------------------------
@mcp.tool()
def send_notification(
    channel: str,
    to: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    template: Optional[str] = None,
    context: Optional[dict] = None,
) -> dict:
    """Send a notification (email, sms, or push) through the gateway.

    channel: 'email', 'sms', or 'push'.
    to: recipient (email address, phone number, or omit for push to use the
        default configured recipient).
    subject: required for email; used as the push title if provided.
    body: message content. Required unless `template` is given.
    template: name of a server-side template to render instead of `body`.
    context: dict of values to substitute into {{context.key}} placeholders
        in the body/template.
    """
    req = MessageRequest(
        channel=channel, to=to, subject=subject, body=body,
        template=template, context=context or {},
    )
    from app.main import _load_template_text, _render_context  # local import avoids circularity

    used_template = (req.template or "").strip() or None
    base_text = _load_template_text(used_template, req=req) if used_template else (req.body or "")
    final_body = _render_context(base_text, req.context)

    recipients = req.to_list_deduped() or ([""] if channel == "push" else [])
    if not recipients:
        return {"ok": False, "error": "Missing 'to' recipient(s)"}

    repo = get_repository()
    message_ids = []
    for recipient in recipients:
        msg = MessageEnqueued.from_request(req.model_copy(update={"to": recipient, "body": final_body}))
        try:
            repo.insert_message({"message_id": msg.message_id, "channel": msg.channel, "status": "queued", "to": recipient})
        except Exception:
            pass
        from app.broker import publish_message

        publish_message(msg)
        message_ids.append(msg.message_id)

    return {"ok": True, "message_ids": message_ids}


# ---------------------------------------------------------------------
# 2. Setup wizard
# ---------------------------------------------------------------------
@mcp.tool()
def list_providers(channel: str) -> dict:
    """List available provider options for a channel ('email', 'sms', or 'push')."""
    lister = _PROVIDER_LISTERS.get(channel)
    if not lister:
        return {"ok": False, "error": f"Unknown channel {channel!r}. Use email, sms, or push."}
    return {"ok": True, "channel": channel, "providers": lister()}


@mcp.tool()
def get_setup_instructions(provider: str) -> dict:
    """Get the env vars a provider needs and where to obtain their values.

    Does NOT accept or return a place to paste secret values — this tool
    only tells you which .env keys to set and where to get them. Edit your
    .env file directly, then run check_provider_config to verify.
    """
    info = _SETUP_INSTRUCTIONS.get(provider)
    if not info:
        return {"ok": False, "error": f"Unknown provider {provider!r}."}
    return {"ok": True, "provider": provider, **info}


@mcp.tool()
def check_provider_config(channel: str) -> dict:
    """Verify the currently-configured provider for a channel has valid
    credentials, by making a lightweight authenticated call to its API.

    If you just edited .env, restart the gateway first
    (`docker compose restart`) so the new values are loaded, then call this.
    """
    getter = _PROVIDER_GETTERS.get(channel)
    if not getter:
        return {"ok": False, "error": f"Unknown channel {channel!r}. Use email, sms, or push."}
    try:
        provider = getter()
    except SystemExit as e:
        return {"ok": False, "error": str(e)}
    result = provider.check_config()
    return {"ok": result.ok, "provider": result.provider, "error": result.error, "status_code": result.status_code}


@mcp.tool()
def get_setup_status() -> dict:
    """Overall setup progress: which provider is active per channel, and
    whether its credentials currently check out."""
    status = {"db_backend": backend_name(), "db_ok": get_repository().ping()}
    for chan, getter in _PROVIDER_GETTERS.items():
        try:
            provider = getter()
            result = provider.check_config()
            status[chan] = {"provider": provider.name, "configured": result.ok, "error": result.error}
        except SystemExit as e:
            status[chan] = {"provider": None, "configured": False, "error": str(e)}
    return status


# ---------------------------------------------------------------------
# 3. Operator-facing: read/inspect + act
# ---------------------------------------------------------------------
@mcp.tool()
def get_health() -> dict:
    """Overall gateway health: API, database, and RabbitMQ reachability."""
    db_ok = False
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

    return {"ok": True, "db_backend": backend_name(), "db_ok": db_ok, "rabbitmq_ok": rabbit_ok}


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
    vhost = (parsed.path or "/").lstrip("/") or "/"

    url = (
        f"http://{host}:{mgmt_port}/api/queues/"
        f"{urllib.parse.quote(vhost, safe='')}/{urllib.parse.quote(queue_name, safe='')}"
    )
    req = urllib.request.Request(url)
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


@mcp.tool()
def get_queue_status() -> dict:
    """Queue depth and consumer count for each channel's queue and dead-letter queue."""
    result: dict[str, Any] = {}
    for chan, queue_name in QUEUE_NAMES.items():
        for label, qn in [(chan, queue_name), (f"{chan}_dlq", dead_letter_queue_name(chan))]:
            try:
                info = _rabbitmq_management_queue_info(qn)
                result[label] = {"messages": info.get("messages"), "consumers": info.get("consumers")}
            except Exception as e:
                result[label] = {"error": str(e)}
    return {"ok": True, "queues": result}


@mcp.tool()
def list_recent_messages(channel: Optional[str] = None, status: Optional[str] = None, limit: int = 20) -> dict:
    """List recently-enqueued messages, optionally filtered by channel and/or status
    ('queued', 'delivered', 'failed')."""
    try:
        msgs = get_repository().list_messages(channel, status, limit)
        return {"ok": True, "messages": msgs}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def list_delivery_attempts(message_id: str) -> dict:
    """List delivery attempts recorded for a given message_id."""
    try:
        attempts = get_repository().list_attempts(message_id)
        return {"ok": True, "attempts": attempts}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def list_dead_letters(channel: Optional[str] = None, limit: int = 20) -> dict:
    """Peek at dead-lettered (permanently failed) messages without consuming them."""
    channels = [channel] if channel else list(QUEUE_NAMES.keys())
    results: dict[str, list] = {}
    conn = None
    try:
        conn = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        ch = conn.channel()
        for chan in channels:
            dlq_name = dead_letter_queue_name(chan)
            peeked = []
            for _ in range(limit):
                method, properties, body = ch.basic_get(queue=dlq_name, auto_ack=False)
                if method is None:
                    break
                try:
                    peeked.append(json.loads(body.decode("utf-8", errors="replace")))
                except Exception:
                    peeked.append({"raw": body.decode("utf-8", errors="replace")})
                # Requeue immediately — this is a peek, not a consume.
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            results[chan] = peeked
        return {"ok": True, "dead_letters": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn is not None:
            conn.close()


@mcp.tool()
def retry_dead_letter(channel: str, message_id: str) -> dict:
    """Find a dead-lettered message by message_id on the given channel's DLQ
    and republish it to the real queue for redelivery. Removes it from the DLQ."""
    dlq_name = dead_letter_queue_name(channel)
    conn = None
    try:
        conn = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        ch = conn.channel()
        found = None
        found_tag = None
        # Scan the DLQ for the matching message_id, nacking-with-requeue
        # everything we pass over so nothing else is lost.
        scanned = []
        while True:
            method, properties, body = ch.basic_get(queue=dlq_name, auto_ack=False)
            if method is None:
                break
            try:
                parsed = json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                parsed = None
            if parsed and parsed.get("message_id") == message_id:
                found = parsed
                found_tag = method.delivery_tag
                break
            scanned.append(method.delivery_tag)

        for tag in scanned:
            ch.basic_nack(delivery_tag=tag, requeue=True)

        if found is None:
            if found_tag is not None:
                ch.basic_nack(delivery_tag=found_tag, requeue=True)
            return {"ok": False, "error": f"message_id {message_id!r} not found in {dlq_name}"}

        ch.basic_ack(delivery_tag=found_tag)
        found.pop("_attempt_count", None)
        republish_from_dead_letter(channel, found)
        return {"ok": True, "message_id": message_id, "requeued": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn is not None:
            conn.close()


# Note: this module is imported by app/main.py, which mounts mcp's routes
# into the main FastAPI app (same port, path /mcp) rather than running this
# as a standalone process. No __main__ entrypoint needed here.
