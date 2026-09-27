"""Shared settings helper used across provider implementations.

Lookup order: environment variable (advanced override), then the encrypted
settings stored in the database by the onboarding flow, then the default.
"""

from __future__ import annotations

import os
from typing import Optional


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = (os.environ.get(name) or "").strip()
    if val:
        return val

    from app.services import secret_store

    stored = secret_store.get_setting(name)
    if stored and stored.strip():
        return stored.strip()
    return default


def public_base_url(request) -> str:
    """The address this gateway is reached at, for URLs shown to the user (API
    examples, the MCP endpoint) or handed to an AI agent to connect with.

    Defaults to the address of the current request - this already gets it right
    for the common cases without any setup: localhost in dev, a server's bare IP,
    or a domain pointed straight at the container. Set PUBLIC_BASE_URL (Settings,
    or the environment for an advanced override) only when that guess is wrong -
    typically a reverse proxy or tunnel (nginx, Cloudflare Tunnel, ngrok, a VPN)
    that doesn't forward the original Host header, so the gateway only ever sees
    its own internal address."""
    override = (get_env("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if override:
        return override
    return str(request.base_url).rstrip("/")
