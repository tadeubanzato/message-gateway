import contextlib
import os
import re
import time

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from scalar_fastapi import get_scalar_api_reference

from app.auth import require_api_key
from app.broker import publish_message
from app.db import backend_name, get_repository
from app.mcp_server.server import mcp
from app.routes.portal import router as portal_router
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
@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    get_repository()  # initializes schema/indexes for whichever backend is active
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Relay Gateway", version="0.1.0", lifespan=_lifespan)

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


app.include_router(portal_router)


@app.get("/health")
def health():
    repo_ok = False
    try:
        repo_ok = get_repository().ping()
    except Exception:
        repo_ok = False
    return {"ok": True, "db_backend": backend_name(), "db_ok": repo_ok}


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
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8" />
      <title>Get Started — Relay Gateway</title>
      <style>
        body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 60px auto; padding: 0 20px; color: #111; }}
        h1 {{ font-size: 22px; }}
        .box {{ background: #f5f5f7; border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin: 16px 0; }}
        code {{ font-family: ui-monospace, monospace; }}
        .prompt {{ font-size: 14px; line-height: 1.5; }}
        button {{ padding: 8px 14px; border-radius: 8px; border: 1px solid #111; background: #111; color: #fff; cursor: pointer; }}
      </style>
    </head>
    <body>
      <h1>Relay Gateway — Get Started</h1>
      <p>MCP server URL: <code>{mcp_url}</code></p>
      <p>Paste this prompt into Claude Code, Codex, Claude Desktop, or any MCP-capable agent:</p>
      <div class="box">
        <div class="prompt" id="prompt-text">{prompt}</div>
      </div>
      <button onclick="navigator.clipboard.writeText(document.getElementById('prompt-text').innerText)">Copy prompt</button>
      <p><a href="/scalar">API reference</a> · <a href="/docs">Swagger</a></p>
    </body>
    </html>
    """


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


@app.post("/v1/messages", response_model=MessageResponse, response_model_exclude_none=True)
def create_message(req: MessageRequest, auth: dict = Depends(require_api_key)):
    input_was_list = isinstance(req.to, list)
    channel = req.channel
    recipients = req.to_list_deduped()

    if not recipients:
        if channel == "push":
            recipients = [""]
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

    repo = get_repository()
    message_ids: list[str] = []

    for to in recipients:
        update_payload = {"to": to, "body": final_body}
        if channel == "email":
            update_payload["subject"] = final_subject
            update_payload["emailType"] = req.emailType

        req_one = req.model_copy(update=update_payload)
        msg = MessageEnqueued.from_request(req_one)

        try:
            repo.insert_message({
                "message_id": msg.message_id,
                "channel": msg.channel,
                "status": "queued",
                "account_id": auth.get("account_id"),
                "to": to,
            })
        except Exception:
            pass  # message logging must never block delivery

        publish_message(msg)
        message_ids.append(msg.message_id)

    to_deduped_field = recipients if input_was_list else None

    if len(message_ids) == 1:
        return MessageResponse(status="queued", message_id=message_ids[0], to_deduped=to_deduped_field, template=used_template)
    return MessageResponse(status="queued", message_ids=message_ids, to_deduped=to_deduped_field, template=used_template)
