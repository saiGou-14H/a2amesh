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
        """$SRV.PING 广播收集所有在线服务，再逐个拉取 AgentCard。"""
        inbox = f"_INBOX.discover.{uuid4().hex}"
        names: set[str] = set()

        async def cb(msg):
            try:
                data = json.loads(msg.data)
                if data.get("name"):
                    names.add(data["name"])
            except Exception:
                pass

        sub = await self.nc.subscribe(inbox, cb=cb)
        await self.nc.publish("$SRV.PING", b"", reply=inbox)
        await asyncio.sleep(1.0)
        await sub.unsubscribe()

        cards: list[AgentCard] = []
        for name in sorted(names):
            try:
                cards.append(await self.get_card(name))
            except Exception:
                continue
        return cards

    async def get_card(self, agent: str) -> AgentCard:
        resp = await self.nc.request(f"a2a.cards.{agent}", b"", timeout=5)
        return AgentCard(**json.loads(resp.data))

    async def _rpc(self, agent: str, method: str, params: dict, timeout: float,
                   retries: int = 3) -> dict:
        """JSON-RPC 调用；瞬态错误（超时/无响应/连接断）指数退避重试。"""
        import nats.errors as nats_errors
        req = {"jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params}
        for attempt in range(1, retries + 1):
            try:
                resp = await self.nc.request(
                    f"a2a.rpc.{agent}", json.dumps(req).encode(), timeout=timeout)
                data = json.loads(resp.data)
                if "error" in data:
                    raise JsonRpcError(data["error"]["code"], data["error"]["message"])
                return data["result"]
            except JsonRpcError:
                raise
            except (asyncio.TimeoutError, nats_errors.TimeoutError, nats_errors.NoRespondersError,
                    nats_errors.ConnectionClosedError, nats_errors.NoServersError, OSError) as e:
                if attempt >= retries:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
        raise RuntimeError("unreachable")

    async def send_message(self, agent: str, message: Message, *,
                           runtime=None, workdir=None, session_id=None,
                           timeout=600) -> Task:
        result = await self._rpc(agent, "message/send", {
            "message": message.model_dump(),
            "metadata": {"runtime": runtime, "workdir": workdir, "sessionId": session_id},
        }, timeout)
        return Task(**result)

    async def send_message_stream(self, agent: str, message: Message, task_id: str | None = None, *,
                                  runtime=None, workdir=None, session_id=None,
                                  timeout=600) -> tuple[str, list[dict]]:
        """流式任务：返回 (task_id, 事件列表)。事件为 A2A 标准四类。"""
        task_id = task_id or uuid4().hex
        events: list[dict] = []

        async def cb(msg):
            try:
                events.append(json.loads(msg.data))
            except Exception:
                pass

        sub = await self.nc.subscribe(f"a2a.stream.{agent}.{task_id}", cb=cb)
        try:
            await self._rpc(agent, "message/stream", {
                "message": message.model_dump(),
                "metadata": {"runtime": runtime, "workdir": workdir,
                             "sessionId": session_id, "taskId": task_id},
            }, timeout)
            await asyncio.sleep(0.2)  # 等最后事件落盘
        finally:
            await sub.unsubscribe()
        return task_id, events

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
