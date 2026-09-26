"""
SQLite repository — JSON-in-columns approach.

Each "collection" is a table with (id TEXT PRIMARY KEY, doc TEXT) where `doc`
is a JSON-serialized document. Queries that would be a Mongo filter are done
by loading candidate rows and filtering in Python — fine at this project's
scale (personal/small-team messaging volume), and it keeps the document shape
byte-for-byte compatible with the Atlas backend rather than requiring two
divergent data models.

Uses Python's stdlib sqlite3 with a per-call connection (SQLite handles this
fine and it avoids cross-thread connection-sharing issues under FastAPI's
thread pool for sync routes).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from app.db.base import DuplicateEmailError, DuplicateUserKeyError, Repository

_DB_PATH = os.environ.get("SQLITE_PATH", "/app/data/gateway.db").strip() or "/app/data/gateway.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    user_key TEXT UNIQUE NOT NULL,
    doc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_sessions (
    session_id TEXT PRIMARY KEY,
    doc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    channel TEXT,
    status TEXT,
    doc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    doc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_message_id ON attempts(message_id);
"""


def _now() -> float:
    return time.time()


def _json_default(o: Any) -> Any:
    # datetime objects get serialized as ISO strings elsewhere before reaching
    # this layer; this is a safety net for anything else JSON can't handle.
    return str(o)


class SqliteRepository(Repository):
    def __init__(self) -> None:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    # ---- accounts ----
    def find_account_by_email(self, email: str) -> Optional[dict[str, Any]]:
        row = self._conn().execute(
            "SELECT doc FROM accounts WHERE email = ?", (email,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def find_account_by_user_key(self, user_key: str) -> Optional[dict[str, Any]]:
        row = self._conn().execute(
            "SELECT doc FROM accounts WHERE user_key = ?", (user_key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def find_account_by_id(self, account_id: str) -> Optional[dict[str, Any]]:
        row = self._conn().execute(
            "SELECT doc FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def find_account_by_active_token_hash(
        self, user_key: str, token_hash: str
    ) -> Optional[dict[str, Any]]:
        acct = self.find_account_by_user_key(user_key)
        if not acct:
            return None
        tokens = (
            ((acct.get("services") or {}).get("message-gateway") or {}).get("tokens")
            or []
        )
        for t in tokens:
            if (
                isinstance(t, dict)
                and t.get("token_hash") == token_hash
                and t.get("revoked_at") is None
            ):
                return acct
        return None

    def insert_account(self, doc: dict[str, Any]) -> str:
        account_id = doc.get("_id") or str(uuid.uuid4())
        doc = {**doc, "_id": account_id}
        try:
            self._conn().execute(
                "INSERT INTO accounts (id, email, user_key, doc) VALUES (?, ?, ?, ?)",
                (account_id, doc["email"], doc["user_key"], json.dumps(doc, default=_json_default)),
            )
            self._conn().commit()
        except sqlite3.IntegrityError as e:
            msg = str(e)
            if "accounts.email" in msg:
                raise DuplicateEmailError() from e
            if "accounts.user_key" in msg:
                raise DuplicateUserKeyError() from e
            raise
        return account_id

    def push_token(self, account_id: str, token_doc: dict[str, Any]) -> None:
        acct = self.find_account_by_id(account_id)
        if not acct:
            return
        services = acct.setdefault("services", {})
        svc = services.setdefault("message-gateway", {"service": "message-gateway", "tokens": []})
        if not isinstance(svc.get("tokens"), list):
            svc["tokens"] = []
        svc["tokens"].append(token_doc)
        self._conn().execute(
            "UPDATE accounts SET doc = ? WHERE id = ?",
            (json.dumps(acct, default=_json_default), account_id),
        )
        self._conn().commit()

    def revoke_tokens(self, account_id: str, app_filter_fn) -> None:
        acct = self.find_account_by_id(account_id)
        if not acct:
            return
        svc = ((acct.get("services") or {}).get("message-gateway") or {})
        tokens = svc.get("tokens") or []
        changed = False
        for t in tokens:
            if not isinstance(t, dict):
                continue
            if t.get("revoked_at") is None and app_filter_fn(t):
                t["revoked_at"] = time.time()
                changed = True
        if changed:
            self._conn().execute(
                "UPDATE accounts SET doc = ? WHERE id = ?",
                (json.dumps(acct, default=_json_default), account_id),
            )
            self._conn().commit()

    # ---- portal sessions ----
    def create_session(self, session_doc: dict[str, Any]) -> None:
        self._conn().execute(
            "INSERT INTO portal_sessions (session_id, doc) VALUES (?, ?)",
            (session_doc["session_id"], json.dumps(session_doc, default=_json_default)),
        )
        self._conn().commit()

    def find_session(self, session_id: str) -> Optional[dict[str, Any]]:
        row = self._conn().execute(
            "SELECT doc FROM portal_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def delete_session(self, session_id: str) -> None:
        self._conn().execute("DELETE FROM portal_sessions WHERE session_id = ?", (session_id,))
        self._conn().commit()

    # ---- messages / attempts ----
    def insert_message(self, doc: dict[str, Any]) -> None:
        self._conn().execute(
            "INSERT INTO messages (message_id, created_at, channel, status, doc) VALUES (?, ?, ?, ?, ?)",
            (
                doc["message_id"],
                _now(),
                doc.get("channel"),
                doc.get("status", "queued"),
                json.dumps(doc, default=_json_default),
            ),
        )
        self._conn().commit()

    def list_messages(
        self, channel: Optional[str], status: Optional[str], limit: int
    ) -> list[dict[str, Any]]:
        query = "SELECT doc FROM messages WHERE 1=1"
        params: list[Any] = []
        if channel:
            query += " AND channel = ?"
            params.append(channel)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn().execute(query, params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def update_message_status(self, message_id: str, status: str) -> None:
        row = self._conn().execute(
            "SELECT doc FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if not row:
            return
        doc = json.loads(row[0])
        doc["status"] = status
        self._conn().execute(
            "UPDATE messages SET status = ?, doc = ? WHERE message_id = ?",
            (status, json.dumps(doc, default=_json_default), message_id),
        )
        self._conn().commit()

    def insert_attempt(self, doc: dict[str, Any]) -> None:
        attempt_id = doc.get("_id") or str(uuid.uuid4())
        doc = {**doc, "_id": attempt_id}
        self._conn().execute(
            "INSERT INTO attempts (id, message_id, created_at, doc) VALUES (?, ?, ?, ?)",
            (attempt_id, doc["message_id"], _now(), json.dumps(doc, default=_json_default)),
        )
        self._conn().commit()

    def list_attempts(self, message_id: str) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT doc FROM attempts WHERE message_id = ? ORDER BY created_at DESC",
            (message_id,),
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def purge_old_messages(self, older_than_seconds: int) -> int:
        cutoff = _now() - older_than_seconds
        old_ids = [
            r[0]
            for r in self._conn()
            .execute("SELECT message_id FROM messages WHERE created_at < ?", (cutoff,))
            .fetchall()
        ]
        if not old_ids:
            return 0
        placeholders = ",".join("?" for _ in old_ids)
        self._conn().execute(f"DELETE FROM attempts WHERE message_id IN ({placeholders})", old_ids)
        self._conn().execute(f"DELETE FROM messages WHERE message_id IN ({placeholders})", old_ids)
        self._conn().commit()
        return len(old_ids)

    # ---- lifecycle ----
    def ensure_indexes(self) -> None:
        # Schema + indexes already created in __init__; nothing extra needed.
        pass

    def ping(self) -> bool:
        try:
            self._conn().execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False
