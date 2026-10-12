"""
Text commands: reply to "/help", "/status", "/speed" or "/reboot" sent by SMS to
a phone paired with this gateway (incoming messages must be turned on).

Only numbers on the allowed list are answered, and only for the commands that are
switched on; both are set on the SMS channel page. Anyone else gets no reply at
all, so the number doesn't advertise that it takes commands.

Commands run inside the gateway's container. What they report about the machine
(uptime, load, memory, disk, temperature) is what the container can see of its
host. A container cannot restart its host, so /reboot only leaves a request file
in a folder shared with the host; a small helper installed there (host/install-
reboot-helper.sh) acts on it. Without the helper, /reboot says it isn't set up.
"""

from __future__ import annotations

import glob
import os
import re
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

from app.services import access, secret_store
from app.services.env import get_env

PREFIX = "/"
HOST_CONTROL_DIR = os.environ.get("HOST_CONTROL_DIR", "/app/host-control").strip() or "/app/host-control"
REBOOT_REQUEST = os.path.join(HOST_CONTROL_DIR, "reboot-request")
REBOOT_HELPER_MARKER = os.path.join(HOST_CONTROL_DIR, "reboot-helper-installed")
_REBOOT_CONFIRM_SECS = 300
_REBOOT_DELAY_SECS = 30     # long enough for the "rebooting" reply to leave the phone
_MIN_NUMBER_DIGITS = 7      # shorter entries would match far too many senders

_pending_reboot: dict[str, float] = {}   # sender digits -> when the confirmation expires
_lock = threading.Lock()


# ---------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, hours, minutes = minutes // 1440, (minutes % 1440) // 60, minutes % 60
    return (f"{days}d " if days else "") + f"{hours}h {minutes}m"


def _temperature() -> Optional[float]:
    readings = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        raw = _read(path)
        if raw.lstrip("-").isdigit():
            readings.append(int(raw) / 1000.0)
    return max(readings) if readings else None


def _public_ip() -> str:
    for url in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=4) as resp:
                ip = resp.read(64).decode("ascii", errors="ignore").strip()
            if ip:
                return ip
        except Exception:  # noqa: BLE001
            continue
    return "unknown"


def _cmd_status(sender: str) -> str:
    from app.services import channels

    lines = ["Server status"]
    uptime = _read("/proc/uptime").split()
    if uptime:
        lines.append("Uptime: " + _duration(float(uptime[0])))
    load = _read("/proc/loadavg").split()
    if len(load) >= 3:
        lines.append("Load: " + " ".join(load[:3]))
    mem = dict(re.findall(r"^(\w+):\s+(\d+) kB", _read("/proc/meminfo"), flags=re.M))
    if mem.get("MemTotal") and mem.get("MemAvailable"):
        total, free = int(mem["MemTotal"]), int(mem["MemAvailable"])
        lines.append(f"Memory: {100 * (total - free) // total}% of {total / 1048576:.1f} GB")
    try:
        st = os.statvfs("/")
        lines.append(f"Disk: {100 - 100 * st.f_bavail // st.f_blocks}% used, {st.f_bavail * st.f_frsize / 1e9:.0f} GB free")
    except OSError:
        pass
    temp = _temperature()
    if temp is not None:
        lines.append(f"Temp: {temp:.0f} C")
    lines.append("Public IP: " + _public_ip())
    ready = [s["label"] for s in channels.all_status() if s["state"] in ("ready", "ready_degraded")]
    lines.append("Channels ready: " + (", ".join(ready) if ready else "none"))
    return "\n".join(lines)


def _mbps(byte_count: int, seconds: float) -> float:
    return byte_count * 8 / max(seconds, 0.001) / 1e6


