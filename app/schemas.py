from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

Channel = Literal["push", "email", "sms"]
EmailType = Literal["txt", "html"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class MessageRequest(BaseModel):
    channel: Channel
    to: Optional[Union[str, list[str]]] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    template: Optional[str] = None
    emailType: Optional[EmailType] = None
    context: dict[str, Any] = Field(default_factory=dict)

    # Which connected provider to use (default: the channel's default provider)
    provider: Optional[str] = None

    # push-only fields (ignored by other channels)
    app: Optional[str] = None
    device: Optional[str] = None
    url: Optional[str] = None
    url_title: Optional[str] = None

    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_request(self) -> "MessageRequest":
        template = (self.template or "").strip()
        body = (self.body or "").strip()

        if not template and not body:
            raise ValueError("Either 'body' or 'template' must be provided")
        if self.body is not None and body == "" and not template:
            raise ValueError("'body' must be non-empty")

        # Email always needs a recipient. SMS may omit it when a default phone number is
        # set (enqueue_message fills it in, or rejects the message if there is none).
        if self.channel == "email" and self.to is None:
            raise ValueError("'to' is required for email messages")

        if self.channel == "email":
            if not (self.subject or "").strip():
                raise ValueError("'subject' is required for email messages")
            if self.emailType is None:
                self.emailType = "txt"

            raw = self.to if isinstance(self.to, list) else [self.to]
            invalid: list[str] = []
            for r in raw:
                if not isinstance(r, str):
                    invalid.append(str(r))
                    continue
                rr = r.strip()
                if not rr or not _EMAIL_RE.match(rr):
                    invalid.append(rr or "<empty>")
            if invalid:
                raise ValueError(f"Invalid email recipient(s): {invalid}")

        if self.channel != "push":
            self.url = None
            self.url_title = None
        else:
            u = (self.url or "").strip()
            if u:
                if not _URL_RE.match(u):
                    raise ValueError("'url' must start with http:// or https://")
                self.url = u
            t = (self.url_title or "").strip()
            if t:
                self.url_title = t

        return self

    def to_list_deduped(self) -> list[str]:
        if self.to is None:
            return []
        raw = self.to if isinstance(self.to, list) else [self.to]
        seen: set[str] = set()
        out: list[str] = []
        for r in raw:
            if not isinstance(r, str):
                continue
            rr = r.strip()
            if not rr or rr in seen:
                continue
            seen.add(rr)
            out.append(rr)
        return out


class MessageEnqueued(BaseModel):
    message_id: str
    created_at: str
    channel: Channel
    to: str
    subject: Optional[str] = None
    body: str
    emailType: Optional[EmailType] = None
    provider: Optional[str] = None
    app: Optional[str] = None
    device: Optional[str] = None
    url: Optional[str] = None
    url_title: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_request(req: "MessageRequest") -> "MessageEnqueued":
        now = datetime.now(timezone.utc).isoformat()
        to_value = req.to if isinstance(req.to, str) else ""
        return MessageEnqueued(
            message_id=str(uuid4()),
            created_at=now,
            channel=req.channel,
            to=to_value,
            subject=req.subject,
            body=req.body or "",
            emailType=req.emailType,
            provider=(req.provider or "").strip().lower() or None,
            app=req.app,
            device=req.device,
            url=req.url,
            url_title=req.url_title,
            meta=req.meta or {},
        )


class MessageResponse(BaseModel):
    status: Literal["queued"]
    message_id: Optional[str] = None
    message_ids: Optional[list[str]] = None
    to_deduped: Optional[list[str]] = None
    template: Optional[str] = None
