"""
MongoDB Atlas repository — ports the old codebase's db/mongo.py + auth/api_key.py +
gateway_portal.py query logic behind the shared Repository interface.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from app.db.base import DuplicateEmailError, DuplicateUserKeyError, Repository

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
    def __init__(self, uri: str, db_name: str) -> None:
        if not uri:
            raise SystemExit("MongoDB URI is missing (required for the atlas backend)")
        if not db_name:
            raise SystemExit("MongoDB database name is missing (required for the atlas backend)")

        self._client = MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=5000,
            retryWrites=True,
        )
        self._db = self._client[db_name]

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

    def list_accounts(self) -> list[dict[str, Any]]:
        return [_stringify_id(d) for d in self._db.accounts.find({})]

    def update_account_fields(self, account_id: str, fields: dict[str, Any]) -> None:
        self._db.accounts.update_one({"_id": _to_object_id(account_id)}, {"$set": fields})

    def count_accounts(self) -> int:
        return int(self._db.accounts.count_documents({}))

    def oldest_account_id(self) -> Optional[str]:
        doc = self._db.accounts.find_one({}, sort=[("_id", 1)], projection={"_id": 1})
        return str(doc["_id"]) if doc else None

    def set_password_hash(self, account_id: str, password_hash: str) -> None:
        self._db.accounts.update_one(
            {"_id": _to_object_id(account_id)}, {"$set": {"password_hash": password_hash}}
        )

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

    def rename_token_app(self, account_id: str, app_filter_fn, new_app: str) -> None:
        acct = self.find_account_by_id(account_id)
        if not acct:
            return
        svc = (acct.get("services") or {}).get("message-gateway") or {}
        tokens = svc.get("tokens") or []
        token_ids_to_rename = [
            t.get("token_id")
            for t in tokens
            if isinstance(t, dict) and t.get("revoked_at") is None and app_filter_fn(t)
        ]
        if not token_ids_to_rename:
            return
        self._db.accounts.update_one(
            {"_id": _to_object_id(account_id)},
            {"$set": {"services.message-gateway.tokens.$[t].app": new_app}},
            array_filters=[{"t.token_id": {"$in": token_ids_to_rename}}],
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
        self, channel: Optional[str], status: Optional[str], limit: int,
        account_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        q: dict[str, Any] = {}
        if channel:
            q["channel"] = channel
        if status:
            q["status"] = status
        if account_id is not None:
            q["account_id"] = account_id
        cursor = self._db.messages.find(q).sort("created_at", -1).limit(limit)
        return [_stringify_id(d) for d in cursor]

    def get_message(self, message_id: str) -> Optional[dict[str, Any]]:
        return _stringify_id(self._db.messages.find_one({"message_id": message_id}))

    def update_message_fields(self, message_id: str, fields: dict[str, Any]) -> None:
        self._db.messages.update_one({"message_id": message_id}, {"$set": fields})

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

    # ---- settings ----
    def list_settings(self) -> dict[str, dict[str, Any]]:
        return {d["_id"]: d for d in self._db.settings.find({})}

    def set_setting(self, name: str, value_enc: str) -> None:
        self._db.settings.update_one(
            {"_id": name},
            {"$set": {"value_enc": value_enc, "updated_at": time.time()}},
            upsert=True,
        )

    def delete_setting(self, name: str) -> None:
        self._db.settings.delete_one({"_id": name})

    # ---- move data to another database ----
    @staticmethod
    def _epoch(d: dict[str, Any]) -> float:
        c = d.get("created_at")
        if isinstance(c, datetime):
            return (c if c.tzinfo else c.replace(tzinfo=timezone.utc)).timestamp()
        return time.time()

    def export_data(self) -> dict[str, list[dict[str, Any]]]:
        def without(d: dict[str, Any], *keys: str) -> dict[str, Any]:
            return {k: v for k, v in d.items() if k not in keys}

        return {
            "accounts": [{**d, "_id": str(d["_id"])} for d in self._db.accounts.find({})],
            "portal_sessions": [without(d, "_id") for d in self._db.portal_sessions.find({})],
            "messages": [{**without(d, "_id", "created_at"), "_created_at": self._epoch(d)} for d in self._db.messages.find({})],
            "attempts": [{**without(d, "_id", "created_at"), "_id": str(d["_id"]), "_created_at": self._epoch(d)}
                         for d in self._db.attempts.find({})],
            "settings": [{**d, "_id": str(d["_id"])} for d in self._db.settings.find({})],
        }

    def import_data(self, data: dict[str, list[dict[str, Any]]]) -> None:
        for d in data.get("accounts", []):
            doc = {**d, "_id": _to_object_id(str(d["_id"]))}  # keeps ids identical; ObjectId-shaped ids stay ObjectIds
            self._db.accounts.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        for d in data.get("portal_sessions", []):
            self._db.portal_sessions.replace_one({"session_id": d["session_id"]}, dict(d), upsert=True)
        for d in data.get("messages", []):
            doc = dict(d); doc.pop("_id", None)
            doc["created_at"] = datetime.fromtimestamp(float(doc.pop("_created_at", None) or time.time()), tz=timezone.utc)
            self._db.messages.replace_one({"message_id": doc["message_id"]}, doc, upsert=True)
        attempts = []
        for d in data.get("attempts", []):
            doc = dict(d); doc.pop("_id", None)
            doc["created_at"] = datetime.fromtimestamp(float(doc.pop("_created_at", None) or time.time()), tz=timezone.utc)
            attempts.append(doc)
        if attempts:
            self._db.attempts.insert_many(attempts)
        for d in data.get("settings", []):
            doc = dict(d); name = doc.pop("_id")
            self._db.settings.replace_one({"_id": name}, {"_id": name, **doc}, upsert=True)

    def counts(self) -> dict[str, int]:
        return {t: int(self._db[t].count_documents({}))
                for t in ("accounts", "portal_sessions", "messages", "attempts", "settings")}

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


def test_connection(uri: str, db_name: str) -> tuple[bool, str]:
    """Try to reach an Atlas cluster with the given URI. Returns (ok, message).
    Used by onboarding before anything is saved; error text never echoes the URI."""
    if not (uri or "").strip():
        return False, "Enter a MongoDB connection string."
    if not (db_name or "").strip():
        return False, "Enter a database name."
    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000)
        client.admin.command("ping")
        client[db_name].list_collection_names()
        return True, "Connected."
    except Exception as e:  # noqa: BLE001 - surface a safe, short reason
        name = type(e).__name__
        if "Authentication" in name or "OperationFailure" in name:
            return False, "Authentication failed: check the username, password and database permissions."
        if "ServerSelection" in name or "Timeout" in name or "Configuration" in name:
            return False, "Could not reach the cluster: check the connection string and that your IP is allowed in Atlas Network Access."
        return False, f"Connection failed ({name})."
    finally:
        if client is not None:
            client.close()
