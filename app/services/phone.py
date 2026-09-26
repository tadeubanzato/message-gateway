"""Phone number handling shared by the MCP tools and the default-number setting."""

from __future__ import annotations

import re


def normalize_phone(number: str) -> str:
    """Normalize a phone number typed any way ('+1 (555) 123-4567', '0044 7911...')
    to +15551234567. The country code is required; we never guess one.
    Raises ValueError with a message safe to show the user."""
    raw = (number or "").strip()
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+") or not re.fullmatch(r"\+\d{8,15}", digits):
        raise ValueError(
            f"Phone numbers need the country code, for example +15551234567 (got {raw!r})."
        )
    return digits
