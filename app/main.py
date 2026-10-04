import contextlib
import os
import re
import threading
import time
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from scalar_fastapi import get_scalar_api_reference

from app import bootstrap
from app.auth import require_api_key
from app.broker import publish_message
from app.db import backend_name, get_repository
from app.mcp_server.auth import McpAuthMiddleware
from app.mcp_server.server import mcp
from app.routes.onboarding import router as onboarding_router
from app.routes.portal import router as portal_router
from app.routes.portal_ui import router as portal_ui_router
from app.services import access, channels, email_templates, html_safe, key_check, message_log
from app.services.env import public_base_url
from app.version import APP_NAME, APP_VERSION
from app.schemas import EmailMessage, EmailTemplateMessage, MessageEnqueued, PushMessage, SmsMessage, SmsTemplateMessage, TelegramMessage, WhatsAppMessage, MessageRequest, MessageResponse

# The MCP server is served from this same FastAPI app (same port, path
# /mcp) rather than run as a separate process/port. Two gotchas discovered
# while building this, confirmed by testing directly rather than assuming:
#
# 1. FastMCP's streamable_http_app() has its own lifespan (starts/stops its
#    session manager) that must be combined with FastAPI's own lifespan via
#    this async contextmanager — a plain mount() does NOT propagate a
#    sub-app's lifespan, and calling the tool without this raises
#    "RuntimeError: Task group is not initialized" on first request.
#
# 2. app.mount("/", mcp_asgi_app) shadows EVERY other route in this app
#    (confirmed: /health returned 404 once mounted this way), because
#    Starlette's Mount matches by prefix and "/" matches everything. The
#    fix is to copy the MCP sub-app's routes directly into this app's
#    router instead of mounting the whole sub-app.
RETENTION_SECONDS = 90 * 24 * 3600


def _purge_loop() -> None:
    """SQLite has no TTL indexes, so old log entries are deleted here hourly.
    (Atlas expires them itself via TTL indexes; its purge is a no-op.)"""
    while True:
        try:
            get_repository().purge_old_messages(RETENTION_SECONDS)
        except Exception:
            pass
        time.sleep(3600)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    get_repository()  # initializes schema/indexes for whichever backend is active
    try:
        access.ensure_roles()
    except Exception:
        pass  # a labelling problem must never stop the gateway from starting
    try:
        key_check.log_startup()
    except Exception:
        pass  # a diagnostic must never stop the gateway from starting
    threading.Thread(target=_purge_loop, name="log-purge", daemon=True).start()
    async with mcp.session_manager.run():
        yield


