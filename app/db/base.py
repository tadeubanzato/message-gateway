"""
Repository interface shared by both backends (SQLite JSON-columns and MongoDB Atlas).

Documents are plain dicts. `_id` is always a string once returned from any
repository method (Mongo ObjectIds are stringified; SQLite uses its own
generated string ids) so calling code never branches on backend type.

Design note: this intentionally mirrors MongoDB's document/collection model
(find_one, insert_one, update_one, push-to-array semantics) rather than a
normalized relational schema, per the project's "boring, simple" bias — the
SQLite backend stores each document as a JSON blob in a single column and
does the query/update logic in Python. This trades some query power for a
much smaller amount of code to keep in sync across two backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class Repository(ABC):
    # ---- accounts ----
    @abstractmethod
    def find_account_by_email(self, email: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def find_account_by_user_key(self, user_key: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def find_account_by_id(self, account_id: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def find_account_by_active_token_hash(
        self, user_key: str, token_hash: str
    ) -> Optional[dict[str, Any]]:
        """Account with user_key AND an active (revoked_at is None) token matching token_hash."""
        ...

    @abstractmethod
    def insert_account(self, doc: dict[str, Any]) -> str:
        """Insert an account document, return its id as a string. Must enforce
        unique email and unique user_key (raise DuplicateKeyError subclass on conflict)."""
        ...

    @abstractmethod
    def list_accounts(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def update_account_fields(self, account_id: str, fields: dict[str, Any]) -> None:
        """Set top-level fields on an account document."""
        ...

    @abstractmethod
    def count_accounts(self) -> int:
        ...

    @abstractmethod
    def oldest_account_id(self) -> Optional[str]:
        """Id of the first-created account (the owner, for databases created
        before owners were tracked explicitly)."""
        ...

    @abstractmethod
    def set_password_hash(self, account_id: str, password_hash: str) -> None:
        ...

    @abstractmethod
    def push_token(self, account_id: str, token_doc: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def revoke_tokens(self, account_id: str, app_filter_fn) -> None:
        """
        app_filter_fn(token_dict) -> bool: revoke every currently-active token
        for which this returns True. Kept as a Python predicate (not a Mongo
        query dict) so both backends share the exact same revoke semantics.
        """
        ...

    @abstractmethod
    def rename_token_app(self, account_id: str, app_filter_fn, new_app: str) -> None:
        """
        Rename the currently-active token(s) matched by app_filter_fn (same predicate
        shape as revoke_tokens) to new_app. The token and its secret are unchanged;
        only the display name moves.
        """
        ...

    # ---- portal sessions ----
    @abstractmethod
    def create_session(self, session_doc: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def find_session(self, session_id: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        ...

    # ---- messages / attempts (P1 reliability work) ----
    @abstractmethod
    def insert_message(self, doc: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def list_messages(
        self, channel: Optional[str], status: Optional[str], limit: int,
        account_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Newest first. account_id, when given, restricts to that account's messages."""
        ...

    @abstractmethod
    def get_message(self, message_id: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def update_message_fields(self, message_id: str, fields: dict[str, Any]) -> None:
        """Merge fields into a message record (may include "status")."""
        ...

    @abstractmethod
    def update_message_status(self, message_id: str, status: str) -> None:
        ...

    @abstractmethod
    def insert_attempt(self, doc: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def list_attempts(self, message_id: str) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def purge_old_messages(self, older_than_seconds: int) -> int:
        """Delete messages/attempts older than the given age. Returns count deleted.
        Mongo backend prefers a TTL index (this becomes a no-op there); SQLite
        backend must actively delete on a schedule since it has no TTL indexes."""
        ...

    # ---- settings (values are already encrypted by app.services.secret_store) ----
    @abstractmethod
    def list_settings(self) -> dict[str, dict[str, Any]]:
        """All stored settings as {name: {"value_enc": str, "updated_at": float}}."""
        ...

    @abstractmethod
    def set_setting(self, name: str, value_enc: str) -> None:
        ...

    @abstractmethod
    def delete_setting(self, name: str) -> None:
        ...

    # ---- email templates (saved from the web app's template builder) ----
    @abstractmethod
    def list_email_templates(self) -> list[dict[str, Any]]:
        """Every saved template: {"_id": "tpl_...", "name", "html", "txt", "created_at", "updated_at"}."""
        ...

    @abstractmethod
    def get_email_template(self, template_id: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def find_email_template_by_name(self, name: str) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    def save_email_template(self, doc: dict[str, Any]) -> None:
        """Insert or replace the template with doc["_id"]. Names are unique
        (raise DuplicateTemplateNameError on a clash with a different template)."""
        ...

    @abstractmethod
    def delete_email_template(self, template_id: str) -> bool:
        ...

    # ---- move data to another database (Settings > Database) ----
    @abstractmethod
    def export_data(self) -> dict[str, list[dict[str, Any]]]:
        """Everything needed to recreate this gateway elsewhere: accounts, portal_sessions,
        messages, attempts, settings, email_templates. Ids are preserved; message/attempt times are exported
        as epoch seconds in "_created_at". Setting values stay encrypted."""
        ...

    @abstractmethod
    def import_data(self, data: dict[str, list[dict[str, Any]]]) -> None:
        """Load data produced by export_data() from either backend."""
        ...

    @abstractmethod
    def counts(self) -> dict[str, int]:
        ...

    # ---- lifecycle ----
    @abstractmethod
    def ensure_indexes(self) -> None:
        ...

    @abstractmethod
    def ping(self) -> bool:
        ...


class DuplicateEmailError(Exception):
    pass


class DuplicateUserKeyError(Exception):
    pass


class DuplicateTemplateNameError(Exception):
    pass
