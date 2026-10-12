"""
The bundled SMSGate server (the optional `smsgate` compose profile).

SMSGate turns an Android phone into an SMS sender. Its server runs in its own
container next to the gateway; this module is the gateway's side of that pairing:

- the private token a phone must present to register, generated once and kept
  with the other bootstrap secrets, and handed to the server through a config
  file in a volume both containers mount
- where the server is on the internal network, and whether it is up

Nothing here is needed (or does anything visible) when the profile is off.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request

from app import bootstrap

CONFIG_DIR = os.environ.get("SMSGATE_CONFIG_DIR", "/app/smsgate").strip() or "/app/smsgate"
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yml")
# The path the phone app talks to. The gateway forwards it to the server, so the
# phone only ever needs the gateway's own address.
MOBILE_PATH = "/api/mobile"


def internal_url() -> str:
    return (os.environ.get("SMSGATE_INTERNAL_URL") or "http://smsgate:3000").strip().rstrip("/")


def internal_api_url() -> str:
    """The server's third-party (send) API. Never exposed outside the Docker network."""
    return internal_url() + "/api/3rdparty/v1"


def private_token() -> str:
    return bootstrap.get("smsgate_private_token")


def write_config() -> bool:
    """Write the server's config file into the shared volume. Returns False when the
    volume isn't mounted (a setup without the bundled server), which is fine."""
    if not os.path.isdir(CONFIG_DIR):
        return False
    # json.dumps output is valid YAML for a plain string, and quotes it safely.
    text = "gateway:\n  mode: private\n  private_token: " + json.dumps(private_token()) + "\n"
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            if f.read() == text:
                return True
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)  # the server runs as a different user in its own container
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def is_running() -> bool:
    try:
        with urllib.request.urlopen(internal_url() + "/health", timeout=2) as resp:
            return 200 <= int(getattr(resp, "status", None) or resp.getcode()) < 300
    except Exception:
        return False