API_DESCRIPTION = """
Send **email, SMS and push notifications** through one API, using the providers you connected in the web app.

## Authentication

Every request must carry **two credentials**, sent as headers. You create both in the gateway's web app.

| Header | What it is | Looks like |
|---|---|---|
| `X-User-Key` | Identifies your account. It never changes. | `gw_user_...` |
| `X-API-Token` | The secret for one API key. Shown **once**, when created. | `gw_tok_...` |

### Get your credentials

1. Open the web app (the same address as this page, e.g. `http://localhost:8010/gateway`) and sign in.
2. Go to **API keys** in the menu (`/gateway/keys`).
3. Copy your **user key** from the top of the page.
4. Under **Keys**, type a name for the program that will call the API (e.g. `my-website`) and click **Create key**.
5. Copy the **token** right away. It is displayed only once.

### How the exchange works

There is no separate login call and no session: the two headers go on **every** request.
The gateway looks up your account by `X-User-Key`, hashes the `X-API-Token` you sent and compares it with the
stored hash of your active tokens. Only the hash is stored, never the token itself, so **a lost token can't be
shown again**.

```bash
curl -X POST http://localhost:8010/v1/messages/sms \
  -H "X-User-Key: gw_user_..." \
  -H "X-API-Token: gw_tok_..." \
  -H "Content-Type: application/json" \
  -d '{"to": "+15551234567", "body": "Hello"}'
```

### Managing keys

- **One key per program.** Each key has its own name and token, so you can revoke one without touching the others.
- **Lost or leaked a token?** On the API keys page click **Replace token** (or create a key with the same name).
  A new token is shown once and the old one **stops working immediately**.
- **Delete** a key to revoke it for good.
- Requests with a missing, wrong or revoked credential get `401 Unauthorized`.

To try requests from this page, click **Authenticate** and enter both values.

## Responses and errors

A successful send returns `200` right away with `status: "queued"`: the message is validated, logged and
delivered in the background, with retries.

```json
{ "status": "queued", "message_id": "6f1c2b0e-..." }
```

**Several recipients.** `to` may be a list, or one string with the recipients separated by commas, semicolons or
new lines (`"a@x.com, b@x.com"`). This works the same for email, SMS, Telegram and WhatsApp. Duplicates are removed and each
recipient gets its own message, so the response has `message_ids` (one per recipient) and `to_deduped` instead of `message_id`.

**Checking delivery.** Delivery is asynchronous, so `queued` does not mean delivered. See the result and every
attempt on the **Messages** page of the web app (`/gateway/messages`).

Errors return JSON with a `detail`:

| Status | Meaning | Example `detail` |
|---|---|---|
| `400` | Bad request: missing or invalid field, unknown provider, unknown app or WhatsApp (Gakai) account, unknown template, missing `context` value | `{"error": "Provider 'foo' isn't set up for email.", "available": ["sendgrid"], "default": "sendgrid"}` |
| `401` | Missing, wrong or revoked `X-User-Key` / `X-API-Token` | `"Unauthorized"` |
| `403` | The provider that would send this - the one you named, or the channel's default - is turned off (Channels > that channel > that provider's card) | `{"error": "Mailjet is turned off on the gateway.", "channel": "email", "provider": "mailjet"}` |
| `409` | No provider is connected for the channel yet (an administrator connects one in the web app) | `{"error": "No sms provider is connected yet...", "available": [], "default": "custom_http"}` |
| `422` | The JSON body is malformed or a required field is missing | FastAPI validation details |

## AI agents (MCP)

The gateway runs an [MCP](https://modelcontextprotocol.io) server at `/mcp` (streamable HTTP), registered as
**message-gateway**, secured with the same `X-User-Key` / `X-API-Token` headers as the HTTP API above. Any
MCP-capable agent - Claude Code, Codex, Claude Desktop, ChatGPT with a custom connector - can connect to it directly;
no separate install or SDK needed.

```
claude mcp add --transport http message-gateway http://localhost:8010/mcp \
  --header "X-User-Key: gw_user_..." --header "X-API-Token: gw_tok_..."
```

That exact command (with your real values) is shown once right after you create or replace an API key, and the
**About** page in the web app has a short natural-language prompt you can paste into any agent instead. Once
connected, an agent discovers the available tools itself; broadly, they cover:

- **Sending** - `send_email`, `send_sms`, `send_push`, `send_telegram`, `send_whatsapp`, `send_test`, and a general `send_notification`.
  Same validation, logging and on/off checks as this REST API - a message through a turned-off provider gets the
  same `403` shown above.
- **Discovery** - `list_providers`, `get_setup_status`, `get_setup_instructions`, `get_health`.
- **Your message history** - `list_recent_messages`, `get_message`, `list_delivery_attempts` (scoped to messages
  sent under the connecting account's own keys).
- **Administrator troubleshooting** (the gateway owner's key only) - `check_provider_config`, `get_queue_status`,
  `list_dead_letters`, `retry_dead_letter`.

Provider credentials, API keys, and settings are never exposed through MCP - those stay in the web app.

## Email templates (IDs and `context`)

Build email templates in the web app under **Templates** (administrators, once Email is set up): paste or upload
HTML, edit it in the preview, and select text to turn it into a placeholder such as `{{ context.name }}`. Every
saved template gets an **ID** (like `tpl_1a2b3c4d5e6f`). Your app sends that ID as `template`, plus a `context`
object with each recipient's values:

```json
{
  "to": "ana@example.com",
  "subject": "Welcome, {{ context.name }}",
  "template": "tpl_1a2b3c4d5e6f",
  "emailType": "html",
  "context": { "name": "Ana" }
}
```

- A placeholder must start with `context.`; spaces inside the braces are optional (`{{ context.name }}` and
  `{{context.name}}` both work). The `context` key is what follows `context.` - here `name`, not `context.name`.
  A bare `{{ name }}` is never filled in.
- `template` takes the ID or the template's name. Built-in file templates (like `welcome`) have no ID and are sent by name.
- The **Templates** page shows the exact JSON (and a `curl` command) for each template. To read it from your code, call
  `GET /v1/templates/email` (every template with its ID and required keys) or `GET /v1/templates/email/{template}`.
- A placeholder with no value in `context` is a `400`. Templates are stored in the gateway's database.

## Quick start

Pick the endpoint for your channel: **Email**, **SMS** or **Push**. Email and SMS each have a plain-text endpoint and a template endpoint.
Add `provider` to choose a specific connected provider; without it the channel's default is used, unless it's
turned off (`403`) - see **Errors** above.
Delivery happens in the background and the response returns a `message_id`.
"""

