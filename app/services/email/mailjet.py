"""
Mailjet (Send API v3.1) email provider. Ported from the old codebase's
services/email/mailjet_delivery.py, adapted to the EmailProvider interface.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from app.services.email.base import EmailProvider, EmailResult


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return default if v is None or v == "" else v


def _basic_auth_header(api_key: str, secret_key: str) -> str:
    token = f"{api_key}:{secret_key}".encode("utf-8")
    return "Basic " + base64.b64encode(token).decode("ascii")


def _extract_provider_message_id(resp: dict[str, Any]) -> Optional[str]:
    try:
        messages = resp.get("Messages")
        if isinstance(messages, list) and messages:
            msg0 = messages[0]
            to_list = msg0.get("To")
            if isinstance(to_list, list) and to_list:
                to0 = to_list[0]
                mid = to0.get("MessageID") or to0.get("MessageUUID") or to0.get("MessageId")
                if mid is not None:
                    return str(mid)
            mid2 = msg0.get("MessageID") or msg0.get("MessageUUID") or msg0.get("MessageId")
            if mid2 is not None:
                return str(mid2)
    except Exception:
        return None
    return None


class MailjetProvider(EmailProvider):
    name = "mailjet"

    def required_env_vars(self) -> list[str]:
        return ["MAILJET_API_KEY", "MAILJET_SECRET_KEY", "MAILJET_FROM_EMAIL"]

    def send(
        self, *, to: str, subject: str, body: str, email_type: str = "txt",
        message_id: Optional[str] = None,
    ) -> EmailResult:
        api_key = _env("MAILJET_API_KEY")
        secret_key = _env("MAILJET_SECRET_KEY")
        from_email = _env("MAILJET_FROM_EMAIL")
        from_name = _env("MAILJET_FROM_NAME", "Message Gateway")
        api_url = _env("MAILJET_API_URL", "https://api.mailjet.com/v3.1/send")
        timeout = float(_env("MAILJET_TIMEOUT_SECS", "10") or 10.0)

        missing = [k for k, v in [
            ("MAILJET_API_KEY", api_key), ("MAILJET_SECRET_KEY", secret_key),
            ("MAILJET_FROM_EMAIL", from_email),
        ] if not v]
        if missing:
            return EmailResult(ok=False, provider=self.name, error=f"Missing env var(s): {', '.join(missing)}")

        et = (email_type or "txt").strip().lower()
        msg: dict[str, Any] = {
            "From": {"Email": from_email, "Name": from_name},
            "To": [{"Email": to}],
            "Subject": subject,
        }
        msg["HTMLPart" if et == "html" else "TextPart"] = body
        if message_id:
            msg["CustomID"] = message_id

        data = json.dumps({"Messages": [msg]}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": _basic_auth_header(api_key, secret_key),  # type: ignore[arg-type]
        }
        req = urllib.request.Request(api_url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
                ok = 200 <= int(status) < 300
                if not ok:
                    return EmailResult(ok=False, provider=self.name, status_code=int(status),
                                        error=f"Mailjet non-2xx: {status}", raw=parsed,
                                        transient=500 <= int(status) < 600)
                return EmailResult(ok=True, provider=self.name, status_code=int(status),
                                    provider_message_id=_extract_provider_message_id(parsed), raw=parsed)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            return EmailResult(ok=False, provider=self.name, status_code=code,
                                error=f"Mailjet HTTPError: {code}", transient=500 <= code < 600)
        except Exception as e:
            return EmailResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> EmailResult:
        api_key = _env("MAILJET_API_KEY")
        secret_key = _env("MAILJET_SECRET_KEY")
        missing = [k for k, v in [("MAILJET_API_KEY", api_key), ("MAILJET_SECRET_KEY", secret_key)] if not v]
        if missing:
            return EmailResult(ok=False, provider=self.name, error=f"Missing: {', '.join(missing)}")
        req = urllib.request.Request(
            "https://api.mailjet.com/v3/REST/apikey",
            headers={"Authorization": _basic_auth_header(api_key, secret_key)},  # type: ignore[arg-type]
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return EmailResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except urllib.error.HTTPError as e:
            return EmailResult(ok=False, provider=self.name, status_code=e.code, error="Invalid Mailjet credentials")
        except Exception as e:
            return EmailResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
