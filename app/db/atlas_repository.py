"""
MongoDB Atlas repository — ports the old codebase's db/mongo.py + auth/api_key.py +
gateway_portal.py query logic behind the shared Repository interface.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from app.db.base import DuplicateEmailError, DuplicateUserKeyError, Repository

MONGODB_URI = os.environ.get("MONGODB_URI", "").strip()
MONGODB_DB = os.environ.get("MONGODB_DB", "").strip()

_NINETY_DAYS_SECONDS = 90 * 24 * 3600


def _stringify_id(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _to_object_id(id_str: str):
    try:
        return ObjectId(id_str)
    except Exception:
        return id_str  # already a plain id (shouldn't normally happen)


class AtlasRepository(Repository):
    def __init__(self) -> None:
        if not MONGODB_URI:
            raise SystemExit("MONGODB_URI is missing (required when DB_BACKEND=atlas)")
        if not MONGODB_DB:
            raise SystemExit("MONGODB_DB is missing (required when DB_BACKEND=atlas)")

        self._client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=5000,
            retryWrites=True,
        )
        self._db = self._client[MONGODB_DB]

    # ---- accounts ----
    def find_account_by_email(self, email: str) -> Optional[dict[str, Any]]:
        return _stringify_id(self._db.accounts.find_one({"email": email}))

    def find_account_by_user_key(self, user_key: str) -> Optional[dict[str, Any]]:
        return _stringify_id(self._db.accounts.find_one({"user_key": user_key}))

    def find_account_by_id(self, account_id: str) -> Optional[dict[str, Any]]:
        return _stringify_id(self._db.accounts.find_one({"_id": _to_object_id(account_id)}))

    def find_account_by_active_token_hash(
        self, user_key: str, token_hash: str
    ) -> Optional[dict[str, Any]]:
        q = {
            "user_key": user_key,
            "services.message-gateway.tokens": {
                "$elemMatch": {"token_hash": token_hash, "revoked_at": None}
            },
        }
        return _stringify_id(self._db.accounts.find_one(q))

    def insert_account(self, doc: dict[str, Any]) -> str:
        doc = dict(doc)
        doc.pop("_id", None)
        try:
            result = self._db.accounts.insert_one(doc)
        except DuplicateKeyError as e:
            msg = str(e)
            if "email" in msg:
                raise DuplicateEmailError() from e
            if "user_key" in msg:
                raise DuplicateUserKeyError() from e
            raise
        return str(result.inserted_id)

    def push_token(self, account_id: str, token_doc: dict[str, Any]) -> None:
        self._db.accounts.update_one(
            {"_id": _to_object_id(account_id)},
            {"$push": {"services.message-gateway.tokens": token_doc}},
        )

    def revoke_tokens(self, account_id: str, app_filter_fn) -> None:
        acct = self.find_account_by_id(account_id)
        if not acct:
            return
        svc = (acct.get("services") or {}).get("message-gateway") or {}
        tokens = svc.get("tokens") or []
        now = datetime.now(timezone.utc)
        token_ids_to_revoke = [
            t.get("token_id")
            for t in tokens
            if isinstance(t, dict) and t.get("revoked_at") is None and app_filter_fn(t)
        ]
        if not token_ids_to_revoke:
            return
        self._db.accounts.update_one(
            {"_id": _to_object_id(account_id)},
            {"$set": {"services.message-gateway.tokens.$[t].revoked_at": now}},
            array_filters=[{"t.token_id": {"$in": token_ids_to_revoke}}],
        )

    # ---- portal sessions ----
    def create_session(self, session_doc: dict[str, Any]) -> None:
        self._db.portal_sessions.insert_one(session_doc)

    def find_session(self, session_id: str) -> Optional[dict[str, Any]]:
        return _stringify_id(self._db.portal_sessions.find_one({"session_id": session_id}))

    def delete_session(self, session_id: str) -> None:
        self._db.portal_sessions.delete_one({"session_id": session_id})

    # ---- messages / attempts ----
    def insert_message(self, doc: dict[str, Any]) -> None:
        doc = dict(doc)
        doc.setdefault("created_at", datetime.now(timezone.utc))
        self._db.messages.insert_one(doc)

    def list_messages(
        self, channel: Optional[str], status: Optional[str], limit: int
    ) -> list[dict[str, Any]]:
        q: dict[str, Any] = {}
        if channel:
            q["channel"] = channel
        if status:
            q["status"] = status
        cursor = self._db.messages.find(q).sort("created_at", -1).limit(limit)
        return [_stringify_id(d) for d in cursor]

    def update_message_status(self, message_id: str, status: str) -> None:
        self._db.messages.update_one({"message_id": message_id}, {"$set": {"status": status}})

    def insert_attempt(self, doc: dict[str, Any]) -> None:
        doc = dict(doc)
        doc.setdefault("created_at", datetime.now(timezone.utc))
        self._db.attempts.insert_one(doc)

    def list_attempts(self, message_id: str) -> list[dict[str, Any]]:
        cursor = self._db.attempts.find({"message_id": message_id}).sort("created_at", -1)
        return [_stringify_id(d) for d in cursor]

    def purge_old_messages(self, older_than_seconds: int) -> int:
        # No-op by design: Atlas backend relies on the TTL index created in
        # ensure_indexes() (expireAfterSeconds) for automatic cleanup, rather
        # than an app-triggered delete. Returns 0 to signal "handled by DB."
        return 0

    # ---- lifecycle ----
    def ensure_indexes(self) -> None:
        accounts = self._db.accounts
        accounts.create_index("email", unique=True, name="uniq_email")
        accounts.create_index("user_key", unique=True, name="uniq_user_key")

        sessions = self._db.portal_sessions
        sessions.create_index("session_id", unique=True, name="uniq_session_id")
        sessions.create_index("expires_at", expireAfterSeconds=0, name="ttl_sessions_expires_at")

        msgs = self._db.messages
        msgs.create_index("message_id", unique=True, name="uniq_message_id")
        msgs.create_index(
            "created_at", expireAfterSeconds=_NINETY_DAYS_SECONDS, name="ttl_messages_90d"
        )
        msgs.create_index("channel", name="idx_channel")

        atts = self._db.attempts
        atts.create_index("message_id", name="idx_message_id")
        atts.create_index(
            "created_at", expireAfterSeconds=_NINETY_DAYS_SECONDS, name="ttl_attempts_90d"
        )

    def ping(self) -> bool:
        try:
            self._db.command("ping")
            return True
        except Exception:
            return False