API_TAGS = [
    {"name": "Email", "description": "Send an email, as plain text or from a saved template."},
    {"name": "SMS", "description": "Send a text message, as plain text or from a saved template."},
    {"name": "Push", "description": "Send a push notification."},
    {"name": "Telegram", "description": "Send a Telegram message via a bot."},
    {"name": "WhatsApp", "description": "Send a WhatsApp message via the WhatsApp Business Platform (Cloud API) or one or more accounts on a Gakai server."},
    {"name": "Templates", "description": "Look up the email and SMS templates you can send by ID or name, and the `context` values each needs."},
]

GATEWAY_BASE_URL = (os.environ.get("GATEWAY_BASE_URL") or "http://localhost:8010").strip().rstrip("/")

app = FastAPI(title=APP_NAME, servers=[{"url": GATEWAY_BASE_URL, "description": "This gateway"}], version=APP_VERSION, description=API_DESCRIPTION, openapi_tags=API_TAGS, lifespan=_lifespan,
              docs_url=None, redoc_url=None,  # Scalar at /api/docs is the one API reference
              openapi_url="/api/openapi.json")

# The MCP endpoint requires the same API key and token as the HTTP API.
app.add_middleware(McpAuthMiddleware)

# One stylesheet for every page (setup, portal, get-started).
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

for _route in mcp.streamable_http_app().routes:
    app.router.routes.append(_route)

_startup_time = time.time()


SMS_TEMPLATE_DIR = os.environ.get("SMS_TEMPLATE_DIR", "/app/templates/sms").strip() or "/app/templates/sms"
EMAIL_TEMPLATE_DIR = os.environ.get("EMAIL_TEMPLATE_DIR", "/app/templates/email").strip() or "/app/templates/email"
TEMPLATE_STRICT = os.environ.get("TEMPLATE_STRICT", "true").strip().lower() in ("1", "true", "yes", "y")

_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_CONTEXT_TOKEN_RE = re.compile(r"\{\{(?:\s|&nbsp;)*context\.([A-Za-z0-9_]+)(?:\s|&nbsp;)*\}\}")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _template_dir_for_channel(channel: str) -> str:
    return EMAIL_TEMPLATE_DIR if channel == "email" else SMS_TEMPLATE_DIR


def _template_ext_for_request(req: MessageRequest) -> str:
    if req.channel != "email":
        return "txt"
    return "html" if (req.emailType == "html") else "txt"


def _load_template_text(template_name: str, req: MessageRequest) -> str:
    name = (template_name or "").strip()
    if not name or not _TEMPLATE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Template not found")
    ext = _template_ext_for_request(req)
    if req.channel == "email":
        text = email_templates.read(name, ext)
        if text is None:
            raise HTTPException(status_code=400, detail="Template not found")
        return text
    base_dir = _template_dir_for_channel(req.channel)
    path = os.path.join(base_dir, f"{name}.{ext}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="Template not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load template")


def _extract_required_context_keys(text: str) -> list[str]:
    return sorted(set(_CONTEXT_TOKEN_RE.findall(text or "")))


def _render_context(text: str, ctx: dict, html: bool = False) -> str:
    """Fill {{ context.key }}. For an HTML email the gateway makes each value HTML-safe itself
    (see services/html_safe.py), so the caller can send plain text or already-escaped text."""
    context = ctx or {}

    def repl(match: re.Match) -> str:
        key = match.group(1)
        val = context.get(key)
        if val is None:
            if TEMPLATE_STRICT:
                raise HTTPException(status_code=400, detail=f"Missing context key: {key}")
            return ""
        if html and not html_safe.is_trusted_key(key):
            return html_safe.to_html(val)
        return str(val)

    return _CONTEXT_TOKEN_RE.sub(repl, text)


def _validate_email_recipients_or_400(recipients: list[str]) -> None:
    invalid = [e for e in recipients if not _EMAIL_RE.match(e)]
    if invalid:
        valid = [e for e in recipients if e not in set(invalid)]
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid email recipient(s)", "invalid_emails": invalid, "valid_emails": valid},
        )


app.include_router(onboarding_router, include_in_schema=False)
app.include_router(portal_router, include_in_schema=False)
app.include_router(portal_ui_router, include_in_schema=False)


@app.get("/health", include_in_schema=False, tags=["System"], summary="Health check")
def health():
    repo_ok = False
    try:
        repo_ok = get_repository().ping()
    except Exception:
        repo_ok = False
    return {
        "ok": True, "db_backend": backend_name(), "db_ok": repo_ok,
        "data_persistent": bootstrap.data_is_persistent(),
    }


