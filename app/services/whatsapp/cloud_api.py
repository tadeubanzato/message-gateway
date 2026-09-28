"""
WhatsApp Business Platform provider (Meta's Cloud API - the official, Meta-hosted
WhatsApp Business API; not the deprecated on-premises client, and not a
third-party BSP wrapper).

https://developers.facebook.com/docs/whatsapp/cloud-api/
  POST https://graph.facebook.com/<version>/<phone_number_id>/messages
  Authorization: Bearer <permanent access token>
  Body: {"messaging_product": "whatsapp", "recipient_type": "individual",
         "to": "<digits, no +>", "type": "text", "text": {"body": "..."}}

The big constraint that isn't optional, unlike SMS or Telegram: a free-form text
message only delivers within the 24-hour customer service window - the recipient
must have messaged this WhatsApp number within the last 24 hours. Outside that
window, WhatsApp requires a pre-approved message *template* (HSM), which this
provider deliberately doesn't support (same "plain message only" scope as push
and Telegram) - error 131047 is caught and translated into a clear explanation
instead of a raw API error.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Optional

from app.services.whatsapp.base import WhatsAppProvider, WhatsAppResult
from app.services import env as _env_mod

_env = _env_mod.get_env

API_ROOT = "https://graph.facebook.com"
DEFAULT_VERSION = "v26.0"

# The 24-hour-window violation - WhatsApp's most common, most confusing failure
# for anyone used to SMS/email/push, so it gets its own clear explanation.
_WINDOW_CLOSED_CODE = 131047


class WhatsAppCloudProvider(WhatsAppProvider):
    name = "cloud_api"

    def required_env_vars(self) -> list[str]:
        return ["WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN"]

    def _version(self) -> str:
        return (_env("WHATSAPP_API_VERSION", DEFAULT_VERSION) or DEFAULT_VERSION).strip() or DEFAULT_VERSION

    def send(self, *, to: str, body: str, message_id: str) -> WhatsAppResult:
        phone_id = _env("WHATSAPP_PHONE_NUMBER_ID")
        token = _env("WHATSAPP_ACCESS_TOKEN")
        timeout = float(_env("WHATSAPP_TIMEOUT_SECS", "15") or 15.0)
        if not phone_id or not token:
            return WhatsAppResult(ok=False, provider=self.name, error="Missing env var(s): WHATSAPP_PHONE_NUMBER_ID/WHATSAPP_ACCESS_TOKEN")
        if not (to or "").strip():
            return WhatsAppResult(ok=False, provider=self.name, error="Missing recipient: no 'to' and no default recipient is set.")

        digits = re.sub(r"\D", "", to)  # WhatsApp wants the number with no leading '+'
        url = f"{API_ROOT}/{self._version()}/{phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": digits, "type": "text", "text": {"body": body},
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", None) or resp.getcode())
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            try:
                parsed = json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:
                parsed = {}
            err = parsed.get("error") or {}
            if err.get("code") == _WINDOW_CLOSED_CODE:
                return WhatsAppResult(
                    ok=False, provider=self.name, status_code=code, raw=parsed,
                    error=("This recipient hasn't messaged your WhatsApp number in the last 24 hours, so a "
                           "free-form message can't be delivered - they need to message you first, or you "
                           "need an approved message template (not supported here)."),
                )
            desc = err.get("message") or f"WhatsApp HTTPError: {code}"
            return WhatsAppResult(ok=False, provider=self.name, status_code=code, error=desc, raw=parsed,
                                   transient=code >= 500 or code == 429)
        except Exception as e:  # noqa: BLE001 - network errors are worth retrying
            return WhatsAppResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

        message_id_out = None
        try:
            message_id_out = parsed["messages"][0]["id"]
        except Exception:
            pass
        return WhatsAppResult(ok=200 <= status < 300, provider=self.name, status_code=status,
                               provider_message_id=message_id_out, raw=parsed)

    def check_config(self) -> WhatsAppResult:
        """Validate credentials with a read-only GET on the phone number itself - sends nothing."""
        phone_id = _env("WHATSAPP_PHONE_NUMBER_ID")
        token = _env("WHATSAPP_ACCESS_TOKEN")
        if not phone_id or not token:
            return WhatsAppResult(ok=False, provider=self.name, error="Missing WHATSAPP_PHONE_NUMBER_ID/WHATSAPP_ACCESS_TOKEN")
        req = urllib.request.Request(
            f"{API_ROOT}/{self._version()}/{phone_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = int(getattr(resp, "status", None) or resp.getcode())
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            return WhatsAppResult(ok=False, provider=self.name, status_code=e.code,
                                   error="Invalid WhatsApp phone number ID or access token")
        except Exception as e:  # noqa: BLE001
            return WhatsAppResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
        name = parsed.get("verified_name") or parsed.get("display_phone_number")
        return WhatsAppResult(ok=200 <= status < 300, provider=self.name, status_code=status,
                               provider_status=name, raw=parsed)
