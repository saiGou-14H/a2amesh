"""MeshClient —— 调度其他 agent。"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from a2amesh.a2anats.errors import JsonRpcError
from a2amesh.contracts.models import AgentCard, Message, Task, TextPart


class MeshClient:
    def __init__(self, nc):
        self.nc = nc

    async def discover(self) -> list[AgentCard]:
        resp = await self.nc.request("$SRV.INFO", b"", timeout=5)
        data = json.loads(resp.data)
        services = data.get("services") or []
        cards = []
        for svc in services:
            meta = svc.get("metadata") or {}
            if "card" in meta:
                cards.append(AgentCard(**meta["card"]))
        return cards

    async def get_card(self, agent: str) -> AgentCard:
        resp = await self.nc.request(f"a2a.cards.{agent}", b"", timeout=5)
        return AgentCard(**json.loads(resp.data))

    async def _rpc(self, agent: str, method: str, params: dict, timeout: float) -> dict:
        req = {"jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params}
        resp = await asyncio.wait_for(
            self.nc.request(f"a2a.rpc.{agent}", json.dumps(req).encode(), timeout=timeout),
            timeout,
        )
        data = json.loads(resp.data)
        if "error" in data:
            raise JsonRpcError(data["error"]["code"], data["error"]["message"])
        return data["result"]

    async def send_message(self, agent: str, message: Message, *,
                           runtime=None, workdir=None, session_id=None,
                           timeout=600) -> Task:
        result = await self._rpc(agent, "message/send", {
            "message": message.model_dump(),
            "metadata": {"runtime": runtime, "workdir": workdir, "sessionId": session_id},
        }, timeout)
        return Task(**result)

    async def get_task(self, agent: str, task_id: str) -> Task:
        result = await self._rpc(agent, "tasks/get", {"id": task_id}, 10)
        return Task(**result)

    async def cancel(self, agent: str, task_id: str) -> None:
        await self._rpc(agent, "tasks/cancel", {"id": task_id}, 10)

    async def call_tool(self, agent: str, tool: str, arguments: dict, timeout=60) -> dict:
        return await self._rpc(agent, "tools/call", {"tool": tool, "arguments": arguments}, timeout)

    async def broadcast(self, prompt: str, runtime=None) -> list[Task]:
        agents = await self.discover()
        return await asyncio.gather(*(
            self.send_message(a.name, Message(role="user", parts=[TextPart(text=prompt)]),
                              runtime=runtime)
            for a in agents
        ))
