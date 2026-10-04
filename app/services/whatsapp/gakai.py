"""
Gakai provider: sends WhatsApp messages through a Gakai server (the WhatsApp client in
my projects/gakai.co). Everything goes through Gakai's integration API:

  GET  <address>/api/integrations/v1/accounts   every account (token needs "Read accounts")
  GET  <address>/api/integrations/v1/account    which account a token belongs to
  POST <address>/api/integrations/v1/messages   {"accountId", "phone", "text"}  (token needs "Send messages")
  Authorization: Bearer <application token>

A Gakai token belongs to exactly one account and decides which account sends; `accountId` in the
body is only a safety check (a mismatch is refused with 403 and nothing is sent). So any number of
accounts can be connected, each with its own token:

  GAKAI_URL                  the server address
  GAKAI_LIST_TOKEN           a token with "Read accounts", used to list the accounts to pick from
  GAKAI_ACCOUNTS             "<account id>:<setting holding its token>,..." (each token encrypted separately)
  GAKAI_ACCOUNT_INFO         JSON {"<account id>": {"label", "phone"}} for display and lookup
  GAKAI_DEFAULT_ACCOUNT      the account used when a message names none

Unlike Meta's Cloud API there is no 24-hour window: Gakai sends into any chat on WhatsApp.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from app.services.whatsapp.base import WhatsAppProvider, WhatsAppResult
from app.services import env as _env_mod

_env = _env_mod.get_env

API_PATH = "/api/integrations/v1"


def base_url(raw: Optional[str]) -> str:
    """Normalize the address the user typed: add https:// if missing, drop trailing slashes and
    a pasted /api/integrations/v1 suffix."""
    url = (raw or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    if API_PATH in url:  # a pasted endpoint such as .../api/integrations/v1/messages
        url = url.split(API_PATH, 1)[0].rstrip("/")
    return _reachable_from_here(url)


def _reachable_from_here(url: str) -> str:
    """Inside Docker, localhost is the container itself, not the computer the user is typing
    on. Swap it for host.docker.internal so "http://localhost:3000" just works."""
    if not os.path.exists("/.dockerenv"):
        return url
    parts = urllib.parse.urlsplit(url)
    if parts.hostname in ("localhost", "127.0.0.1", "::1"):
        port = f":{parts.port}" if parts.port else ""
        return urllib.parse.urlunsplit(parts._replace(netloc="host.docker.internal" + port))
    return url


def _request(method: str, url: str, token: str, payload: Optional[dict] = None, timeout: float = 15.0) -> tuple[int, dict[str, Any]]:
    """Returns (status, parsed JSON). HTTP errors are returned, not raised; network errors raise."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", None) or resp.getcode())
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        raw = e.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def _explain(status: int, parsed: dict[str, Any]) -> str:
    msg = (parsed.get("message") or "").strip()
    if status == 401:
        return "Gakai rejected the token (missing, revoked or regenerated). Create or copy a token in Gakai under Settings > Application tokens."
    if status == 403:
        return msg or "This Gakai token does not have permission for that action."
    if status == 404 and not msg:
        return "Gakai could not find that (check the address and that the account still exists)."
    if status == 409:
        return "That WhatsApp account is not connected in Gakai (signed out or still starting). Reconnect it in Gakai, then try again."
    return msg or f"Gakai HTTPError: {status}"


def list_accounts(url: str, token: str) -> list[dict[str, Any]]:
    """Accounts for the dropdown. Raises ValueError with a message safe to show the user."""
    base = base_url(url)
    if not base or not token:
        raise ValueError("Enter the Gakai address and a token first.")
    try:
        status, parsed = _request("GET", base + API_PATH + "/accounts", token, timeout=10)
    except Exception as e:  # noqa: BLE001
        host = urllib.parse.urlparse(base).hostname or ""
        hint = (" The gateway runs in Docker, where localhost is the container itself - use http://host.docker.internal:PORT "
                "to reach something running on this computer.") if host in ("localhost", "127.0.0.1", "::1") else ""
        raise ValueError(f"Could not reach Gakai at {base} ({type(e).__name__}).{hint}") from e
    if status == 403:
        raise ValueError('This token cannot list accounts. In Gakai, tick "Read accounts" on the token (Settings > Application tokens).')
    if not 200 <= status < 300:
        raise ValueError(_explain(status, parsed))
    out = []
    for a in parsed.get("accounts") or []:
        if isinstance(a, dict) and a.get("id"):
            out.append({"id": str(a["id"]), "label": a.get("label") or a["id"], "phone": a.get("phone"),
                        "status": a.get("status"), "current": bool(a.get("current"))})
    return out


def _mapping() -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in (_env("GAKAI_ACCOUNTS") or "").split(","):
        if ":" in pair:
            a, v = pair.split(":", 1)
            if a.strip() and v.strip():
                out[a.strip()] = v.strip()
    return out


def configured_accounts() -> list[dict[str, Any]]:
    """The connected accounts: id, label, phone, token_env, token and is_default."""
    mapping = _mapping()
    try:
        info = json.loads(_env("GAKAI_ACCOUNT_INFO") or "{}")
    except ValueError:
        info = {}
    chosen = (_env("GAKAI_DEFAULT_ACCOUNT") or "").strip()
    default = chosen if chosen in mapping else next(iter(mapping), "")
    return [
        {"id": a, "label": (info.get(a) or {}).get("label") or a, "phone": (info.get(a) or {}).get("phone"),
         "token_env": env, "token": (_env(env) or "").strip(), "is_default": a == default}
        for a, env in mapping.items()
    ]


