"""Twilio SMS provider (Programmable Messaging REST API)."""

from __future__ import annotations

import base64
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from app.services.sms.base import SmsProvider, SmsResult
from app.services import env as _env_mod

_env = _env_mod.get_env


class TwilioProvider(SmsProvider):
    name = "twilio"

    def required_env_vars(self) -> list[str]:
        return ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"]

    def send(self, *, to: str, body: str, message_id: str) -> SmsResult:
        sid = _env("TWILIO_ACCOUNT_SID")
        token = _env("TWILIO_AUTH_TOKEN")
        from_number = _env("TWILIO_FROM_NUMBER")
        timeout = float(_env("TWILIO_TIMEOUT_SECS", "15") or 15.0)

        missing = [k for k, v in [
            ("TWILIO_ACCOUNT_SID", sid), ("TWILIO_AUTH_TOKEN", token), ("TWILIO_FROM_NUMBER", from_number),
        ] if not v]
        if missing:
            return SmsResult(ok=False, provider=self.name, error=f"Missing env var(s): {', '.join(missing)}")

        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        payload = urllib.parse.urlencode({"To": to, "From": from_number, "Body": body}).encode("utf-8")
        auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")

        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                import json as _json
                parsed = _json.loads(resp.read().decode("utf-8", errors="replace"))
                return SmsResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status),
                                  provider_message_id=parsed.get("sid"), provider_status=parsed.get("status"),
                                  raw=parsed)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            return SmsResult(ok=False, provider=self.name, status_code=code,
                              error=f"Twilio HTTPError: {code}", transient=500 <= code < 600)
        except Exception as e:
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> SmsResult:
        sid = _env("TWILIO_ACCOUNT_SID")
        token = _env("TWILIO_AUTH_TOKEN")
        if not sid or not token:
            return SmsResult(ok=False, provider=self.name, error="Missing TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN")
        auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            headers={"Authorization": f"Basic {auth}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return SmsResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except urllib.error.HTTPError as e:
            return SmsResult(ok=False, provider=self.name, status_code=e.code, error="Invalid Twilio credentials")
        except Exception as e:
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
