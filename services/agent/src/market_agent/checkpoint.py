from __future__ import annotations
import base64
import math
import os
from enum import Enum
from pathlib import Path
import aiosqlite
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Send, TimeoutPolicy
from pydantic import BaseModel


def _inert(value):
    if isinstance(value, BaseModel):
        return _inert(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return _inert(value.value)
    if isinstance(value, Send):
        # LangGraph persists a Send packet while a return-direct tool branch is
        # in flight. Preserve that framework type so a checkpoint can resume,
        # but rebuild it only from recursively inert data. Both Send and
        # TimeoutPolicy are in JsonPlusSerializer's strict safe-type allowlist.
        timeout = value.timeout
        if timeout is not None:
            timeout = TimeoutPolicy(
                run_timeout=_inert(timeout.run_timeout),
                idle_timeout=_inert(timeout.idle_timeout),
                refresh_on=_inert(timeout.refresh_on),
            )
        return Send(_inert(value.node), _inert(value.arg), timeout=timeout)
    if isinstance(value, dict):
        return (
            {key: _inert(item) for key, item in value.items()}
            if all(isinstance(key, str) for key in value)
            else (_ for _ in ()).throw(TypeError("checkpoint mappings require string keys"))
        )
    if isinstance(value, (list, tuple)):
        return [_inert(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("non-inert checkpoint values are forbidden")


class InertJsonSerializer(JsonPlusSerializer):
    def dumps_typed(self, obj):
        return super().dumps_typed(_inert(obj))


class CheckpointError(RuntimeError):
    pass


class CheckpointStore:
    """Own one encrypted SQLite saver for the service lifespan."""

    def __init__(self, database: Path, key_file: Path):
        self.database, self.key_file, self.connection, self.saver = database, key_file, None, None

    async def open(self) -> AsyncSqliteSaver:
        if self.saver is not None:
            return self.saver
        try:
            key = base64.urlsafe_b64decode(self.key_file.read_bytes().strip())
        except (OSError, ValueError) as exc:
            raise CheckpointError("checkpoint encryption key is unavailable") from exc
        if len(key) != 32:
            raise CheckpointError("checkpoint encryption key is invalid")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.database)
        serde = EncryptedSerializer.from_pycryptodome_aes(
            key=key, serde=InertJsonSerializer(pickle_fallback=False, allowed_msgpack_modules=None)
        )
        saver = AsyncSqliteSaver(connection, serde=serde)
        try:
            await saver.setup()
            os.chmod(self.database, 0o600)
        except BaseException:
            await connection.close()
            raise
        self.connection, self.saver = connection, saver
        return saver

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
        self.connection = self.saver = None

    async def final_values(
        self, thread_id: str, allowed: frozenset[str], *, turn_id: str | None = None
    ) -> dict:
        try:
            saver = await self.open()
            config = {"configurable": {"thread_id": thread_id}}
            items = (
                [await saver.aget_tuple(config)]
                if turn_id is None
                else [item async for item in saver.alist(config, limit=513)]
            )
        except Exception as exc:
            raise CheckpointError("checkpoint decryption failed") from exc
        if turn_id is not None:
            if len(items) > 512:
                raise CheckpointError("checkpoint history exceeds limit")
            items = [
                item
                for item in items
                if isinstance(item.metadata, dict)
                and item.metadata.get("source") == "loop"
                and isinstance(item.checkpoint.get("channel_values"), dict)
                and item.checkpoint["channel_values"].get("terminal")
                and item.checkpoint["channel_values"].get("turn_id") == turn_id
                and item.checkpoint["channel_values"].get("active_turn_id") == turn_id
            ]
            if len(items) != 1:
                raise CheckpointError(
                    "checkpoint turn is ambiguous" if items else "checkpoint is unavailable"
                )
        item = (
            items[0]
            if items and items[0] is not None
            else (_ for _ in ()).throw(CheckpointError("checkpoint is unavailable"))
        )
        values = item.checkpoint.get("channel_values")
        if not isinstance(values, dict):
            raise CheckpointError("checkpoint values are invalid")
        if any(not (name.startswith("branch:") or name.startswith("__")) for name in set(values) - allowed):
            raise CheckpointError("checkpoint fields are invalid")
        result = {name: values[name] for name in allowed if name in values}
        return (
            _inert(result)
            if result.get("terminal")
            else (_ for _ in ()).throw(CheckpointError("checkpoint is not terminal"))
        )
