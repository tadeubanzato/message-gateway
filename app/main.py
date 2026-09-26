import contextlib
import os
import re
import threading
import time
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
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
from app.services import channels, message_log
from app.version import APP_NAME, APP_VERSION
from app.schemas import MessageEnqueued, MessageRequest, MessageResponse

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
    threading.Thread(target=_purge_loop, name="log-purge", daemon=True).start()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=_lifespan)

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
_CONTEXT_TOKEN_RE = re.compile(r"\{\{\s*context\.([A-Za-z0-9_]+)\s*\}\}")
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
    base_dir = _template_dir_for_channel(req.channel)
    ext = _template_ext_for_request(req)
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


def _render_context(text: str, ctx: dict) -> str:
    context = ctx or {}

    def repl(match: re.Match) -> str:
        key = match.group(1)
        val = context.get(key)
        if val is None:
            if TEMPLATE_STRICT:
                raise HTTPException(status_code=400, detail=f"Missing context key: {key}")
            return ""
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


app.include_router(onboarding_router)
app.include_router(portal_router)
app.include_router(portal_ui_router)


@app.get("/health")
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


@app.get("/scalar", include_in_schema=False)
def scalar_docs():
    return get_scalar_api_reference(openapi_url=app.openapi_url, title=app.title, dark_mode=True)


@app.get("/get-started", response_class=HTMLResponse, include_in_schema=False)
def get_started():
    mcp_url = os.environ.get("PUBLIC_MCP_URL", "http://localhost:8010/mcp").strip()
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
      <button class="btn ghost sm" onclick="navigator.clipboard.writeText(document.getElementById('prompt-text').innerText)">Copy prompt</button>
    </div>
    <p class="muted"><a href="/scalar">API reference</a> &middot; <a href="/docs">Swagger</a></p>
  </main>
</body>
</html>"""


@app.get("/v1/auth/whoami")
def whoami(auth: dict = Depends(require_api_key)):
    return {"ok": True, "account_id": auth.get("account_id"), "user_key": auth.get("user_key")}


@app.get("/v1/templates/sms/{template_name}")
def get_sms_template_expected_context(template_name: str, auth: dict = Depends(require_api_key)):
    req = MessageRequest(channel="sms", to="+10000000000", body="x")
    text = _load_template_text(template_name, req=req)
    keys = _extract_required_context_keys(text)
    return {
        "template": template_name, "channel": "sms", "required_context_keys": keys,
        "example_context": {k: "<required>" for k in keys}, "template_strict": TEMPLATE_STRICT,
    }


@app.get("/v1/templates/email/{template_name}")
def get_email_template_expected_context(template_name: str, auth: dict = Depends(require_api_key), emailType: str = "txt"):
    et = "html" if str(emailType).strip().lower() == "html" else "txt"
    req = MessageRequest(channel="email", to="x@y.z", subject="x", body="x", emailType=et)  # type: ignore[arg-type]
    text = _load_template_text(template_name, req=req)
    keys = _extract_required_context_keys(text)
    return {
        "template": template_name, "channel": "email", "emailType": et, "required_context_keys": keys,
        "example_context": {k: "<required>" for k in keys}, "template_strict": TEMPLATE_STRICT,
    }


def enqueue_message(req: MessageRequest, account_id: Optional[str]) -> MessageResponse:
    """Validate a message request, log it and queue it for delivery. Shared by the
    HTTP API and the MCP server. Raises HTTPException on invalid input."""
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
        else:
            raise HTTPException(status_code=400, detail="Missing 'to' recipient(s)")

    if channel == "email":
        if not (req.subject or "").strip():
            raise HTTPException(status_code=400, detail="Missing 'subject' for email message")
        _validate_email_recipients_or_400(recipients)

    used_template = (req.template or "").strip() or None
    base_text = _load_template_text(used_template, req=req) if used_template else (req.body or "")
    final_body = _render_context(base_text, req.context)

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

    if channel == "push" and (req.app or "").strip():
        effective = requested or channels.default_provider(channel)
        if effective == "pushover":
            names = [a["name"] for a in channels.pushover_apps()]
            if names and req.app.strip().lower() not in names:
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"Pushover app '{req.app.strip()}' isn't set up.", "available_apps": names},
                )

    message_ids: list[str] = []

    for to in recipients:
        update_payload = {"to": to, "body": final_body}
        if channel == "email":
            update_payload["subject"] = final_subject
            update_payload["emailType"] = req.emailType

        req_one = req.model_copy(update=update_payload)
        msg = MessageEnqueued.from_request(req_one)

        message_log.record_queued(msg, account_id, used_template)

        publish_message(msg)
        message_ids.append(msg.message_id)

    to_deduped_field = recipients if input_was_list else None

    if len(message_ids) == 1:
        return MessageResponse(status="queued", message_id=message_ids[0], to_deduped=to_deduped_field, template=used_template)
    return MessageResponse(status="queued", message_ids=message_ids, to_deduped=to_deduped_field, template=used_template)


@app.post("/v1/messages", response_model=MessageResponse, response_model_exclude_none=True)
def create_message(req: MessageRequest, auth: dict = Depends(require_api_key)):
    return enqueue_message(req, auth.get("account_id"))


@app.get("/v1/messages", tags=["Message log"], summary="List your sent messages")
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


@app.get("/v1/messages/{message_id}", tags=["Message log"], summary="Get one message and its delivery attempts")
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


@app.get("/v1/providers", tags=["Providers"], summary="Which providers you can send with")
def list_providers(auth: dict = Depends(require_api_key)):
    """For each channel, the connected providers and the default. Pass any of
    these as "provider" in POST /v1/messages to choose one per message."""
    out = {
        ch: {"default": channels.default_provider(ch), "available": channels.connected_providers(ch)}
        for ch in channels.CATALOG
    }
    out["push"]["pushover_apps"] = [a["name"] for a in channels.pushover_apps() if a["set"]]
    return out
