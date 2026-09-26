"""SendGrid email provider (v3 Mail Send API)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

from app.services.env import get_env
from app.services.email.base import EmailProvider, EmailResult


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return get_env(name, default)


class SendGridProvider(EmailProvider):
    name = "sendgrid"

    def required_env_vars(self) -> list[str]:
        return ["SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL"]

    def send(
        self, *, to: str, subject: str, body: str, email_type: str = "txt",
        message_id: Optional[str] = None,
    ) -> EmailResult:
        api_key = _env("SENDGRID_API_KEY")
        from_email = _env("SENDGRID_FROM_EMAIL")
        from_name = _env("SENDGRID_FROM_NAME", "Message Gateway")
        timeout = float(_env("SENDGRID_TIMEOUT_SECS", "10") or 10.0)

        missing = [k for k, v in [("SENDGRID_API_KEY", api_key), ("SENDGRID_FROM_EMAIL", from_email)] if not v]
        if missing:
            return EmailResult(ok=False, provider=self.name, error=f"Missing env var(s): {', '.join(missing)}")

        content_type = "text/html" if (email_type or "txt").lower() == "html" else "text/plain"
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": from_email, "name": from_name},
            "subject": subject,
            "content": [{"type": content_type, "value": body}],
        }
        if message_id:
            payload["custom_args"] = {"message_id": message_id}

        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                # SendGrid's message id comes back in the X-Message-Id header
                provider_mid = resp.headers.get("X-Message-Id") if hasattr(resp, "headers") else None
                return EmailResult(ok=200 <= int(status) < 300, provider=self.name,
                                    status_code=int(status), provider_message_id=provider_mid)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            return EmailResult(ok=False, provider=self.name, status_code=code,
                                error=f"SendGrid HTTPError: {code}", transient=500 <= code < 600)
        except Exception as e:
            return EmailResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> EmailResult:
        api_key = _env("SENDGRID_API_KEY")
        if not api_key:
            return EmailResult(ok=False, provider=self.name, error="Missing SENDGRID_API_KEY")
        req = urllib.request.Request(
            "https://api.sendgrid.com/v3/user/account",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return EmailResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except urllib.error.HTTPError as e:
            return EmailResult(ok=False, provider=self.name, status_code=e.code, error="Invalid SendGrid API key")
        except Exception as e:
            return EmailResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
