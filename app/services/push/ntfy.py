"""ntfy push provider — open-source, free (public instance or self-hosted).
Offered as an alternative to Pushover (which remains the default)."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Optional

from app.services.push.base import PushProvider, PushResult
from app.services import env as _env_mod

_env = _env_mod.get_env

DEFAULT_NTFY_URL = "https://ntfy.sh"


class NtfyProvider(PushProvider):
    name = "ntfy"

    def required_env_vars(self) -> list[str]:
        return ["NTFY_TOPIC", "NTFY_SERVER_URL"]

    def send(
        self, *, body: str, title: Optional[str] = None, device: Optional[str] = None,
        app: Optional[str] = None, to: Optional[str] = None, data: Optional[dict[str, Any]] = None,
        url: Optional[str] = None, url_title: Optional[str] = None,
    ) -> PushResult:
        # 'to' can override the configured topic (lets one gateway send to
        # multiple ntfy topics/subscribers), otherwise falls back to NTFY_TOPIC.
        topic = (to or "").strip() or _env("NTFY_TOPIC", "")
        server = _env("NTFY_SERVER_URL", DEFAULT_NTFY_URL) or DEFAULT_NTFY_URL
        timeout = float(_env("NTFY_TIMEOUT_SECS", "10") or 10.0)

        if not topic:
            return PushResult(ok=False, provider=self.name, error="Missing recipient: NTFY_TOPIC not set and 'to' not provided")

        headers: dict[str, str] = {}
        if title:
            headers["Title"] = title
        link = (url or "").strip() or str((data or {}).get("url") or "").strip()
        if link:
            headers["Click"] = link
        auth_token = _env("NTFY_AUTH_TOKEN", "")
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        req = urllib.request.Request(
            f"{server.rstrip('/')}/{topic}",
            data=(body or "").encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return PushResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            return PushResult(ok=False, provider=self.name, status_code=code,
                               error=f"ntfy HTTPError: {code}", transient=500 <= code < 600)
        except Exception as e:
            return PushResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> PushResult:
        server = _env("NTFY_SERVER_URL", DEFAULT_NTFY_URL) or DEFAULT_NTFY_URL
        topic = _env("NTFY_TOPIC", "")
        if not topic:
            return PushResult(ok=False, provider=self.name, error="Missing NTFY_TOPIC")
        req = urllib.request.Request(f"{server.rstrip('/')}/v1/health")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return PushResult(ok=200 <= int(status) < 300, provider=self.name, status_code=int(status))
        except Exception as e:
            return PushResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
