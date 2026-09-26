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