def resolve_account(ref: Optional[str]) -> Optional[dict[str, Any]]:
    """The connected account a message asked for, by its Gakai account id (names can repeat or be
    renamed; ids never change); the default when it names none. None when it names one that is
    not connected."""
    accounts = configured_accounts()
    want = (ref or "").strip()
    if not want:
        return next((a for a in accounts if a["is_default"]), None)
    return next((a for a in accounts if a["id"] == want), None)


class GakaiProvider(WhatsAppProvider):
    name = "gakai"

    def required_env_vars(self) -> list[str]:
        return ["GAKAI_URL", "GAKAI_LIST_TOKEN", "GAKAI_ACCOUNTS"]

    def send(self, *, to: str, body: str, message_id: str, account: Optional[str] = None) -> WhatsAppResult:
        url = base_url(_env("GAKAI_URL"))
        acct = resolve_account(account)
        if not url or not _mapping():
            return WhatsAppResult(ok=False, provider=self.name, error="Gakai is not set up: add the address and at least one account.")
        if acct is None:
            ids = ", ".join(a["id"] for a in configured_accounts())
            return WhatsAppResult(ok=False, provider=self.name, error=f"Gakai account {account!r} isn't connected. Connected account ids: {ids}.")
        if not acct["token"]:
            return WhatsAppResult(ok=False, provider=self.name, error=f"No token saved for the Gakai account \"{acct['label']}\".")
        if not (to or "").strip():
            return WhatsAppResult(ok=False, provider=self.name, error="Missing recipient: no 'to' and no default recipient is set.")
        timeout = float(_env("GAKAI_TIMEOUT_SECS", "20") or 20.0)
        try:
            status, parsed = _request("POST", url + API_PATH + "/messages", acct["token"],
                                      {"accountId": acct["id"], "phone": to.strip(), "text": body}, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - network errors are worth retrying
            return WhatsAppResult(ok=False, provider=self.name, error=f"{type(e).__name__}: {e}", transient=True)
        if 200 <= status < 300 and parsed.get("ok", True):
            msg = parsed.get("message")
            msg = msg if isinstance(msg, dict) else {}
            return WhatsAppResult(ok=True, provider=self.name, status_code=status, raw=parsed,
                                  provider_message_id=msg.get("id"), provider_status=msg.get("ackName"))
        if status == 404 and "not on whatsapp" in (parsed.get("message") or "").lower():
            err = "That number is not on WhatsApp."
        else:
            err = _explain(status, parsed)
        # 409 (account still starting) and 5xx/429 can clear up on their own; the rest cannot.
        return WhatsAppResult(ok=False, provider=self.name, status_code=status, error=err, raw=parsed,
                              transient=status in (409, 429) or status >= 500)

    def check_config(self) -> WhatsAppResult:
        """Read-only: GET /account with each account's token confirms it works and belongs to that account."""
        url = base_url(_env("GAKAI_URL"))
        accounts = configured_accounts()
        if not url or not accounts:
            return WhatsAppResult(ok=False, provider=self.name, error="Missing Gakai address or accounts")
        names = []
        for a in accounts:
            label = a["label"]
            if not a["token"]:
                return WhatsAppResult(ok=False, provider=self.name, error=f"The Gakai account \"{label}\" has no token. Enter that account's own token.")
            try:
                status, parsed = _request("GET", url + API_PATH + "/account", a["token"], timeout=10)
            except Exception as e:  # noqa: BLE001
                return WhatsAppResult(ok=False, provider=self.name, error=f"Could not reach Gakai at {url} ({type(e).__name__}).")
            if status == 404:
                return WhatsAppResult(ok=False, provider=self.name, status_code=status,
                                      error=f"The account \"{label}\" no longer exists in Gakai. Untick it.")
            if not 200 <= status < 300:
                return WhatsAppResult(ok=False, provider=self.name, status_code=status, error=f"{label}: {_explain(status, parsed)}")
            acct = parsed.get("account") or {}
            if acct.get("id") != a["id"]:
                return WhatsAppResult(
                    ok=False, provider=self.name, status_code=status,
                    error=(f"The token entered for \"{label}\" belongs to the Gakai account \"{acct.get('label') or acct.get('id')}\". "
                           "Each account needs its own token."))
            scopes = (parsed.get("token") or {}).get("scopes")
            if isinstance(scopes, list) and "messages:send" not in scopes:
                return WhatsAppResult(ok=False, provider=self.name, status_code=status,
                                      error=f"The token for \"{label}\" can't send messages. In Gakai, tick Send messages on that token (Settings > Application tokens).")
            if acct.get("status") and acct["status"] != "WORKING":
                return WhatsAppResult(ok=False, provider=self.name, status_code=status,
                                      error=f"The account \"{label}\" is {acct['status']} in Gakai (not connected). Open Gakai and pair or reconnect it.")
            names.append(label)
        return WhatsAppResult(ok=True, provider=self.name, provider_status=", ".join(names))
