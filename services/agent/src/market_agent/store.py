"""Encrypted SQLite storage for investigations, their progress events, and agent checkpoints.

Records are encrypted with AES-GCM using a key created in the state directory. This
protects stored visitor questions from casual inspection of the database files on a
shared workstation; anyone who can read the key file can read the data.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from uuid import UUID

import aiosqlite
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .schemas import Event, Investigation


class SecretLeak(RuntimeError):
    """A configured secret was about to be stored or returned."""


def load_key(path: Path) -> bytes:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)))
    key = base64.urlsafe_b64decode(path.read_bytes().strip())
    if len(key) != 32:
        raise ValueError("state encryption key must be 32 bytes")
    return key


class _Cipher:
    """AES-GCM for LangGraph's EncryptedSerializer."""

    def __init__(self, key: bytes):
        self.aead = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        nonce = os.urandom(12)
        return "aesgcm", nonce + self.aead.encrypt(nonce, plaintext, None)

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        if ciphername != "aesgcm":
            raise ValueError(f"unsupported checkpoint cipher {ciphername}")
        return self.aead.decrypt(ciphertext[:12], ciphertext[12:], None)


async def open_checkpointer(path: Path, key: bytes) -> tuple[AsyncSqliteSaver, aiosqlite.Connection]:
    connection = await aiosqlite.connect(path)
    serde = EncryptedSerializer(_Cipher(key), JsonPlusSerializer(pickle_fallback=False))
    saver = AsyncSqliteSaver(connection, serde=serde)
    await saver.setup()
    return saver, connection


class Store:
    def __init__(self, path: Path, key: bytes, secrets: tuple[str, ...] = ()):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.aead, self.secrets, self.lock = AESGCM(key), secrets, RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS investigations_v2 (id TEXT PRIMARY KEY, payload BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS events_v2 (
                id TEXT NOT NULL, sequence INTEGER NOT NULL, payload BLOB NOT NULL,
                PRIMARY KEY (id, sequence)
            );
            """
        )
        os.chmod(path, 0o600)

    def _seal(self, identifier: str, text: str) -> bytes:
        if any(secret in text for secret in self.secrets):
            raise SecretLeak("refusing to store a configured secret")
        nonce = os.urandom(12)
        return nonce + self.aead.encrypt(nonce, text.encode(), identifier.encode())

    def _open(self, identifier: str, blob: bytes) -> str:
        return self.aead.decrypt(blob[:12], blob[12:], identifier.encode()).decode()

    def save(self, investigation: Investigation) -> None:
        key = str(investigation.investigation_id)
        payload = self._seal(key, investigation.model_dump_json(exclude={"events"}))
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO investigations_v2(id, payload) VALUES(?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (key, payload),
            )

    def append(self, investigation_id: UUID, event: Event) -> None:
        key = str(investigation_id)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO events_v2(id, sequence, payload) VALUES(?, ?, ?)",
                (key, event.sequence, self._seal(key, event.model_dump_json())),
            )

    def get(self, investigation_id: UUID) -> Investigation | None:
        key = str(investigation_id)
        with self.lock:
            row = self.db.execute("SELECT payload FROM investigations_v2 WHERE id=?", (key,)).fetchone()
        return None if row is None else Investigation.model_validate_json(self._open(key, row[0]))

    def running(self) -> list[Investigation]:
        with self.lock:
            rows = self.db.execute("SELECT id, payload FROM investigations_v2").fetchall()
        records = (Investigation.model_validate_json(self._open(key, blob)) for key, blob in rows)
        return [item for item in records if item.status == "running"]

    def events(self, investigation_id: UUID, after: int = 0) -> list[Event]:
        key = str(investigation_id)
        with self.lock:
            rows = self.db.execute(
                "SELECT payload FROM events_v2 WHERE id=? AND sequence>? ORDER BY sequence", (key, after)
            ).fetchall()
        return [Event.model_validate(json.loads(self._open(key, row[0]))) for row in rows]

    def close(self) -> None:
        with self.lock:
            self.db.close()
