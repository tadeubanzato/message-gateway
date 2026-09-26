"""
Local-modem SMS provider (calls a self-hosted Flask API in front of
ModemManager/mmcli). Ported from the old codebase's services/sms/sms_delivery.py.

Generalized: the old code's docstring example LAN IP is gone from here — the
MCP wizard's get_setup_instructions() is where user-facing guidance about
what SMS_API_BASE_URL should look like belongs, not a hardcoded example baked
into a comment.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from app.services.sms.base import SmsProvider, SmsResult
from app.services import env as _env_mod

_env = _env_mod.get_env


class LocalModemProvider(SmsProvider):
    name = "local_modem"

    def required_env_vars(self) -> list[str]:
        return ["SMS_API_BASE_URL", "SMS_API_TOKEN"]

    def send(self, *, to: str, body: str, message_id: str) -> SmsResult:
        base = _env("SMS_API_BASE_URL")
        token = _env("SMS_API_TOKEN")
        timeout = float(_env("SMS_HTTP_TIMEOUT_SECONDS", "15") or 15.0)

        if not base:
            return SmsResult(ok=False, provider=self.name, error="SMS_API_BASE_URL is missing")
        if not token:
            return SmsResult(ok=False, provider=self.name, error="SMS_API_TOKEN is missing")

        url = base.rstrip("/") + "/api/send_sms"
        payload = {"number": to, "message": body, "message_id": message_id}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                      "Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                text = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(text) if text else None
            if status == 200 and isinstance(parsed, dict) and parsed.get("status") == "ok":
                return SmsResult(ok=True, provider=self.name, status_code=status,
                                  provider_message_id=str(parsed.get("sms_id")) if parsed.get("sms_id") else None,
                                  provider_status=str(parsed.get("mmcli_status")) if parsed.get("mmcli_status") else None,
                                  raw=parsed)
            err = parsed.get("error") if isinstance(parsed, dict) else None
            return SmsResult(ok=False, provider=self.name, status_code=status,
                              error=str(err) if err else f"non-success response (status={status})",
                              transient=isinstance(status, int) and 500 <= status < 600)
        except Exception as e:
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> SmsResult:
        base = _env("SMS_API_BASE_URL")
        token = _env("SMS_API_TOKEN")
        if not base or not token:
            return SmsResult(ok=False, provider=self.name, error="Missing SMS_API_BASE_URL/SMS_API_TOKEN")
        req = urllib.request.Request(
            base.rstrip("/") + "/api/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return SmsResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except Exception as e:
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
