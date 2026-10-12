"""
SMSGate provider: send text messages through your own Android phone, using the
SMS Gateway for Android app (https://sms-gate.app).

Talks to an SMSGate server's third-party API. By default that is the server
bundled with this gateway (the `smsgate` compose profile, reached over the
internal Docker network); set SMSGATE_URL to use another one, for example the
public cloud at https://api.sms-gate.app/3rdparty/v1.

The server only queues the message for the phone, so "ok" here means the server
accepted it, not that the handset has sent it yet.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Optional

from app.services import env as _env_mod
from app.services import smsgate_server
from app.services.sms.base import SmsProvider, SmsResult

_env = _env_mod.get_env


def base_url() -> str:
    return ((_env("SMSGATE_URL") or "").strip() or smsgate_server.internal_api_url()).rstrip("/")


def _error_text(raw: bytes) -> str:
    """The server's own explanation, when its error body has one."""
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return ""
    return str(parsed.get("message") or "").strip() if isinstance(parsed, dict) else ""


class SmsGateProvider(SmsProvider):
    name = "smsgate"

    def required_env_vars(self) -> list[str]:
        return ["SMSGATE_USERNAME", "SMSGATE_PASSWORD"]

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> tuple[int, bytes]:
        user, password = (_env("SMSGATE_USERNAME") or "").strip(), _env("SMSGATE_PASSWORD") or ""
        auth = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base_url() + path, data=data, method=method, headers=headers)
        timeout = float(_env("SMSGATE_TIMEOUT_SECS", "15") or 15.0)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", None) or resp.getcode()), resp.read()

    def send(self, *, to: str, body: str, message_id: str) -> SmsResult:
        if not (_env("SMSGATE_USERNAME") and _env("SMSGATE_PASSWORD")):
            return SmsResult(ok=False, provider=self.name, error="Missing env var(s): SMSGATE_USERNAME, SMSGATE_PASSWORD")

        # The gateway's own id is reused as the server's id, so a retry of a message
        # the server already accepted is recognized instead of being texted twice.
        payload: dict[str, Any] = {"id": message_id, "textMessage": {"text": body}, "phoneNumbers": [to]}
        device = (_env("SMSGATE_DEVICE_ID") or "").strip()
        if device:
            payload["deviceId"] = device
        sim = (_env("SMSGATE_SIM_NUMBER") or "").strip()
        if sim.isdigit():
            payload["simNumber"] = int(sim)

        try:
            status, raw = self._request("POST", "/messages", payload)
        except urllib.error.HTTPError as e:
            if e.code == 409:  # this id is already queued: an earlier attempt got through
                return SmsResult(ok=True, provider=self.name, status_code=409, provider_message_id=message_id)
            detail = _error_text(e.read())
            if e.code in (401, 403):
                return SmsResult(ok=False, provider=self.name, status_code=e.code,
                                 error="SMSGate rejected the username or password. Copy them again from the app's Home tab.")
            return SmsResult(ok=False, provider=self.name, status_code=e.code,
                             error=f"SMSGate returned HTTP {e.code}" + (f": {detail}" if detail else ""),
                             transient=e.code >= 500 or e.code == 429)
        except Exception as e:  # noqa: BLE001 - network errors are worth retrying
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            parsed = {}
        parsed = parsed if isinstance(parsed, dict) else {}
        return SmsResult(ok=200 <= status < 300, provider=self.name, status_code=status,
                         provider_message_id=str(parsed.get("id") or message_id),
                         provider_status=parsed.get("state"), raw=parsed)

    def check_config(self) -> SmsResult:
        """Confirm the credentials work and that a phone is paired with them. Nothing is sent."""
        if not (_env("SMSGATE_USERNAME") and _env("SMSGATE_PASSWORD")):
            return SmsResult(ok=False, provider=self.name, error="Missing SMSGATE_USERNAME/SMSGATE_PASSWORD")
        try:
            status, raw = self._request("GET", "/devices")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return SmsResult(ok=False, provider=self.name, status_code=e.code,
                                 error="SMSGate rejected the username or password. Copy them again from the app's Home tab.")
            return SmsResult(ok=False, provider=self.name, status_code=e.code, error=f"SMSGate returned HTTP {e.code}.")
        except Exception as e:  # noqa: BLE001
            return SmsResult(ok=False, provider=self.name,
                             error=f"Could not reach the SMSGate server at {base_url()} ({type(e).__name__}). Is it running?")
        try:
            devices = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            devices = None
        if not isinstance(devices, list) or not devices:
            return SmsResult(ok=False, provider=self.name, status_code=status,
                             error="No phone is paired with these credentials yet.")
        wanted = (_env("SMSGATE_DEVICE_ID") or "").strip()
        if wanted and not any(isinstance(d, dict) and d.get("id") == wanted for d in devices):
            return SmsResult(ok=False, provider=self.name, status_code=status,
                             error=f"Device {wanted} isn't paired with these credentials.")
        return SmsResult(ok=True, provider=self.name, status_code=status)
