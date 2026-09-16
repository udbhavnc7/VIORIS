"""
Encrypted-at-rest token vault (Phase 6, Prompt 6.x).

Every OAuth access/refresh token lives here — encrypted with Fernet and keyed
by `(connector, identity)` in a SQLite store. Raw tokens are never returned to
callers: the vault exposes only the decrypted entry inside the connector
process, and nothing here ever writes a token to a log.

Key handling: the key is read from the `VIORUS_TOKEN_KEY` env var (base64) or
generated once and stored with narrow file permissions. Rotating the key
invalidates the store — the operator re-connects accounts (acceptable for a
single-user, $0 £ local deployment).
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .base import ConnectionMissingError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_entries (
    connector    TEXT NOT NULL,
    identity     TEXT NOT NULL,
    encrypted    TEXT NOT NULL,
    scopes_json  TEXT NOT NULL,
    expired      INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (connector, identity)
);
"""


class TokenVault:
    """Thread-safe, encrypted-at-rest store of provider sessions.

    Entries are dicts with at least the provider's raw token fields
    (access_token, refresh_token, …) plus a stable `account_id`.
    """

    def __init__(self, db_path: str | Path, key: str | bytes | None = None,
                 key_file: str | Path | None = None) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_key(key, key_file))
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _load_key(self, key: str | bytes | None, key_file: str | Path | None) -> bytes:
        if key is not None:
            raw = key if isinstance(key, bytes) else key.encode()
        else:
            env = os.getenv("VIORUS_TOKEN_KEY")
            if env:
                raw = base64.b64decode(env)
            elif key_file is not None and Path(key_file).exists():
                raw = Path(key_file).read_bytes().strip()
            else:
                raw = self._write_default_key(key_file)
        try:
            return bytes(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 - still normalize to bytes
            return bytes(raw)

    def _write_default_key(self, key_file) -> bytes:
        raw = Fernet.generate_key()
        if key_file is not None:
            Path(key_file).parent.mkdir(parents=True, exist_ok=True)
            Path(key_file).write_bytes(raw)
            try:
                os.chmod(Path(key_file), 0o600)
            except OSError:
                pass
        return raw

    # ── storage ────────────────────────────────────────────────────────────
    def save(self, connector: str, identity: str, raw: dict,
             scopes: list | None = None) -> None:
        """Encrypt + upsert a provider token blob. `raw` is provider JSON; the
        vault preserves it verbatim plus a stable account_id."""
        blob = dict(raw)
        blob["account_id"] = blob.get("account_id") or identity
        encrypted = self._fernet.encrypt(json.dumps(blob).encode())
        scopes_json = json.dumps(scopes or blob.get("scope", []))
        with self._lock:
            self._conn.execute(
                "INSERT INTO token_entries (connector, identity, encrypted, scopes_json, expired) "
                "VALUES (?, ?, ?, ?, 0) "
                "ON CONFLICT(connector, identity) DO UPDATE SET "
                "encrypted=excluded.encrypted, scopes_json=excluded.scopes_json, "
                "expired=0, updated_at=datetime('now')",
                (connector, identity, encrypted, scopes_json),
            )
            self._conn.commit()

    def load(self, connector: str, identity: str) -> dict | None:
        """Decrypt + return a session entry, or None. Never raise on a
        tampered/corrupt row — that produces a failed connection to re-do."""
        with self._lock:
            row = self._conn.execute(
                "SELECT encrypted, scopes_json, expired FROM token_entries "
                "WHERE connector=? AND identity=?",
                (connector, identity),
            ).fetchone()
        if row is None:
            return None
        try:
            blob = json.loads(self._fernet.decrypt(row[0]))
        except InvalidToken:
            return None
        blob["scopes"] = json.loads(row[1] or "[]")
        blob["expired"] = bool(row[2])
        return blob

    def mark_expired(self, connector: str, identity: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE token_entries SET expired=1 WHERE connector=? AND identity=?",
                (connector, identity),
            )
            self._conn.commit()

    def delete(self, connector: str, identity: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM token_entries WHERE connector=? AND identity=?",
                (connector, identity),
            )
            self._conn.commit()

    def list(self, connector: str) -> list[dict]:
        """Non-secret connection summaries (no tokens) for the UI panel."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT identity, scopes_json, expired FROM token_entries WHERE connector=?",
                (connector,),
            ).fetchall()
        return [
            {"account": r[0], "scopes": json.loads(r[1] or "[]"), "expired": bool(r[2])}
            for r in rows
        ]

    def require_entry(self, connector: str, identity: str) -> dict:
        entry = self.load(connector, identity)
        if entry is None:
            raise ConnectionMissingError(f"no connected account '{identity}' for {connector}")
        return entry