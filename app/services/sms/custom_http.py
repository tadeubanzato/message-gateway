"""
Custom HTTP SMS provider: send through YOUR OWN SMS endpoint (an SMS box, a modem
API on your network, or any HTTP service) using a request pattern you define.

You configure, in the web app (Channels > SMS):
  - the endpoint URL and HTTP method
  - optional headers (for example an Authorization token), one per line
  - a sample JSON body containing placeholders:
        {{to}}          the phone number, e.g. +15551234567
        {{to_digits}}   the number with digits only, e.g. 15551234567
        {{message}}     the message text
        {{message_id}}  the gateway's id for this message
  - optionally, text the response must contain to count as success

Example body:  {"number": "{{to}}", "text": "{{message}}"}

Placeholders are replaced inside the parsed JSON (never by pasting text into the
JSON source), so quotes or newlines in a message can't break the request.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from app.services import env as _env_mod
from app.services.sms.base import SmsProvider, SmsResult

_env = _env_mod.get_env

METHODS = ("POST", "PUT", "PATCH")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(to|to_digits|message|message_id)\s*\}\}")
_ID_KEYS = ("id", "message_id", "messageId", "sms_id", "sid", "uuid")


def parse_headers(text: Optional[str]) -> dict[str, str]:
    """Headers from one 'Name: value' per line, or from a JSON object."""
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("Headers JSON must be an object.")
        return {str(k).strip(): str(v).strip() for k, v in obj.items() if str(k).strip()}
    headers: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Header line {line!r} must look like 'Name: value'.")
        name, value = line.split(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", name.strip()):
            raise ValueError(f"Invalid header name {name.strip()!r}.")
        headers[name.strip()] = value.strip()
    return headers


def _substitute(value: Any, fields: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda m: fields[m.group(1)], value)
    if isinstance(value, list):
        return [_substitute(v, fields) for v in value]
    if isinstance(value, dict):
        return {_substitute(k, fields): _substitute(v, fields) for k, v in value.items()}
    return value


def render_body(template_text: str, to: str, message: str, message_id: str) -> Any:
    """The JSON body to send: the template with placeholders filled in."""
    fields = {"to": to, "to_digits": re.sub(r"\D", "", to), "message": message, "message_id": message_id}
    return _substitute(json.loads(template_text), fields)


def validate_config(url: str, method: str, headers_text: Optional[str], body_text: str) -> Optional[str]:
    """Return a human-readable problem with the configuration, or None if it is usable."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "The endpoint URL must start with http:// or https://."
    if (method or "POST").upper() not in METHODS:
        return "The HTTP method must be POST, PUT or PATCH."
    try:
        parse_headers(headers_text)
    except (ValueError, json.JSONDecodeError) as e:
        return f"Headers: {e}"
    try:
        json.loads(body_text)
    except json.JSONDecodeError as e:
        return f"The request body is not valid JSON ({e.msg} at line {e.lineno}). Put placeholders inside quotes, like \"{{{{to}}}}\"."
    used = set(_PLACEHOLDER_RE.findall(body_text))
    if not (used & {"to", "to_digits"}) or "message" not in used:
        return "The request body must include the phone number ({{to}}) and the message text ({{message}})."
    return None


def _squash(text: str) -> str:
    """Remove whitespace around JSON punctuation so '"status": "ok"' matches '"status":"ok"'."""
    return re.sub(r"\s*([:,{}\[\]])\s*", r"\1", text or "")


def _extract_id(text: str) -> Optional[str]:
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, dict):
        for key in _ID_KEYS:
            if parsed.get(key) not in (None, ""):
                return str(parsed[key])
    return None


class CustomHttpProvider(SmsProvider):
    name = "custom_http"

    def required_env_vars(self) -> list[str]:
        return ["CUSTOM_SMS_URL", "CUSTOM_SMS_BODY"]

    def _config(self) -> tuple[str, str, Optional[str], str, Optional[str], float]:
        return (
            (_env("CUSTOM_SMS_URL") or "").strip(),
            (_env("CUSTOM_SMS_METHOD", "POST") or "POST").strip().upper(),
            _env("CUSTOM_SMS_HEADERS"),
            _env("CUSTOM_SMS_BODY") or "",
            _env("CUSTOM_SMS_SUCCESS_TEXT"),
            float(_env("CUSTOM_SMS_TIMEOUT_SECS", "15") or 15.0),
        )

    def send(self, *, to: str, body: str, message_id: str) -> SmsResult:
        url, method, headers_text, body_text, success_text, timeout = self._config()
        problem = validate_config(url, method, headers_text, body_text)
        if problem:
            return SmsResult(ok=False, provider=self.name, error=f"Custom SMS endpoint isn't configured correctly: {problem}")
        try:
            payload = json.dumps(render_body(body_text, to, body, message_id)).encode("utf-8")
            headers = {"Content-Type": "application/json", "Accept": "application/json", **parse_headers(headers_text)}
            req = urllib.request.Request(url, data=payload, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", None) or resp.getcode())
                text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            snippet = e.read().decode("utf-8", errors="replace")[:120].strip()
            return SmsResult(
                ok=False, provider=self.name, status_code=e.code,
                error=f"Endpoint returned HTTP {e.code}" + (f": {snippet}" if snippet else ""),
                transient=e.code >= 500 or e.code == 429,
            )
        except Exception as e:  # noqa: BLE001 - network errors are worth retrying
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

        if success_text and success_text not in text and _squash(success_text) not in _squash(text):
            return SmsResult(ok=False, provider=self.name, status_code=status,
                             error=f"Endpoint replied HTTP {status} but the response didn't contain {success_text!r}.")
        return SmsResult(ok=True, provider=self.name, status_code=status, provider_message_id=_extract_id(text))

    def check_config(self) -> SmsResult:
        """Validate the pattern and confirm the endpoint's host accepts connections.
        It does not call the endpoint itself, so nothing is sent."""
        url, method, headers_text, body_text, _success, _timeout = self._config()
        problem = validate_config(url, method, headers_text, body_text)
        if problem:
            return SmsResult(ok=False, provider=self.name, error=problem)
        parsed = urllib.parse.urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((parsed.hostname, port), timeout=8):
                pass
        except OSError as e:
            return SmsResult(ok=False, provider=self.name, error=f"Could not reach {parsed.hostname}:{port} ({type(e).__name__}). Is the address right and the service running?")
        return SmsResult(ok=True, provider=self.name)
