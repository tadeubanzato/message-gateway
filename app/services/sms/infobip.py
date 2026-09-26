"""Infobip SMS provider (thinner implementation — send + check_config, stub-quality
compared to Twilio/local_modem which are the fully-verified defaults)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from app.services.sms.base import SmsProvider, SmsResult
from app.services import env as _env_mod

_env = _env_mod.get_env


class InfobipProvider(SmsProvider):
    name = "infobip"

    def required_env_vars(self) -> list[str]:
        return ["INFOBIP_API_KEY", "INFOBIP_BASE_URL", "INFOBIP_FROM"]

    def send(self, *, to: str, body: str, message_id: str) -> SmsResult:
        api_key = _env("INFOBIP_API_KEY")
        base_url = _env("INFOBIP_BASE_URL")
        sender = _env("INFOBIP_FROM")
        timeout = float(_env("INFOBIP_TIMEOUT_SECS", "15") or 15.0)

        missing = [k for k, v in [
            ("INFOBIP_API_KEY", api_key), ("INFOBIP_BASE_URL", base_url), ("INFOBIP_FROM", sender),
        ] if not v]
        if missing:
            return SmsResult(ok=False, provider=self.name, error=f"Missing env var(s): {', '.join(missing)}")

        url = f"{base_url.rstrip('/')}/sms/2/text/advanced"
        payload = {"messages": [{"destinations": [{"to": to}], "from": sender, "text": body}]}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"App {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
                mid = None
                try:
                    mid = parsed["messages"][0]["messageId"]
                except Exception:
                    pass
                return SmsResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status),
                                  provider_message_id=mid, raw=parsed)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            return SmsResult(ok=False, provider=self.name, status_code=code,
                              error=f"Infobip HTTPError: {code}", transient=500 <= code < 600)
        except Exception as e:
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> SmsResult:
        api_key = _env("INFOBIP_API_KEY")
        base_url = _env("INFOBIP_BASE_URL")
        if not api_key or not base_url:
            return SmsResult(ok=False, provider=self.name, error="Missing INFOBIP_API_KEY/INFOBIP_BASE_URL")
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/account/1/balance",
            headers={"Authorization": f"App {api_key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return SmsResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except urllib.error.HTTPError as e:
            return SmsResult(ok=False, provider=self.name, status_code=e.code, error="Invalid Infobip credentials")
        except Exception as e:
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