def _cmd_speed(sender: str) -> str:
    """Download and upload against Cloudflare's speed test endpoints. Measured from
    this container, which shares the machine's internet connection."""
    # The test site refuses very large single downloads, so a fast line gets several
    # rounds: enough data for the figure to mean something, without making a slow one wait.
    down_bytes, up_bytes, rounds, enough_secs = 50_000_000, 25_000_000, 4, 4.0
    headers = {"User-Agent": "message-gateway-speedtest"}
    try:
        got, spent = 0, 0.0
        for _ in range(rounds):
            req = urllib.request.Request(f"https://speed.cloudflare.com/__down?bytes={down_bytes}", headers=headers)
            start = time.monotonic()
            with urllib.request.urlopen(req, timeout=20) as resp:
                first_byte = time.monotonic()   # connection setup isn't transfer time
                while time.monotonic() - start < 15:
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    got += len(chunk)
            spent += time.monotonic() - first_byte
            if spent >= enough_secs:
                break
        down = _mbps(got, spent)

        req = urllib.request.Request("https://speed.cloudflare.com/__up", data=os.urandom(up_bytes), method="POST",
                                     headers={**headers, "Content-Type": "application/octet-stream"})
        start = time.monotonic()
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        up = _mbps(up_bytes, time.monotonic() - start)
    except Exception as e:  # noqa: BLE001
        return f"Speed test failed ({type(e).__name__}). The server may be offline or the test site unreachable."
    return f"Speed test\nDownload: {down:.0f} Mbps\nUpload: {up:.0f} Mbps"


def reboot_available() -> bool:
    return os.path.exists(REBOOT_HELPER_MARKER)


def _cmd_reboot(sender: str) -> str:
    if not reboot_available():
        return "Reboot isn't set up on this server. Run host/install-reboot-helper.sh there once to allow it."
    with _lock:
        _pending_reboot[_digits(sender)] = time.time() + _REBOOT_CONFIRM_SECS
    return "Reboot the server? Reply Y to confirm or N to cancel (5 minutes)."


def _cmd_help(sender: str) -> str:
    on = enabled_commands()
    lines = ["Commands:"] + [f"{PREFIX}{name} - {COMMANDS[name]['help']}" for name in COMMANDS if name in on]
    return "\n".join(lines)


COMMANDS: dict[str, dict[str, Any]] = {
    "help": {"help": "list the commands", "label": "List the available commands", "run": _cmd_help, "default": True},
    "status": {"help": "server status", "label": "Server status: uptime, load, memory, disk, temperature, public IP, channels", "run": _cmd_status, "default": True},
    "speed": {"help": "internet speed test", "label": "Internet speed test (download and upload); takes up to a minute on a slow line", "run": _cmd_speed, "default": True},
    "reboot": {"help": "restart the server", "label": "Restart the server, after a Y/N confirmation", "run": _cmd_reboot, "default": False},
}


# ---------------------------------------------------------------------
# Settings: who may use commands, and which ones
# ---------------------------------------------------------------------
def _digits(number: Optional[str]) -> str:
    return re.sub(r"\D", "", number or "")


def allowed_numbers() -> list[str]:
    return [line.strip() for line in (get_env("SMS_COMMANDS_ALLOWED") or "").splitlines() if line.strip()]


def sender_allowed(sender: Optional[str]) -> bool:
    """An entry matches when one number ends with the other's digits, so a number
    saved without its country code (8577075969) still matches +18577075969, and a
    sender shown without one still matches an entry saved with it."""
    digits = _digits(sender)
    if len(digits) < _MIN_NUMBER_DIGITS:
        return False
    return any(len(d) >= _MIN_NUMBER_DIGITS and (digits.endswith(d) or d.endswith(digits))
               for d in map(_digits, allowed_numbers()))


def enabled_commands() -> set[str]:
    stored = get_env("SMS_COMMANDS_ENABLED")
    if stored is None:
        return {name for name, c in COMMANDS.items() if c["default"]}
    return {n.strip().lower() for n in stored.split(",") if n.strip().lower() in COMMANDS}


