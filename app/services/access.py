"""
Who may do what in the portal.

- The first account, created during setup, is the ADMINISTRATOR (stored internally
  as the "owner"). Only the administrator can change settings and channels.
- Sign-ups are closed once an administrator exists, unless the administrator turns
  them on in Settings. Databases created before this was tracked explicitly treat
  the oldest account as the administrator.
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


def ensure_roles() -> None:
    """Make the administrator visible in the database: every account gets role "admin" or
    "member". New accounts get it when created; this fills in accounts that predate it and
    keeps the label in step with who the administrator is. Safe to run repeatedly."""
    admin_id = owner_id()
    if not admin_id:
        return
    repo = get_repository()
    for acct in repo.list_accounts():
        role = "admin" if str(acct.get("_id")) == admin_id else "member"
        if acct.get("role") != role:
            repo.update_account_fields(str(acct["_id"]), {"role": role})


def has_accounts() -> bool:
    return get_repository().count_accounts() > 0


def signups_open() -> bool:
    if not has_accounts():
        return True  # the very first account (the administrator) can always be created
    return (secret_store.get_setting(SIGNUPS_KEY) or "0") == "1"
