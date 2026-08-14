"""JetStream KV-backed session and memory storage."""

from __future__ import annotations

import json

from nats.js.errors import (
    BucketNotFoundError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)


class MemoryStore:
    """Three-layer memory: short-lived sessions, agent memory, shared memory."""

    def __init__(self, session_kv, agent_kv, shared_kv, agent: str):
        self.sess = session_kv
        self.mem = agent_kv
        self.shared = shared_kv
        self.agent = agent

    @classmethod
    async def create(cls, js, agent: str, session_ttl_seconds: int = 86400) -> MemoryStore:
        """Open or create all KV buckets needed by an agent."""
        session_kv = await cls._open_or_create(
            js, f"A2AMESH_SESS_{agent}", ttl=session_ttl_seconds
        )
        agent_kv = await cls._open_or_create(js, f"A2AMESH_MEM_{agent}")
        shared_kv = await cls._open_or_create(js, "A2AMESH_MEM_SHARED")
        return cls(session_kv, agent_kv, shared_kv, agent)

    @staticmethod
    async def _open_or_create(js, bucket: str, ttl: int | None = None):
        try:
            return await js.key_value(bucket)
        except BucketNotFoundError:
            try:
                return await js.create_key_value(bucket=bucket, ttl=ttl)
            except Exception:
                # Another peer may have created the shared bucket concurrently.
                return await js.key_value(bucket)

    async def session_append(self, session_id: str, message: dict) -> None:
        """Append atomically, retrying on concurrent KV revisions."""
        for _ in range(8):
            try:
                entry = await self.sess.get(session_id)
            except KeyNotFoundError:
                payload = json.dumps([message], ensure_ascii=False).encode()
                try:
                    await self.sess.create(session_id, payload)
                    return
                except KeyWrongLastSequenceError:
                    continue

            history = json.loads(entry.value)
            history.append(message)
            payload = json.dumps(history, ensure_ascii=False).encode()
            try:
                await self.sess.update(session_id, payload, last=entry.revision)
                return
            except KeyWrongLastSequenceError:
                continue
        raise RuntimeError(f"session update conflict: {session_id}")

    async def session_get(self, session_id: str) -> list[dict]:
        try:
            entry = await self.sess.get(session_id)
        except KeyNotFoundError:
            return []
        return json.loads(entry.value)

    async def session_close(self, session_id: str) -> None:
        await self.sess.delete(session_id)

    @staticmethod
    async def _get_text(bucket, key: str) -> str | None:
        try:
            entry = await bucket.get(key)
        except KeyNotFoundError:
            return None
        return entry.value.decode()

    async def mem_get(self, key: str) -> str | None:
        return await self._get_text(self.mem, key)

    async def mem_set(self, key: str, value: str) -> None:
        await self.mem.put(key, value.encode())

    async def shared_get(self, key: str) -> str | None:
        return await self._get_text(self.shared, key)

    async def shared_set(self, key: str, value: str) -> None:
        await self.shared.put(key, value.encode())
