from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

Channel = Literal["push", "email", "sms", "telegram", "whatsapp"]
EmailType = Literal["txt", "html"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class MessageRequest(BaseModel):
    channel: Channel = Field(description="Where to send: `email`, `sms`, `push`, `telegram` or `whatsapp`.")
    to: Optional[Union[str, list[str]]] = Field(None, description="Recipient(s). An email address for `email`; a phone number with country code, e.g. `+15551234567`, for `sms` and `whatsapp` (omit to use the default phone number); a chat id for `telegram` (omit to use the default chat id); not needed for `push`. May be a list.")
    subject: Optional[str] = Field(None, description="Required for `email`. Used as the title for `push`.")
    body: Optional[str] = Field(None, description="The message text. Required unless `template` is given.")
    template: Optional[str] = Field(None, description="Name of a server-side template to use instead of `body`.")
    emailType: Optional[EmailType] = Field(None, description="`txt` (default) or `html`. Email only.")
    context: dict[str, Any] = Field(default_factory=dict, description="Values for `{{context.key}}` placeholders in `body` or the template.")

    # Which connected provider to use (default: the channel's default provider)
    provider: Optional[str] = Field(None, description="Which connected provider to use, e.g. `sendgrid`. Default: the channel's default provider.")

    # push-only fields (ignored by other channels)
    app: Optional[str] = Field(None, description="Push (Pushover) only: which named app to send from. Default: the gateway's default app.")
    device: Optional[str] = Field(None, description="Push only: send to a specific device name.")
    url: Optional[str] = Field(None, description="Push only: a link to attach (http:// or https://).")
    url_title: Optional[str] = Field(None, description="Push only: text for the attached link.")

    meta: dict[str, Any] = Field(default_factory=dict, description="Optional extra data stored with the message.")

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


_PROVIDER_DESC = "Which connected provider to use. Default: the channel's default provider."
_EMAIL_TEMPLATE_DESC = ("ID (e.g. `tpl_1a2b3c4d5e6f`, shown in the web app's Templates page) or name of an email "
                        "template. Loads its `.txt` body, or its `.html` body when `emailType` is `html`.")
_SMS_TEMPLATE_DESC = "Name of a saved SMS template, e.g. `welcome`. Loads `<name>.txt` from `app/templates/sms/`."
_CONTEXT_DESC = ("Values for the `{{ context.key }}` placeholders in the template, keyed by the part after `context.` "
                 "(`{{ context.name }}` -> `{\"name\": \"Ana\"}`). Spaces inside the braces are optional; the `context.` "
                 "prefix is required. A placeholder with no matching key makes the request fail with a 400. In an HTML email "
                 "each value is made HTML-safe by the gateway: send plain text (line breaks become `<br>`), already-escaped "
                 "text, or basic HTML (`<br>`, `<b>`/`<bold>`, `<i>`/`<italic>`, `<u>`, lists, `<a href>`); other tags show "
                 "as text. A key ending in `_html` is inserted as is.")


class _ChannelMessage(BaseModel):
    """Base for the per-channel request bodies; converts to the internal MessageRequest."""
    _channel: str = ""

    def to_request(self) -> "MessageRequest":
        return MessageRequest(channel=self._channel, **self.model_dump(exclude_none=True))


class EmailMessage(_ChannelMessage):
    _channel = "email"
    to: Union[str, list[str]] = Field(description="Recipient email address, or a list of addresses.")
    subject: str = Field(description="Subject line.")
    body: str = Field(description="The message text.")
    emailType: EmailType = Field("txt", description="`txt` (default) or `html`.")
    provider: Optional[str] = Field(None, description=_PROVIDER_DESC + " Options: `sendgrid`, `mailjet` (only the ones connected in the web app work).")


class EmailTemplateMessage(_ChannelMessage):
    _channel = "email"
    to: Union[str, list[str]] = Field(description="Recipient email address, or a list of addresses.")
    subject: str = Field(description="Subject line. May contain `{{ context.key }}` placeholders.")
    template: str = Field(description=_EMAIL_TEMPLATE_DESC)
    context: dict[str, Any] = Field(default_factory=dict, description=_CONTEXT_DESC)
    emailType: EmailType = Field("txt", description="`txt` (default) uses the template's plain-text body; `html` uses its HTML body.")
    provider: Optional[str] = Field(None, description=_PROVIDER_DESC + " Options: `sendgrid`, `mailjet` (only the ones connected in the web app work).")


class SmsMessage(_ChannelMessage):
    _channel = "sms"
    to: Optional[Union[str, list[str]]] = Field(None, description="Phone number in international format with country code, e.g. `+15551234567` (digits only after the `+`), or a list. The API sends it as given and does not guess a country code. Omit to use the default phone number set in the web app.")
    body: str = Field(description="The message text.")
    provider: Optional[str] = Field(None, description=_PROVIDER_DESC + " Options: `custom_http`, `twilio`, `infobip`, `sinch` (only the ones connected in the web app work).")


class SmsTemplateMessage(_ChannelMessage):
    _channel = "sms"
    to: Optional[Union[str, list[str]]] = Field(None, description="Phone number in international format with country code, e.g. `+15551234567` (digits only after the `+`), or a list. The API sends it as given and does not guess a country code. Omit to use the default phone number set in the web app.")
    template: str = Field(description=_SMS_TEMPLATE_DESC)
    context: dict[str, Any] = Field(default_factory=dict, description=_CONTEXT_DESC)
    provider: Optional[str] = Field(None, description=_PROVIDER_DESC + " Options: `custom_http`, `twilio`, `infobip`, `sinch` (only the ones connected in the web app work).")


class TelegramMessage(_ChannelMessage):
    _channel = "telegram"
    to: Optional[Union[str, list[str]]] = Field(None, description="Chat id (a number, e.g. `123456789`), or a list. Find yours with 'Find chat IDs' in the web app (Channels > Telegram). Omit to use the default chat ID set in the web app.")
    body: str = Field(description="The message text.")
    provider: Optional[str] = Field(None, description=_PROVIDER_DESC + " Options: `bot_api` (only if connected in the web app).")


class WhatsAppMessage(_ChannelMessage):
    _channel = "whatsapp"
    to: Optional[Union[str, list[str]]] = Field(None, description="Phone number in international format with country code, e.g. `+15551234567`, or a list. Omit to use the default recipient set in the web app. Only delivers if this number has messaged your WhatsApp business number in the last 24 hours - see Channels > WhatsApp.")
    body: str = Field(description="The message text.")
    provider: Optional[str] = Field(None, description=_PROVIDER_DESC + " Options: `cloud_api` (only if connected in the web app).")


class PushMessage(_ChannelMessage):
    _channel = "push"
    body: str = Field(description="The message text.")
    subject: Optional[str] = Field(None, description="Notification title.")
    app: Optional[str] = Field(None, description="Pushover only: the **name** of one of your Pushover apps, as added in the web app under Channels > Push (e.g. `alerts`). This is the app's name, not its API token. Default: the default app. An unknown name returns a 400 listing the valid ones.")
    device: Optional[str] = Field(None, description="Pushover only: deliver to one device by its Pushover device name. Default: all your devices.")
    url: Optional[str] = Field(None, description="A link to attach (http:// or https://).")
    url_title: Optional[str] = Field(None, description="Pushover only: text for the attached link.")
    to: Optional[str] = Field(None, description="Override the recipient: a Pushover user or group key, or an ntfy topic. Default: the one saved in the web app.")
    provider: Optional[str] = Field(None, description=_PROVIDER_DESC + " Options: `pushover`, `ntfy` (only the ones connected in the web app work).")


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
