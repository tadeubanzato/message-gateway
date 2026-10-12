"""
Incoming events from an SMSGate phone: texts it receives, and what happened to the
ones the gateway sent through it.

The phone posts each event to a URL on this gateway. That URL ends in a random
secret the gateway makes up when the webhooks are registered, so only a phone that
was given the address can post to it. If the app's webhook signing key is also
saved here, every event's signature is checked as well.

Received texts are stored in the message log as channel "sms", status "received",
direction "inbound", under the administrator's account - so they show up in the
web app, the API and the MCP tools without anything new to learn.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import get_repository
from app.services import access, message_log, secret_store
from app.services.env import get_env
from app.services.sms.smsgate import SmsGateProvider

PATH = "/api/smsgate/webhook"
EVENTS = ("sms:received", "sms:sent", "sms:delivered", "sms:failed")
_ID_PREFIX = "mg-"  # ids of the webhooks this gateway registers, so it only ever touches its own
_SIGNATURE_MAX_AGE_SECS = 300


def _webhook_id(event: str) -> str:
    return _ID_PREFIX + event.replace(":", "-")


def secret() -> Optional[str]:
    return (get_env("SMSGATE_WEBHOOK_SECRET") or "").strip() or None


def secret_matches(candidate: str) -> bool:
    expected = secret()
    return bool(expected) and hmac.compare_digest(expected.encode(), (candidate or "").encode())


def signature_ok(body: bytes, signature: Optional[str], timestamp: Optional[str]) -> bool:
    """True when no signing key is saved (the URL secret is the only check), or when
    the event carries a fresh, valid HMAC-SHA256 of body + timestamp."""
    key = (get_env("SMSGATE_WEBHOOK_SIGNING_KEY") or "").strip()
    if not key:
        return True
    if not signature or not timestamp:
        return False
    try:
        if abs(time.time() - int(timestamp)) > _SIGNATURE_MAX_AGE_SECS:
            return False
    except ValueError:
        return False
    expected = hmac.new(key.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


# ---------------------------------------------------------------------
# Registering the webhooks with the SMSGate server
# ---------------------------------------------------------------------
def _registered(provider: SmsGateProvider) -> list[dict[str, Any]]:
    _status, raw = provider._request("GET", "/webhooks")
    hooks = json.loads(raw.decode("utf-8", errors="replace") or "[]")
    return [h for h in hooks if isinstance(h, dict) and str(h.get("id") or "").startswith(_ID_PREFIX)]


def status() -> dict[str, Any]:
    try:
        events = sorted(h.get("event") for h in _registered(SmsGateProvider()) if h.get("event"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "enabled": False, "error": f"Could not ask the SMSGate server ({type(e).__name__})."}
    return {"ok": True, "enabled": bool(secret()) and "sms:received" in events, "events": events,
            "signed": bool((get_env("SMSGATE_WEBHOOK_SIGNING_KEY") or "").strip())}


def enable(public_base: str) -> dict[str, Any]:
    """Point the phone's events at this gateway. Safe to run again: it replaces its own registrations."""
    if not public_base.startswith("https://"):
        return {"ok": False, "error": "The phone can only post to an https address. Open this page through your gateway's public https address and try again."}
    provider = SmsGateProvider()
    token = secret()
    if not token:
        token = secrets.token_urlsafe(24)
        secret_store.set_setting("SMSGATE_WEBHOOK_SECRET", token)
    url = f"{public_base}{PATH}/{token}"
    try:
        for hook in _registered(provider):
            provider._request("DELETE", "/webhooks/" + hook["id"])
        for event in EVENTS:
            provider._request("POST", "/webhooks", {"id": _webhook_id(event), "url": url, "event": event})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"The SMSGate server didn't accept the webhooks ({type(e).__name__}: {e})."}
    return status()


def disable() -> dict[str, Any]:
    try:
        provider = SmsGateProvider()
        for hook in _registered(provider):
            provider._request("DELETE", "/webhooks/" + hook["id"])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not remove the webhooks ({type(e).__name__})."}
    secret_store.delete_setting("SMSGATE_WEBHOOK_SECRET")  # the old address stops working at once
    return {"ok": True, "enabled": False, "events": []}


# ---------------------------------------------------------------------
# Handling an event
# ---------------------------------------------------------------------
def _utc_iso(value: Any) -> str:
    """The event's own time as UTC, in the same shape the rest of the message log uses."""
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


def _record_received(event: dict[str, Any], payload: dict[str, Any]) -> str:
    # The phone retries a webhook it didn't see succeed, so the id is derived from
    # the phone's own id for the text: a repeat finds the first copy and stops.
    message_id = "in-" + str(payload.get("messageId") or event.get("id") or secrets.token_hex(8))
    repo = get_repository()
    if repo.get_message(message_id):
        return "duplicate"
    received_at = _utc_iso(payload.get("receivedAt"))
    doc: dict[str, Any] = {
        "message_id": message_id,
        "channel": "sms",
        "direction": "inbound",
        "status": "received",
        "account_id": access.owner_id(),
        "from": payload.get("sender") or payload.get("phoneNumber"),
        "to": payload.get("recipient"),
        "queued_at": received_at,
        "received_at": received_at,
        "provider": SmsGateProvider.name,
        "source": "inbound",
        "sim_number": payload.get("simNumber"),
        "is_test": False,
        "attempts": 0,
    }
    if message_log.store_content_enabled():
        doc["body_enc"] = secret_store.encrypt(str(payload.get("message") or ""))
    repo.insert_message(doc)
    return "stored"


def _record_outcome(kind: str, payload: dict[str, Any]) -> str:
    """What the handset did with a message the gateway sent (its id is the gateway's own)."""
    message_id = str(payload.get("messageId") or "")
    repo = get_repository()
    doc = repo.get_message(message_id) if message_id else None
    if not doc or doc.get("direction") == "inbound":
        return "unknown message"
    at = _utc_iso(payload.get(kind + "At"))
    fields: dict[str, Any] = {"handset_status": kind, "handset_at": at}
    if kind == "failed":
        reason = str(payload.get("reason") or "").strip()
        fields.update(status="failed", failed_at=at,
                      last_error="The phone couldn't send it" + (f": {reason}" if reason else "."))
    elif doc.get("handset_status") == "delivered":
        return "ignored"  # a late "sent" must not hide the delivery receipt
    repo.update_message_fields(message_id, fields)
    return "updated"


def handle(event: dict[str, Any]) -> str:
    name = str(event.get("event") or "")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return "ignored"
    if name == "sms:received":
        return _record_received(event, payload)
    if name in ("sms:sent", "sms:delivered", "sms:failed"):
        return _record_outcome(name.split(":", 1)[1], payload)
    return "ignored"