@app.get("/api/docs", include_in_schema=False)
def scalar_docs():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url, title=f"{app.title} API", dark_mode=True,
        persist_auth=True, hide_models=True, default_open_all_tags=True, hide_download_button=True,
    )


@app.get("/get-started", response_class=HTMLResponse, include_in_schema=False)
def get_started(request: Request):
    mcp_url = f"{public_base_url(request)}/mcp"
    prompt = (
        f"Connect to the MCP server at {mcp_url} and run through its setup wizard "
        "to configure this message gateway."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Get started - {APP_NAME}</title>
  <link rel="stylesheet" href="/static/app.css" />
</head>
<body>
  <main class="page">
    <h1>{APP_NAME}</h1>
    <p class="sub">Two ways to set up your gateway.</p>
    <div class="card">
      <h2>In your browser</h2>
      <p style="margin-top:0">The guided setup walks you through the database, your providers and a test message.</p>
      <a class="btn" href="/">Open setup</a>
    </div>
    <div class="card">
      <h2>With an AI agent</h2>
      <p class="muted" style="margin-top:0">MCP server: <code>{mcp_url}</code>. Paste this prompt into Claude Code, Codex or any MCP-capable agent:</p>
      <pre><code id="prompt-text">{prompt}</code></pre>
      <button class="btn ghost sm" onclick="copyText(document.getElementById('prompt-text').innerText, this)">Copy prompt</button>
    </div>
    <p class="muted"><a href="/api/docs">API reference</a></p>
  </main>
  <script src="/static/copy.js"></script>
</body>
</html>"""


@app.get("/v1/auth/whoami", include_in_schema=False, tags=["Auth"], summary="Check your credentials")
def whoami(auth: dict = Depends(require_api_key)):
    return {"ok": True, "account_id": auth.get("account_id"), "user_key": auth.get("user_key")}


_TEMPLATE_LOOKUP_RESPONSES = {
    400: {"description": "Unknown template."},
    401: {"description": "Missing or invalid `X-User-Key` / `X-API-Token`."},
}


def _example_request(channel: str, template: str, keys: list[str], email_type: str = "txt") -> dict:
    ctx = {k: k for k in keys}
    if channel == "email":
        return {"to": "ana@example.com", "subject": "Subject", "template": template, "emailType": email_type, "context": ctx}
    return {"to": "+15551234567", "template": template, "context": ctx}


@app.get("/v1/templates/sms/{template_name}", tags=["Templates"], summary="Get an SMS template",
         description="The `context` keys an SMS template needs, and a ready-to-send example request. SMS templates are "
                     "files in `app/templates/sms/`, referenced by name.", responses=_TEMPLATE_LOOKUP_RESPONSES)
def get_sms_template_expected_context(template_name: str, auth: dict = Depends(require_api_key)):
    req = MessageRequest(channel="sms", to="+10000000000", body="x")
    text = _load_template_text(template_name, req=req)
    keys = _extract_required_context_keys(text)
    return {
        "template": template_name, "channel": "sms", "required_context_keys": keys,
        "example_context": {k: k for k in keys}, "template_strict": TEMPLATE_STRICT,
        "example_request": _example_request("sms", template_name, keys),
    }


@app.get("/v1/templates/email", tags=["Templates"], summary="List email templates",
         description="Every email template you can send: the ones saved in the web app's Templates page (with an `id`) and "
                     "the built-in files (`id` is null; send those by `name`). `required_context_keys` are the keys "
                     "your `context` must contain: the part after `context.` in each `{{ context.key }}` placeholder.",
         responses={401: _TEMPLATE_LOOKUP_RESPONSES[401]})
def list_email_templates(auth: dict = Depends(require_api_key)):
    return {"templates": [{"id": t["id"], "name": t["name"], "source": t["source"], "required_context_keys": t["keys"],
                           "has_html": t["has_html"], "has_txt": t["has_txt"]} for t in email_templates.list_templates()]}


@app.get("/v1/templates/email/{template}", tags=["Templates"], summary="Get an email template",
         description="The `context` keys one email template needs, and a ready-to-send example request. `template` is the "
                     "template ID (e.g. `tpl_1a2b3c4d5e6f`) or its name. `emailType` (`html` or `txt`, default `txt`) "
                     "picks which body the keys are read from.", responses=_TEMPLATE_LOOKUP_RESPONSES)
def get_email_template_expected_context(template: str, auth: dict = Depends(require_api_key), emailType: str = "txt"):
    et = "html" if str(emailType).strip().lower() == "html" else "txt"
    req = MessageRequest(channel="email", to="x@y.z", subject="x", body="x", emailType=et)  # type: ignore[arg-type]
    text = _load_template_text(template, req=req)
    found = email_templates.get(template) or {}
    keys = _extract_required_context_keys(text)
    return {
        "template": template, "id": found.get("id"), "name": found.get("name", template), "channel": "email",
        "emailType": et, "required_context_keys": keys,
        "example_context": {k: k for k in keys}, "template_strict": TEMPLATE_STRICT,
        "example_request": _example_request("email", found.get("id") or template, keys, et),
    }


def enqueue_message(req: MessageRequest, account_id: Optional[str], source: str = "api") -> MessageResponse:
    """Validate a message request, log it and queue it for delivery. Shared by the
    HTTP API, the web app's Send test button, and the MCP server. Raises HTTPException
    on invalid input.

    source: recorded on the message log - 'api', 'portal' or 'mcp' (see message_log.record_queued)."""
    input_was_list = isinstance(req.to, list)
    channel = req.channel
    recipients = req.to_list_deduped()

    if not recipients:
        if channel == "push":
            recipients = [""]
        elif channel == "sms" and channels.default_sms_number():
            recipients = [channels.default_sms_number()]
        elif channel == "sms":
            raise HTTPException(
                status_code=400,
                detail="Missing 'to'. Give a phone number, or ask the administrator to set a default phone number (Channels > SMS).",
            )
        elif channel == "telegram" and channels.default_telegram_chat_id():
            recipients = [channels.default_telegram_chat_id()]
        elif channel == "telegram":
            raise HTTPException(
                status_code=400,
                detail="Missing 'to'. Give a chat ID, or ask the administrator to set a default chat ID (Channels > Telegram).",
            )
        elif channel == "whatsapp" and channels.default_whatsapp_number():
            recipients = [channels.default_whatsapp_number()]
        elif channel == "whatsapp":
            raise HTTPException(
                status_code=400,
                detail="Missing 'to'. Give a phone number, or ask the administrator to set a default recipient (Channels > WhatsApp).",
            )
        else:
            raise HTTPException(status_code=400, detail="Missing 'to' recipient(s)")

    if channel == "email":
        if not (req.subject or "").strip():
            raise HTTPException(status_code=400, detail="Missing 'subject' for email message")
        _validate_email_recipients_or_400(recipients)

    used_template = (req.template or "").strip() or None
    base_text = _load_template_text(used_template, req=req) if used_template else (req.body or "")
    final_body = _render_context(base_text, req.context, html=(channel == "email" and req.emailType == "html"))

    final_subject = None
    if channel == "email":
        final_subject = _render_context(req.subject or "", req.context)

    requested = (req.provider or "").strip().lower() or None
    connected = channels.connected_providers(channel)
    if not requested and channels.default_provider(channel) not in connected:
        # Refuse now, with a clear reason, instead of queueing a message that can never be delivered.
        raise HTTPException(
            status_code=409,
            detail={
                "error": (
                    f"No {channel} provider is connected yet. The gateway administrator needs to connect one"
                    if not connected else
                    f"The default {channel} provider isn't connected. Pass \"provider\" to choose one of the connected providers, or ask the administrator to fix the default"
                ),
                "available": connected, "default": channels.default_provider(channel),
            },
        )
    if requested:
        if requested not in connected:
            raise HTTPException(
                status_code=400,
                detail={"error": f"Provider '{requested}' isn't set up for {channel}.",
                        "available": connected, "default": channels.default_provider(channel)},
            )

    effective = requested or channels.default_provider(channel)
    if not channels.provider_enabled(channel, effective):
        # The administrator switched this specific provider off (Channels > that
        # channel > that provider's card) - it stays connected, just not allowed to send.
        provider_label = channels.CATALOG[channel]["providers"][effective]["label"]
        raise HTTPException(
            status_code=403,
            detail={"error": f"{provider_label} is turned off on the gateway.", "channel": channel, "provider": effective},
        )

    if channel == "push" and (req.app or "").strip():
        if effective == "pushover":
            names = [a["name"] for a in channels.pushover_apps()]
            if names and req.app.strip().lower() not in names:
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"Pushover app '{req.app.strip()}' isn't set up.", "available_apps": names},
                )

    if channel == "whatsapp" and effective == "gakai" and (req.app or "").strip():
        from app.services.whatsapp.gakai import configured_accounts, resolve_account

        if resolve_account(req.app) is None:
            raise HTTPException(
                status_code=400,
                detail={"error": f"Gakai account '{req.app.strip()}' isn't connected.",
                        "available_accounts": [{"id": a["id"], "label": a["label"]} for a in configured_accounts()]},
            )

    message_ids: list[str] = []

    for to in recipients:
        update_payload = {"to": to, "body": final_body}
        if channel == "email":
            update_payload["subject"] = final_subject
            update_payload["emailType"] = req.emailType

        req_one = req.model_copy(update=update_payload)
        msg = MessageEnqueued.from_request(req_one)

        message_log.record_queued(msg, account_id, used_template, source=source)

        publish_message(msg)
        message_ids.append(msg.message_id)

    to_deduped_field = recipients if input_was_list else None

    if len(message_ids) == 1:
        return MessageResponse(status="queued", message_id=message_ids[0], to_deduped=to_deduped_field, template=used_template)
    return MessageResponse(status="queued", message_ids=message_ids, to_deduped=to_deduped_field, template=used_template)


_SEND_RESPONSES = {
    200: {"description": "Queued for delivery. `message_id` identifies the message; with several recipients you get `message_ids` instead."},
    400: {"description": "Invalid request (missing recipient, unknown provider, bad address...)."},
    401: {"description": "Missing or invalid `X-User-Key` / `X-API-Token`."},
    403: {"description": "The provider that would send this (explicit or the channel's default) is turned off. The administrator switches it back on in the web app (Channels > that channel > that provider)."},
    409: {"description": "No provider is connected for this channel yet. The administrator connects one in the web app."},
}
_SEND_DESC = ("Queues the message for delivery. It is validated and logged immediately, then delivered in the "
              "background with retries. The response gives the `message_id`.")
_TEMPLATE_RULES = (
    "\n\nPlaceholders written as `{{ context.name }}` (spaces inside the braces are optional, but the `context.` "
    "prefix is required) are replaced with the matching value from `context`, here the key `name`. A placeholder with "
    "no value fails the request with a 400 (unless the gateway runs with `TEMPLATE_STRICT=false`, which fills it with "
    "an empty string). An unknown template is also a 400."
)
_HTML_CONTEXT_RULES = """

### HTML in `context` values

For an HTML email (`emailType: "html"`) the gateway makes every `context` value HTML-safe itself, so the sending
system does not need to escape anything. Send plain text, text that is already escaped, or text with basic HTML:

- **Plain text** is escaped (`&`, `<`, `>`), and line breaks (`\\n`) become `<br>`.
- **Already-escaped text** (`&amp;`, `&lt;`, `<br>`, as many automation tools produce) is kept and never double-escaped.
- **Basic formatting is kept**: `<br>`, `<hr>`, `<b>`/`<strong>`, `<i>`/`<em>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<p>`,
  `<blockquote>`, `<h1>` to `<h4>`, `<sub>`, `<sup>`, `<small>`, lists (`<ul>`, `<ol>`, `<li>`) and `<a href="...">`
  with an `http(s)` or `mailto:` link. `<bold>`, `<italic>`, `<underline>` and `<bullets>`/`<item>` are accepted and
  mapped to the real tag.
- **Anything else is shown as text, not run**: `<script>`, `<style>`, `<div>`, `<mark>`, event attributes such as
  `onclick`, and `javascript:` links.
- **Trusted HTML**: a `context` key that ends in `_html` (for example `table_html`) is inserted exactly as sent.

Subjects, text emails (`emailType: "txt"`) and SMS are never escaped.
"""
_EMAIL_TEMPLATE_DESC = _SEND_DESC + _TEMPLATE_RULES + _HTML_CONTEXT_RULES + """

`template` is the **ID** of a template saved in the web app's **Templates** page (e.g. `tpl_1a2b3c4d5e6f`), or a
template **name**. Saved templates are stored in the gateway's database; a built-in file in `app/templates/email/`
(like `welcome`) is sent by name. `emailType` decides which body is used (`html` or `txt`), so give the template both
if you send both. `{{ context.key }}` also works in `subject`.

Use `GET /v1/templates/email` to list the IDs and the `context` keys each template needs.

### Template example

A template with the body `Hello {{ context.name }}, welcome!`, saved with ID `tpl_1a2b3c4d5e6f`.

The request:

```json
{
  "to": "ana@example.com",
  "subject": "Welcome, {{ context.name }}",
  "template": "tpl_1a2b3c4d5e6f",
  "emailType": "html",
  "context": { "name": "Ana" }
}
```

What Ana receives: subject `Welcome, Ana`, and the body `Hello Ana, welcome!`.
"""
_SMS_TEMPLATE_DESC = _SEND_DESC + _TEMPLATE_RULES + """

SMS templates live in `app/templates/sms/` as `<name>.txt`.

### Template example

The template file `app/templates/sms/welcome.txt`:

```
Hi {{ context.name }}, welcome! Your account is now active.
```

The request:

```json
{
  "to": "+15551234567",
  "template": "welcome",
  "context": { "name": "Ana" }
}
```

What is delivered: `Hi Ana, welcome! Your account is now active.`
"""
_TELEGRAM_DESC = """

### Set up Telegram first

Telegram needs a bot connected in the web app under **Channels > Telegram**. In Telegram, open
@BotFather, send `/newbot`, and follow the prompts - it replies with a **bot token**. Paste that
token in the web app. Telegram bots can't message someone first: whoever should receive messages
has to open the bot and send it anything once. After that, use **Find chat IDs** in the web app to
look up their chat ID, and either set it as the **default chat ID** or pass it as `to`.
"""
_WHATSAPP_DESC = """

### Set up WhatsApp first

WhatsApp needs a provider connected in the web app under **Channels > WhatsApp**. Two are supported:

**Gakai** (a Gakai WhatsApp server)
- Enter the Gakai address and an application token (with *Read accounts* and *Send messages*), then tick one or more of
  its WhatsApp accounts and pick a **default**. A Gakai token sends from one account only, so every ticked account other
  than the token's own needs its own token; the gateway checks each one when you connect.
- Pick the sending account with **`account`**: the Gakai **account id** (e.g. `account-4f1c2a9b`), shown with a **Copy**
  button on the account's row in the web app. Names and phone numbers are not accepted, because they can repeat or change.
  Leave `account` out to send from the default account.
- An unknown id returns a `400` listing the connected accounts: `{"error": "Gakai account 'x' isn't connected.", "available_accounts": [{"id": "account-4f1c2a9b", "label": "Business"}]}`.
- Gakai has no 24-hour window and sends to any number that is on WhatsApp. A number that isn't fails with `That number is not on WhatsApp.`
- With more than one WhatsApp provider connected, add `"provider": "gakai"` to use it. `account` is ignored by the Meta provider.

**WhatsApp Business Platform** (Meta's Cloud API)
- A Phone Number ID and an access token from a Meta developer app. See that page for the full setup (a permanent token
  needs a System User, not the 24-hour token the API Setup page gives you by default).
- **The 24-hour window.** WhatsApp only delivers a free-form message like this API sends if the recipient has messaged
  your WhatsApp number in the last 24 hours - anyone else needs a pre-approved message template, which this endpoint
  doesn't send. A message outside the window fails with a clear `error` explaining that, not a delivered status.
"""
_PUSH_DESC = """

### Set up push first

Push needs a provider connected in the web app under **Channels > Push**. Two are supported:

**Pushover** (needs a [pushover.net](https://pushover.net) account)
- Your **user key** (from your Pushover dashboard) says *who* gets the notification.
- An **app** says *which Pushover application* it is sent from. Create one at
  [pushover.net/apps/build](https://pushover.net/apps/build). It gives you an **API token**.
- In the web app, add each app with a **name** (your own label, e.g. `alerts` or `website`) and its API token.
  One app is the **default**. Names are lowercase: 1 to 40 letters, numbers, dots, dashes or underscores.
- In a request, `app` is that **name**, not the token. Leave it out to use the default app.
  An unknown name returns a 400 with the list of valid names (`available_apps`).
- `device` limits delivery to one of your devices by its Pushover device name. Leave it out to notify all of them.

**ntfy** (free, no account)
- You choose a **topic** name in the web app, and your phone subscribes to that topic in the ntfy app.
- `app` and `device` are ignored.

### Fields by provider

| Field | Pushover | ntfy |
|---|---|---|
| `body` | message text | message text |
| `subject` | notification title | notification title |
| `url`, `url_title` | link with optional label | link opens on tap (`url_title` ignored) |
| `app` | app **name** (default app if omitted) | ignored |
| `device` | device name (all devices if omitted) | ignored |
| `to` | override the recipient user or group key | override the topic |
| `provider` | `pushover` | `ntfy` |

Leave `to` out to use the recipient saved in the web app.
"""


def _post(path, tag, summary, description):
    return app.post(path, response_model=MessageResponse, response_model_exclude_none=True,
                    tags=[tag], summary=summary, description=description, responses=_SEND_RESPONSES)


@_post("/v1/messages/email", "Email", "Send an email", _SEND_DESC)
def send_email(req: EmailMessage = Body(examples=[{"to": "someone@example.com", "subject": "Hello", "body": "Hi there!"}]),
               auth: dict = Depends(require_api_key)):
    return enqueue_message(req.to_request(), auth.get("account_id"))


@_post("/v1/messages/email/template", "Email", "Send an email from a template", _EMAIL_TEMPLATE_DESC)
def send_email_template(req: EmailTemplateMessage = Body(examples=[{"to": "ana@example.com", "subject": "Welcome, {{ context.name }}", "template": "welcome", "context": {"name": "Ana"}}]),
                        auth: dict = Depends(require_api_key)):
    return enqueue_message(req.to_request(), auth.get("account_id"))


@_post("/v1/messages/sms", "SMS", "Send an SMS", _SEND_DESC)
def send_sms(req: SmsMessage = Body(examples=[{"to": "+15551234567", "body": "Running late, back soon."}]),
             auth: dict = Depends(require_api_key)):
    return enqueue_message(req.to_request(), auth.get("account_id"))


@_post("/v1/messages/sms/template", "SMS", "Send an SMS from a template", _SMS_TEMPLATE_DESC)
def send_sms_template(req: SmsTemplateMessage = Body(examples=[{"to": "+15551234567", "template": "welcome", "context": {"name": "Ana"}}]),
                      auth: dict = Depends(require_api_key)):
    return enqueue_message(req.to_request(), auth.get("account_id"))


@_post("/v1/messages/push", "Push", "Send a push notification", _SEND_DESC + _PUSH_DESC)
def send_push(req: PushMessage = Body(examples=[{"subject": "Deploy finished", "body": "Version 1.4 is live.", "app": "alerts"}]),
              auth: dict = Depends(require_api_key)):
    return enqueue_message(req.to_request(), auth.get("account_id"))


@_post("/v1/messages/telegram", "Telegram", "Send a Telegram message", _SEND_DESC + _TELEGRAM_DESC)
def send_telegram(req: TelegramMessage = Body(examples=[{"to": "123456789", "body": "Running late, back soon."}]),
                   auth: dict = Depends(require_api_key)):
    return enqueue_message(req.to_request(), auth.get("account_id"))


@_post("/v1/messages/whatsapp", "WhatsApp", "Send a WhatsApp message", _SEND_DESC + _WHATSAPP_DESC)
def send_whatsapp(req: WhatsAppMessage = Body(examples=[
                       {"to": "+15551234567", "body": "Running late, back soon."},
                       {"to": "+15551234567", "body": "Running late, back soon.", "provider": "gakai", "account": "account-4f1c2a9b"}]),
                   auth: dict = Depends(require_api_key)):
    return enqueue_message(req.to_request(), auth.get("account_id"))


# Generic endpoint kept for existing callers; the per-channel endpoints above are the documented API.
@app.post("/v1/messages", include_in_schema=False, response_model=MessageResponse, response_model_exclude_none=True)
def create_message(req: MessageRequest, auth: dict = Depends(require_api_key)):
    return enqueue_message(req, auth.get("account_id"))


@app.get("/v1/messages", include_in_schema=False, tags=["Message log"], summary="List your sent messages", responses={401: {"description": "Missing or invalid credentials."}})
def list_messages(
    channel: str | None = None,
    status: str | None = None,
    limit: int = 50,
    auth: dict = Depends(require_api_key),
):
    """Your message log, newest first: timestamp, channel, recipient, status,
    provider, attempts and (unless disabled) the decrypted subject and body."""
    limit = max(1, min(int(limit), 200))
    docs = get_repository().list_messages(channel, status, limit, account_id=auth["account_id"])
    return {"messages": [message_log.public_view(d) for d in docs]}


@app.get("/v1/messages/{message_id}", include_in_schema=False, tags=["Message log"], summary="Get one message and its delivery attempts")
def get_message(message_id: str, auth: dict = Depends(require_api_key)):
    repo = get_repository()
    doc = repo.get_message(message_id)
    if not doc or doc.get("account_id") != auth["account_id"]:
        raise HTTPException(status_code=404, detail="Message not found")
    attempts = [
        {k: v for k, v in a.items() if k not in ("_id", "created_at")}
        for a in repo.list_attempts(message_id)
    ]
    return {**message_log.public_view(doc), "delivery_attempts": attempts}


@app.get("/v1/providers", include_in_schema=False, tags=["Providers"], summary="Which providers you can send with")
def list_providers(auth: dict = Depends(require_api_key)):
    """For each channel, the connected providers and the default. Pass any of
    these as "provider" in POST /v1/messages to choose one per message."""
    out = {
        ch: {"default": channels.default_provider(ch), "available": channels.connected_providers(ch)}
        for ch in channels.CATALOG
    }
    out["push"]["pushover_apps"] = [a["name"] for a in channels.pushover_apps() if a["set"]]
    return out
