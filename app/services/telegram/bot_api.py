"""
Telegram Bot API provider.

https://core.telegram.org/bots/api
  POST https://api.telegram.org/bot<TOKEN>/sendMessage   {"chat_id": ..., "text": "..."}
  GET  https://api.telegram.org/bot<TOKEN>/getMe          - validates the token (used by check_config)
  GET  https://api.telegram.org/bot<TOKEN>/getUpdates     - recent messages sent TO the bot, used to
                                                             discover chat ids (see recent_chats() below)

Telegram's API returns HTTP 200 with {"ok": false, ...} for most application-level errors (bad
chat_id, bot blocked by the user, etc.), not just non-2xx statuses - both are checked here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from app.services.telegram.base import TelegramProvider, TelegramResult
from app.services import env as _env_mod

_env = _env_mod.get_env

API_ROOT = "https://api.telegram.org"


def _call(token: str, method: str, params: Optional[dict[str, Any]] = None, timeout: float = 15.0) -> dict[str, Any]:
    """POST to a Bot API method if params are given, else GET. Raises urllib.error.HTTPError on a
    non-2xx response; otherwise returns the parsed JSON body (which may still have "ok": false)."""
    url = f"{API_ROOT}/bot{token}/{method}"
    if params is not None:
        req = urllib.request.Request(
            url, data=json.dumps(params).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


class TelegramBotProvider(TelegramProvider):
    name = "bot_api"

    def required_env_vars(self) -> list[str]:
        return ["TELEGRAM_BOT_TOKEN"]

    def send(self, *, to: str, body: str, message_id: str) -> TelegramResult:
        token = _env("TELEGRAM_BOT_TOKEN")
        timeout = float(_env("TELEGRAM_TIMEOUT_SECS", "15") or 15.0)
        if not token:
            return TelegramResult(ok=False, provider=self.name, error="Missing env var(s): TELEGRAM_BOT_TOKEN")
        if not (to or "").strip():
            return TelegramResult(ok=False, provider=self.name, error="Missing chat id: no 'to' and no default chat ID is set.")

        try:
            parsed = _call(token, "sendMessage", {"chat_id": to, "text": body}, timeout=timeout)
        except urllib.error.HTTPError as e:
            code = int(getattr(e, "code", 0) or 0)
            snippet = e.read().decode("utf-8", errors="replace")[:160].strip()
            return TelegramResult(ok=False, provider=self.name, status_code=code,
                                   error=f"Telegram HTTPError: {code}" + (f" {snippet}" if snippet else ""),
                                   transient=code >= 500 or code == 429)
        except Exception as e:  # noqa: BLE001 - network errors are worth retrying
            return TelegramResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)

        if not parsed.get("ok"):
            desc = parsed.get("description") or "Telegram rejected the request."
            code = parsed.get("error_code")
            # 429 (flood control) and 5xx are worth retrying; a bad chat id or a bot the user
            # blocked (403) is permanent.
            transient = code == 429 or (isinstance(code, int) and code >= 500)
            return TelegramResult(ok=False, provider=self.name, status_code=code, error=desc, transient=transient, raw=parsed)

        result = parsed.get("result") or {}
        return TelegramResult(ok=True, provider=self.name, provider_message_id=str(result.get("message_id") or ""), raw=parsed)

    def check_config(self) -> TelegramResult:
        """Validate the token with getMe - a read-only call, sends nothing."""
        token = _env("TELEGRAM_BOT_TOKEN")
        if not token:
            return TelegramResult(ok=False, provider=self.name, error="Missing TELEGRAM_BOT_TOKEN")
        try:
            parsed = _call(token, "getMe", None, timeout=10)
        except urllib.error.HTTPError as e:
            return TelegramResult(ok=False, provider=self.name, status_code=e.code, error="Invalid Telegram bot token")
        except Exception as e:  # noqa: BLE001
            return TelegramResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}")
        if not parsed.get("ok"):
            return TelegramResult(ok=False, provider=self.name, error=parsed.get("description") or "Invalid Telegram bot token")
        bot = parsed.get("result") or {}
        username = bot.get("username")
        return TelegramResult(ok=True, provider=self.name, provider_status=f"@{username}" if username else None, raw=parsed)


def recent_chats(token: str, limit: int = 10) -> list[dict[str, Any]]:
    """Chat ids that have recently messaged the bot (via getUpdates), newest first, deduped -
    used by the "Find your chat ID" helper in the web app. Telegram bots can't discover a chat id
    any other way: the user has to message the bot first."""
    parsed = _call(token, "getUpdates", None, timeout=10)
    if not parsed.get("ok"):
        raise ValueError(parsed.get("description") or "Telegram rejected the request.")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for update in reversed(parsed.get("result") or []):
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or str(chat_id) in seen:
            continue
        seen.add(str(chat_id))
        name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username") or ""
        out.append({"chat_id": str(chat_id), "name": name, "username": chat.get("username")})
        if len(out) >= limit:
            break
    return out