def config() -> dict[str, Any]:
    from app.services import smsgate_webhooks

    on = enabled_commands()
    return {
        "ok": True,
        "numbers": "\n".join(allowed_numbers()),
        "commands": [{"name": name, "label": c["label"], "enabled": name in on} for name, c in COMMANDS.items()],
        "incoming_on": bool(smsgate_webhooks.secret()),
        "reboot_available": reboot_available(),
    }


def save_config(numbers_text: str, enabled: list[str]) -> dict[str, Any]:
    numbers: list[str] = []
    for line in re.split(r"[\n,;]+", numbers_text or ""):
        line = line.strip()
        if not line:
            continue
        if len(_digits(line)) < _MIN_NUMBER_DIGITS:
            return {"ok": False, "error": f"{line!r} is too short to be a phone number. Use at least {_MIN_NUMBER_DIGITS} digits."}
        if line not in numbers:
            numbers.append(line)
    secret_store.set_settings({
        "SMS_COMMANDS_ALLOWED": "\n".join(numbers),
        "SMS_COMMANDS_ENABLED": ",".join(n for n in COMMANDS if n in {e.strip().lower() for e in enabled}),
    })
    return config()


# ---------------------------------------------------------------------
# Handling an incoming text
# ---------------------------------------------------------------------
def _reply(to: str, text: str) -> None:
    """Send the answer like any other SMS, so it is logged and retried."""
    from app.main import enqueue_message  # local import: main imports the routes that reach this module
    from app.schemas import MessageRequest

    enqueue_message(MessageRequest(channel="sms", to=to, body=text), access.owner_id(), source="command")


def _do_reboot() -> None:
    time.sleep(_REBOOT_DELAY_SECS)
    os.makedirs(HOST_CONTROL_DIR, exist_ok=True)
    with open(REBOOT_REQUEST, "w", encoding="utf-8") as f:
        f.write(str(int(time.time())))


def clear_stale_reboot_request() -> None:
    """On startup: a request left over from before a restart must never fire again."""
    try:
        os.unlink(REBOOT_REQUEST)
    except OSError:
        pass


def _answer(sender: str, text: str) -> Optional[tuple[str, Optional[Callable[[], None]]]]:
    """(reply, something to do after replying), or None when the text isn't for us."""
    key = _digits(sender)
    with _lock:
        expires = _pending_reboot.pop(key, None)
    if expires and time.time() < expires and "reboot" in enabled_commands():
        if text.strip().lower() in ("y", "yes"):
            return f"Rebooting in {_REBOOT_DELAY_SECS} seconds.", _do_reboot
        return "Reboot cancelled.", None

    if not text.startswith(PREFIX):
        return None
    name = (text[len(PREFIX):].split() or [""])[0].lower()
    if name not in COMMANDS:
        return f"Unknown command {PREFIX}{name}. Try {PREFIX}help.", None
    if name not in enabled_commands():
        return f"{PREFIX}{name} is turned off.", None
    return COMMANDS[name]["run"](sender), None


def _run(sender: str, text: str) -> None:
    try:
        result = _answer(sender, text)
        if result is None:
            return
        reply, then = result
        _reply(sender, reply)
        if then:
            then()
    except Exception as e:  # noqa: BLE001
        print(f"[sms-commands] error handling a command: {e!r}")


def handle_incoming(sender: Optional[str], text: Optional[str]) -> bool:
    """Called for every text a paired phone receives. Returns whether it was taken
    as a command. The work happens on its own thread: a speed test must not keep
    the phone's webhook waiting."""
    text = (text or "").strip()
    if not sender or not text or not sender_allowed(sender):
        return False
    with _lock:
        waiting = _digits(sender) in _pending_reboot
    if not text.startswith(PREFIX) and not waiting:
        return False
    threading.Thread(target=_run, args=(sender, text), daemon=True, name="sms-command").start()
    return True
