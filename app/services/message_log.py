"""
Full message log: what was sent, to whom, when, and how delivery went.

Every message gets a record in the configured database (SQLite or Atlas) with
timestamp, channel, recipient, status, provider, attempts and errors. The
subject and body are stored too, encrypted with the same key as provider
credentials, so a database export or an Atlas browser never shows message text.

Set STORE_MESSAGE_CONTENT=0 (env or stored setting) to keep the metadata but
not the content. Logging failures never block delivery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.db import get_repository
from app.services import secret_store
from app.services.env import get_env


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_content_enabled() -> bool:
    return (get_env("STORE_MESSAGE_CONTENT", "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def record_queued(msg: Any, account_id: Optional[str] = None, template: Optional[str] = None) -> None:
    """Create the log entry when a message is accepted. `msg` is a MessageEnqueued."""
    try:
        doc: dict[str, Any] = {
            "message_id": msg.message_id,
            "channel": msg.channel,
            "status": "queued",
            "account_id": account_id,
            "to": msg.to,
            "queued_at": msg.created_at or _now_iso(),
            "template": template,
            "app": msg.app,
            "device": getattr(msg, "device", None),
            "provider": getattr(msg, "provider", None),
            "email_type": msg.emailType,
            "is_test": bool((getattr(msg, "meta", None) or {}).get("test")),
            "attempts": 0,
        }
        if store_content_enabled():
            doc["body_enc"] = secret_store.encrypt(msg.body or "")
            if msg.subject:
                doc["subject_enc"] = secret_store.encrypt(msg.subject)
        get_repository().insert_message(doc)
    except Exception:
        pass  # message logging must never block delivery


def record_attempt(message_id: str, *, attempt: int, provider: Optional[str],
                   provider_message_id: Optional[str], error: Optional[str]) -> None:
    try:
        get_repository().update_message_fields(message_id, {
            "attempts": attempt,
            "provider": provider,
            "provider_message_id": provider_message_id,
            "last_error": error,
            "last_attempt_at": _now_iso(),
        })
    except Exception:
        pass


def record_final(message_id: str, status: str) -> None:
    """status: 'delivered' or 'failed'."""
    try:
        get_repository().update_message_fields(message_id, {
            "status": status,
            ("delivered_at" if status == "delivered" else "failed_at"): _now_iso(),
        })
    except Exception:
        pass


def channel_trend(account_id: str, days: int = 14, channels: tuple[str, ...] = ("email", "sms", "push", "telegram")) -> dict[str, Any]:
    """Per-day, per-channel counts for the last `days` days (UTC), for the home
    page trend chart. Counts every message queued that day, regardless of how
    delivery went. Content is never decrypted for this - only channel and date."""
    today = datetime.now(timezone.utc).date()
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    counts: dict[str, dict[str, int]] = {d: {c: 0 for c in channels} for d in day_keys}
    try:
        docs = get_repository().list_messages(None, None, 5000, account_id=account_id)
    except Exception:
        docs = []
    for d in docs:
        ch = d.get("channel")
        if ch not in channels:
            continue
        day = (d.get("queued_at") or "")[:10]
        if day in counts:
            counts[day][ch] += 1
    return {
        "days": day_keys,
        "series": {c: [counts[d][c] for d in day_keys] for c in channels},
        "totals": {c: sum(counts[d][c] for d in day_keys) for c in channels},
    }


def public_view(doc: dict[str, Any], include_content: bool = True) -> dict[str, Any]:
    """A record safe to return: *_enc fields removed, content decrypted if asked."""
    out = {k: v for k, v in doc.items() if not k.endswith("_enc") and k not in ("_id", "created_at")}
    if include_content:
        body = doc.get("body_enc")
        subject = doc.get("subject_enc")
        out["body"] = secret_store.decrypt(body) if body else None
        if subject:
            out["subject"] = secret_store.decrypt(subject)
        out["content_stored"] = bool(body)
    return out
