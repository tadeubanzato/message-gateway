"""
Who may do what in the portal.

- The first account created is the OWNER. Only the owner can change settings.
- Sign-ups are closed once an owner exists, unless the owner turns them on in
  Settings. Databases created before owners were tracked treat the oldest
  account as the owner.
"""

from __future__ import annotations

from typing import Any, Optional

from app.db import get_repository
from app.services import secret_store

OWNER_KEY = "OWNER_ACCOUNT_ID"
SIGNUPS_KEY = "ALLOW_SIGNUPS"


def owner_id() -> Optional[str]:
    stored = secret_store.get_setting(OWNER_KEY)
    if stored:
        return stored
    oldest = get_repository().oldest_account_id()
    if oldest:
        secret_store.set_setting(OWNER_KEY, oldest)
    return oldest


def claim_owner(account_id: str) -> None:
    if not secret_store.get_setting(OWNER_KEY):
        secret_store.set_setting(OWNER_KEY, account_id)


def is_owner(account: dict[str, Any]) -> bool:
    return str(account.get("_id")) == owner_id()


def has_accounts() -> bool:
    return get_repository().count_accounts() > 0


def signups_open() -> bool:
    if not has_accounts():
        return True  # the very first account (the owner) can always be created
    return (secret_store.get_setting(SIGNUPS_KEY) or "0") == "1"
