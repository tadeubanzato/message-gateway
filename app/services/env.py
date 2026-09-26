"""Tiny shared env-var helper used across provider implementations."""

from __future__ import annotations

import os
from typing import Optional


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name, default)
    if val is None:
        return default
    val = val.strip()
    return val if val else default
