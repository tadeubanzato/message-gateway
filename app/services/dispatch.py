"""
Send a message through the normal pipeline (validation, log, queue, worker) and
wait a few seconds for the delivery result.

Used by the MCP tools and by the Send test button in the web app, so a test is a
real message: it is logged, delivered by the worker, and shows up in the message
log (marked as a test).
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import HTTPException

from app.db import get_repository
from app.schemas import MessageRequest


def send_and_wait(req: MessageRequest, account_id: Optional[str], wait_seconds: int = 8) -> dict[str, Any]:
    from app.main import enqueue_message  # local import: main imports the routes that use this module

    try:
        resp = enqueue_message(req, account_id)
    except HTTPException as e:
        detail = e.detail
        out: dict[str, Any] = {
            "ok": False, "status_code": e.status_code,
            "error": detail.get("error") if isinstance(detail, dict) else str(detail),
        }
        if isinstance(detail, dict):
            out.update({k: detail[k] for k in ("available", "default", "available_apps") if k in detail})
        return out

    ids = [resp.message_id] if getattr(resp, "message_id", None) else list(getattr(resp, "message_ids", None) or [])
    result: dict[str, Any] = {"ok": True, "message_ids": ids, "status": "queued"}
    if not ids or wait_seconds <= 0:
        return result

    repo = get_repository()
    deadline = time.monotonic() + min(int(wait_seconds), 30)
    docs: list[dict[str, Any]] = []
    while True:
        docs = [d for d in (repo.get_message(i) for i in ids) if d]
        if len(docs) == len(ids) and all(d.get("status") != "queued" for d in docs):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    statuses = [d.get("status") for d in docs]
    if statuses and all(s == "delivered" for s in statuses):
        result.update(status="delivered", provider=docs[0].get("provider"))
    elif any(s == "failed" for s in statuses):
        failed = next(d for d in docs if d.get("status") == "failed")
        result.update(ok=False, status="failed", provider=failed.get("provider"),
                      error=failed.get("last_error") or "Delivery failed.")
    else:
        result.update(note="Still being delivered. Check the message log shortly.")
    return result


TEST_TEXT = "Test message from Message Gateway. If you can read this, it works."


def build_test_request(channel: str, to: Optional[str], provider: Optional[str], app: Optional[str]) -> MessageRequest:
    """A short test message, marked meta.test so the log can label it."""
    common = {"provider": (provider or None), "meta": {"test": True}}
    if channel == "push":
        return MessageRequest(channel="push", subject="Message Gateway test", body=TEST_TEXT, app=(app or None), **common)
    if channel == "email":
        return MessageRequest(channel="email", to=to, subject="Message Gateway test", body=TEST_TEXT, emailType="txt", **common)
    return MessageRequest(channel=channel, to=(to or None), body=TEST_TEXT, **common)  # sms, telegram: to + body only
