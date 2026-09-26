"""
Pushover push provider.

Apps are declared with a single PUSHOVER_APPS setting mapping "app_name:ENV_VAR_NAME"
pairs, so anyone can define their own app names without touching code. In the web
app these are managed on the Pushover card (Channels > Push); each token is stored
encrypted.

Example (advanced .env use):
  PUSHOVER_APPS=alerts:PUSHOVER_APPTOKEN_ALERTS,backups:PUSHOVER_APPTOKEN_BACKUPS
  PUSHOVER_APPTOKEN_ALERTS=...
  PUSHOVER_APPTOKEN_BACKUPS=...
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from app.services.push.base import PushProvider, PushResult
from app.services import env as _env_mod

_env = _env_mod.get_env

DEFAULT_API_URL = "https://api.pushover.net/1/messages.json"


def _parse_app_map() -> dict[str, str]:
    """Parse PUSHOVER_APPS="name:ENV_VAR,name2:ENV_VAR2" into a dict.
    Falls back to a single implicit 'default' app pointing at
    PUSHOVER_APPTOKEN if PUSHOVER_APPS isn't set, so simple single-app setups
    need only one env var."""
    raw = _env("PUSHOVER_APPS", "")
    mapping: dict[str, str] = {}
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            app_name, env_var = pair.split(":", 1)
            mapping[app_name.strip().lower()] = env_var.strip()
    if not mapping:
        mapping["default"] = "PUSHOVER_APPTOKEN"
    return mapping


def _default_app_name(app_map: dict[str, str]) -> str:
    """The app used when a message names none: PUSHOVER_DEFAULT_APP if set and
    configured, else an app called "default", else the first configured app."""
    chosen = (_env("PUSHOVER_DEFAULT_APP", "") or "").strip().lower()
    if chosen in app_map:
        return chosen
    if "default" in app_map:
        return "default"
    return next(iter(app_map), "default")


def _looks_like_http_url(u: str) -> bool:
    uu = (u or "").strip().lower()
    return uu.startswith("http://") or uu.startswith("https://")


class PushoverProvider(PushProvider):
    name = "pushover"

    def required_env_vars(self) -> list[str]:
        app_map = _parse_app_map()
        return ["PUSHOVER_USER_KEY", "PUSHOVER_APPS"] + list(app_map.values())

    def _resolve_app_token(self, app: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        app_map = _parse_app_map()
        a = (app or "").strip().lower() or _default_app_name(app_map)
        env_name = app_map.get(a)
        if not env_name:
            allowed = ", ".join(sorted(app_map.keys()))
            return None, f"Unknown app={a!r}. Configured apps: {allowed}"
        tok = _env(env_name, "")
        if not tok:
            return None, f"{env_name} is missing"
        return tok, None

    def send(
        self, *, body: str, title: Optional[str] = None, device: Optional[str] = None,
        app: Optional[str] = None, to: Optional[str] = None, data: Optional[dict[str, Any]] = None,
        url: Optional[str] = None, url_title: Optional[str] = None,
    ) -> PushResult:
        msg = (body or "").strip()
        if not msg:
            return PushResult(ok=False, provider=self.name, error="'body' is required")

        recipient = (to or "").strip() or _env("PUSHOVER_USER_KEY", "")
        if not recipient:
            return PushResult(ok=False, provider=self.name, error="Missing recipient (PUSHOVER_USER_KEY not set and 'to' not provided)")

        app_token, err = self._resolve_app_token(app)
        if err:
            return PushResult(ok=False, provider=self.name, error=err)

        api_url = _env("PUSHOVER_API_URL", DEFAULT_API_URL) or DEFAULT_API_URL
        timeout = float(_env("PUSHOVER_TIMEOUT_SECS", "10") or 10.0)

        data_dict = data or {}
        link_url = (url or "").strip() or str(data_dict.get("url") or "").strip()
        link_title = (url_title or "").strip() or str(data_dict.get("url_title") or "").strip()
        if link_title and not link_url:
            link_title = ""
        if link_url and not _looks_like_http_url(link_url):
            return PushResult(ok=False, provider=self.name, error="Invalid 'url' (must start with http:// or https://)")

        payload: dict[str, str] = {"token": app_token, "user": recipient, "message": msg}  # type: ignore[dict-item]
        if title:
            payload["title"] = title.strip()
        if device:
            payload["device"] = device.strip()
        if link_url:
            payload["url"] = link_url
            if link_title:
                payload["url_title"] = link_title

        encoded = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            api_url, data=encoded, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                import json as _json
                parsed = _json.loads(resp.read().decode("utf-8", errors="replace"))
                if status == 200 and parsed.get("status") == 1:
                    return PushResult(ok=True, provider=self.name, status_code=status,
                                       provider_message_id=parsed.get("request"), raw=parsed)
                errors = parsed.get("errors")
                err_msg = "; ".join(str(e) for e in errors) if isinstance(errors, list) else f"non-success (status={status})"
                return PushResult(ok=False, provider=self.name, status_code=status, error=err_msg, raw=parsed)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            return PushResult(ok=False, provider=self.name, status_code=code,
                               error=f"Pushover HTTPError: {code}", transient=500 <= code < 600)
        except Exception as e:
            return PushResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

    def check_config(self) -> PushResult:
        user_key = _env("PUSHOVER_USER_KEY")
        app_token, err = self._resolve_app_token(None)  # checks the default app
        if not user_key:
            return PushResult(ok=False, provider=self.name, error="Missing PUSHOVER_USER_KEY")
        if err:
            return PushResult(ok=False, provider=self.name, error=err)
        payload = urllib.parse.urlencode({"token": app_token, "user": user_key}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.pushover.net/1/users/validate.json", data=payload, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                import json as _json
                parsed = _json.loads(resp.read().decode("utf-8", errors="replace"))
                return PushResult(ok=status == 200 and parsed.get("status") == 1, provider=self.name,
                                   status_code=status, raw=parsed)
        except Exception as e:
            return PushResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
