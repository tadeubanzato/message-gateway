"""Sinch SMS provider (SMS API v1, the "batches" / XMS endpoint).

https://developers.sinch.com/docs/sms/api-reference/
  POST https://{region}.sms.api.sinch.com/xms/v1/{service_plan_id}/batches
  Authorization: Bearer <api token>
  Body: {"from": "+1...", "to": ["+1..."], "body": "..."}
A successful create returns 201 with {"id": "...", "status": "Queued", ...}.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from app.services.sms.base import SmsProvider, SmsResult
from app.services import env as _env_mod

_env = _env_mod.get_env

REGIONS = ("us", "eu", "au", "br", "ca")


class SinchProvider(SmsProvider):
    name = "sinch"

    def required_env_vars(self) -> list[str]:
        return ["SINCH_SERVICE_PLAN_ID", "SINCH_API_TOKEN", "SINCH_FROM_NUMBER"]

    def _base_url(self) -> str:
        region = (_env("SINCH_REGION", "us") or "us").strip().lower()
        if region not in REGIONS:
            region = "us"
        return f"https://{region}.sms.api.sinch.com"

    def send(self, *, to: str, body: str, message_id: str) -> SmsResult:
        plan_id = _env("SINCH_SERVICE_PLAN_ID")
        token = _env("SINCH_API_TOKEN")
        from_number = _env("SINCH_FROM_NUMBER")
        timeout = float(_env("SINCH_TIMEOUT_SECS", "15") or 15.0)

        missing = [k for k, v in [
            ("SINCH_SERVICE_PLAN_ID", plan_id), ("SINCH_API_TOKEN", token), ("SINCH_FROM_NUMBER", from_number),
        ] if not v]
        if missing:
            return SmsResult(ok=False, provider=self.name, error=f"Missing env var(s): {', '.join(missing)}")

        url = f"{self._base_url()}/xms/v1/{plan_id}/batches"
        payload = {"from": from_number, "to": [to], "body": body}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
                return SmsResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status),
                                  provider_message_id=parsed.get("id"), provider_status=parsed.get("status"),
                                  raw=parsed)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            snippet = e.read().decode("utf-8", errors="replace")[:160].strip()
            return SmsResult(ok=False, provider=self.name, status_code=code,
                              error=f"Sinch HTTPError: {code}" + (f" {snippet}" if snippet else ""),
                              transient=500 <= code < 600)
        except Exception as e:  # noqa: BLE001 - network errors are worth retrying
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> SmsResult:
        """Validate credentials with a harmless, read-only call (list batches,
        one result) instead of sending anything."""
        plan_id = _env("SINCH_SERVICE_PLAN_ID")
        token = _env("SINCH_API_TOKEN")
        if not plan_id or not token:
            return SmsResult(ok=False, provider=self.name, error="Missing SINCH_SERVICE_PLAN_ID/SINCH_API_TOKEN")
        req = urllib.request.Request(
            f"{self._base_url()}/xms/v1/{plan_id}/batches?page_size=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return SmsResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except urllib.error.HTTPError as e:
            return SmsResult(ok=False, provider=self.name, status_code=e.code,
                              error="Invalid Sinch credentials or service plan ID")
        except Exception as e:  # noqa: BLE001
            return SmsResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
