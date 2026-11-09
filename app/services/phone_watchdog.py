"""
Phone watchdog: tell the owner when the SMSGate phone stops checking in.

An Android phone that hangs, loses its connection or has its app killed just goes
quiet: nothing in the gateway notices, and texts queue up unsent until someone
looks. This module polls the SMSGate server for the paired phone's `lastSeen`
time and sends a push notification when it is older than a threshold, then another
when the phone is back.

Settings (environment, or Settings in the web app):
- PHONE_WATCHDOG            "off" turns it off. Default: on whenever SMSGATE_USERNAME/PASSWORD are set.
- PHONE_WATCHDOG_STALE_MIN  minutes of silence before alerting. Default 30.
- PHONE_WATCHDOG_REMIND_MIN minutes between repeat alerts while it stays down. Default 180.
- PHONE_WATCHDOG_APP        Pushover app (or ntfy topic) to alert through. Default: the push default.

It only ever *reports*; it never touches the phone or the message queue.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from app.services import env as _env_mod

_env = _env_mod.get_env

CHECK_INTERVAL_SECS = 60
# Server unreachable this many checks in a row is reported as its own problem.
SERVER_DOWN_CHECKS = 5


def enabled() -> bool:
    if (_env("PHONE_WATCHDOG") or "").strip().lower() in ("off", "0", "false", "no"):
        return False
    return bool(_env("SMSGATE_USERNAME") and _env("SMSGATE_PASSWORD"))


def _minutes(name: str, default: int) -> int:
    try:
        return max(1, int(float(_env(name) or default)))
    except ValueError:
        return default


def parse_time(value: object) -> Optional[datetime]:
    """An ISO-8601 time from the server (`...Z` or with an offset) as an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def newest_last_seen(devices: object, device_id: str = "") -> Optional[datetime]:
    """The most recent `lastSeen` among the paired phones (or just the chosen one)."""
    if not isinstance(devices, list):
        return None
    times = []
    for d in devices:
        if not isinstance(d, dict) or (device_id and d.get("id") != device_id):
            continue
        t = parse_time(d.get("lastSeen"))
        if t:
            times.append(t)
    return max(times) if times else None


@dataclass
class WatchState:
    down_since: Optional[float] = None      # monotonic-agnostic epoch seconds when we first saw it stale
    last_alert: Optional[float] = None      # epoch seconds of the last alert sent
    server_failures: int = 0
    server_alerted: bool = False
    log: list[str] = field(default_factory=list)


def decide(state: WatchState, *, now: float, silent_secs: Optional[float], server_ok: bool,
           stale_secs: float, remind_secs: float) -> list[tuple[str, str]]:
    """Update `state` for one check and return the notifications to send as (title, body).

    silent_secs: seconds since the phone was last seen, or None if it can't be told.
    server_ok:   whether the SMSGate server answered at all.
    """
    out: list[tuple[str, str]] = []

    if not server_ok:
        state.server_failures += 1
        if state.server_failures >= SERVER_DOWN_CHECKS and not state.server_alerted:
            state.server_alerted = True
            out.append(("SMS gateway: server unreachable",
                        "The gateway can't reach its SMSGate server, so texts can't be sent or received. "
                        "Check that the smsgate container is running."))
        return out  # can't judge the phone without the server

    if state.server_alerted:
        state.server_alerted = False
        out.append(("SMS gateway: server back", "The SMSGate server is reachable again."))
    state.server_failures = 0

    if silent_secs is None:
        return out  # no phone paired yet, or no timestamp: nothing to judge

    if silent_secs > stale_secs:
        mins = int(silent_secs // 60)
        if state.down_since is None:
            state.down_since = now - silent_secs
            state.last_alert = now
            out.append(("SMS phone is offline",
                        f"The SMS phone hasn't checked in for {mins} min. Texts will queue until it's back. "
                        "Check that it's powered on, unlocked-and-responsive and online; restart it if it's frozen."))
        elif state.last_alert is not None and now - state.last_alert >= remind_secs:
            state.last_alert = now
            out.append(("SMS phone still offline", f"Still no check-in from the SMS phone after {mins} min."))
    elif state.down_since is not None:
        mins = int((now - state.down_since) // 60)
        state.down_since = None
        state.last_alert = None
        out.append(("SMS phone is back", f"The SMS phone is checking in again (it was offline about {mins} min)."))
    return out


def _fetch_devices() -> Optional[list]:
    """The paired phones from the SMSGate server, or None if it couldn't be reached."""
    from app.services.sms.smsgate import SmsGateProvider
    try:
        status, raw = SmsGateProvider()._request("GET", "/devices")
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    return parsed if status == 200 and isinstance(parsed, list) else None


def _notify(title: str, body: str) -> None:
    try:
        from app.services.push import get_push_provider
        app = (_env("PHONE_WATCHDOG_APP") or "").strip() or None
        res = get_push_provider().send(title=title, body=body, app=app)
        if not res.ok:
            print(f"[phone-watchdog] could not send alert '{title}': {res.error}")
    except Exception as e:  # noqa: BLE001 - an alert problem must never kill the loop
        print(f"[phone-watchdog] could not send alert '{title}': {e!r}")


def check_once(state: WatchState, *, now: Optional[float] = None,
               fetch: Callable[[], Optional[list]] = _fetch_devices,
               notify: Callable[[str, str], None] = _notify) -> None:
    now = time.time() if now is None else now
    devices = fetch()
    last = newest_last_seen(devices, (_env("SMSGATE_DEVICE_ID") or "").strip()) if devices is not None else None
    silent = max(0.0, now - last.timestamp()) if last else None
    for title, body in decide(
        state, now=now, silent_secs=silent, server_ok=devices is not None,
        stale_secs=_minutes("PHONE_WATCHDOG_STALE_MIN", 30) * 60,
        remind_secs=_minutes("PHONE_WATCHDOG_REMIND_MIN", 180) * 60,
    ):
        print(f"[phone-watchdog] {title}: {body}")
        notify(title, body)


def watch_loop() -> None:
    """Runs forever in a daemon thread. Re-reads the settings each round so changes apply without a restart."""
    state = WatchState()
    time.sleep(120)  # let the server and phone settle after a restart before judging
    while True:
        try:
            if enabled():
                check_once(state)
        except Exception as e:  # noqa: BLE001
            print(f"[phone-watchdog] check failed: {e!r}")
        time.sleep(CHECK_INTERVAL_SECS)
