"""Small encrypted SQLite investigation store."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .schemas import InvestigationRecord


class StateStore:
    def __init__(self, database: Path, key_file: Path):
        self.database = database
        self.key_file = key_file
        self._lock = RLock()
        database.parent.mkdir(parents=True, exist_ok=True)
        self._key = self._load_key()
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS investigations (id TEXT PRIMARY KEY, payload BLOB NOT NULL, updated TEXT NOT NULL)"
        )
        self._db.commit()
        try:
            os.chmod(database, 0o600)
        except OSError:
            pass

    def _load_key(self) -> bytes:
        if self.key_file.exists():
            raw = self.key_file.read_bytes().strip()
            key = base64.urlsafe_b64decode(raw)
            if len(key) != 32:
                raise ValueError("invalid state encryption key")
            return key
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        key = AESGCM.generate_key(bit_length=256)
        encoded = base64.urlsafe_b64encode(key)
        descriptor = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return key

    def _encrypt(self, identifier: str, payload: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key).encrypt(nonce, payload, identifier.encode())

    def _decrypt(self, identifier: str, payload: bytes) -> bytes:
        return AESGCM(self._key).decrypt(payload[:12], payload[12:], identifier.encode())

    def save(self, record: InvestigationRecord, *, create: bool = False) -> bool:
        identifier = str(record.investigation_id); canonical = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode(); encrypted = self._encrypt(identifier, canonical)
        try:
            with self._lock, self._db:
                self._db.execute("INSERT INTO investigations(id,payload,updated) VALUES(?,?,?)" + ("" if create else " ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated=excluded.updated"), (identifier, encrypted, record.updated_at.isoformat()))
        except sqlite3.IntegrityError: return False
        return True

    def get(self, identifier: UUID | str) -> InvestigationRecord | None:
        key = str(identifier)
        with self._lock:
            row = self._db.execute("SELECT payload FROM investigations WHERE id=?", (key,)).fetchone()
        if row is None: return None
        return InvestigationRecord.model_validate_json(self._decrypt(key, row[0]))

    def close(self) -> None:
        with self._lock:
            self._db.close()
