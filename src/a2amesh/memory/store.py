"""MemoryStore —— 三层 KV 记忆（会话 / agent 长期 / 团队共享）。"""
from __future__ import annotations

import json


class MemoryStore:
    def __init__(self, js, agent: str):
        self.js = js
        self.agent = agent
        self.sess = js.KeyValue(f"sess.{agent}")
        self.mem = js.KeyValue(f"mem.{agent}")
        self.shared = js.KeyValue("mem.shared")

    async def session_append(self, session_id: str, message: dict):
        key = session_id
        hist = await self._load(key) or []
        hist.append(message)
        await self.sess.put(key, json.dumps(hist).encode())

    async def session_get(self, session_id: str) -> list[dict]:
        return await self._load(session_id) or []

    async def session_close(self, session_id: str):
        await self.sess.delete(session_id)

    async def _load(self, key: str) -> list[dict] | None:
        try:
            entry = await self.sess.get(key)
        except Exception:
            return None
        if entry is None or not entry.value:
            return None
        return json.loads(entry.value)

    async def mem_get(self, key: str):
        e = await self.mem.get(key)
        return e.value.decode() if e and e.value else None

    async def mem_set(self, key: str, value: str):
        await self.mem.put(key, value.encode())

    async def shared_get(self, key: str):
        e = await self.shared.get(key)
        return e.value.decode() if e and e.value else None

    async def shared_set(self, key: str, value: str):
        await self.shared.put(key, value.encode())
