"""MeshClient —— 调度其他 agent。"""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

from a2amesh.a2anats.errors import JsonRpcError
from a2amesh.contracts.models import AgentCard, Message, Task, TextPart

logger = logging.getLogger(__name__)


class MeshClient:
    def __init__(self, nc):
        self.nc = nc

    async def discover(self) -> list[AgentCard]:
        """$SRV.PING 广播收集所有在线服务，再逐个拉取 AgentCard。"""
        inbox = self.nc.new_inbox()
        names: set[str] = set()

        async def cb(msg):
            try:
                data = json.loads(msg.data)
                if data.get("name"):
                    names.add(data["name"])
            except Exception:
                logger.debug("ignored malformed service discovery response", exc_info=True)

        sub = await self.nc.subscribe(inbox, cb=cb)
        await self.nc.publish("$SRV.PING", b"", reply=inbox)
        await asyncio.sleep(1.0)
        await sub.unsubscribe()

        cards: list[AgentCard] = []
        for name in sorted(names):
            try:
                cards.append(await self.get_card(name))
            except Exception:
                logger.debug("failed to fetch Agent Card for %s", name, exc_info=True)
        return cards

    async def get_card(self, agent: str) -> AgentCard:
        resp = await self.nc.request(f"a2a.cards.{agent}", b"", timeout=5)
        return AgentCard(**json.loads(resp.data))

    async def _rpc(
        self,
        agent: str,
        method: str,
        params: dict,
        timeout: float,
        retries: int = 3,
    ) -> dict:
        """Call the private mesh RPC binding, preserving one request ID across retries."""
        import nats.errors as nats_errors

        if retries < 1:
            raise ValueError("retries must be at least 1")
        request_id = uuid4().hex
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        payload = json.dumps(request).encode()
        transient_errors = (
            TimeoutError,
            nats_errors.NoRespondersError,
            nats_errors.ConnectionClosedError,
            nats_errors.NoServersError,
            OSError,
        )
        for attempt in range(1, retries + 1):
            try:
                response = await self.nc.request(
                    f"a2a.rpc.{agent}", payload, timeout=timeout
                )
                data = json.loads(response.data)
                if not isinstance(data, dict):
                    raise RuntimeError("invalid RPC response: expected an object")
                if data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
                    raise RuntimeError("invalid RPC response envelope")
                has_result = "result" in data
                has_error = "error" in data
                if has_result == has_error:
                    raise RuntimeError("invalid RPC response: expected result xor error")
                if has_error:
                    error = data["error"]
                    raise JsonRpcError(error["code"], error["message"])
                return data["result"]
            except JsonRpcError:
                raise
            except transient_errors:
                if attempt >= retries:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
        raise RuntimeError("unreachable")

    async def send_message(
        self,
        agent: str,
        message: Message,
        *,
        runtime=None,
        workdir=None,
        session_id=None,
        task_id: str | None = None,
        timeout=600,
        retries: int = 3,
    ) -> Task:
        # A client-generated stable task ID makes a lost reply safe to retry. The
        # server deduplicates this ID and rejects conflicting payloads.
        task_id = task_id or uuid4().hex
        result = await self._rpc(
            agent,
            "message/send",
            {
                "message": message.model_dump(mode="json"),
                "metadata": {
                    "runtime": runtime,
                    "workdir": workdir,
                    "sessionId": session_id,
                    "taskId": task_id,
                },
            },
            timeout,
            retries=retries,
        )
        return Task(**result)

    async def send_message_stream(
        self,
        agent: str,
        message: Message,
        task_id: str | None = None,
        *,
        runtime=None,
        workdir=None,
        session_id=None,
        timeout=600,
    ) -> tuple[str, list[dict]]:
        """Collect private mesh stream events from a per-request reply inbox."""
        task_id = task_id or uuid4().hex
        request_id = uuid4().hex
        inbox = self.nc.new_inbox()
        subscription = await self.nc.subscribe(inbox)
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "message/stream",
            "params": {
                "message": message.model_dump(mode="json"),
                "metadata": {
                    "runtime": runtime,
                    "workdir": workdir,
                    "sessionId": session_id,
                    "taskId": task_id,
                },
            },
        }
        events: list[dict] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            await self.nc.publish(
                f"a2a.rpc.{agent}",
                json.dumps(request).encode(),
                reply=inbox,
            )
            await self.nc.flush()
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                msg = await subscription.next_msg(timeout=remaining)
                data = json.loads(msg.data)
                if (
                    isinstance(data, dict)
                    and data.get("jsonrpc") == "2.0"
                    and data.get("id") == request_id
                ):
                    if "error" in data:
                        error = data["error"]
                        raise JsonRpcError(error["code"], error["message"])
                    if "result" not in data:
                        raise RuntimeError("invalid streaming terminal response")
                    Task(**data["result"])
                    break
                if not isinstance(data, dict) or "kind" not in data:
                    raise RuntimeError("invalid streaming event")
                events.append(data)
        finally:
            await subscription.unsubscribe()
        return task_id, events

    async def get_task(self, agent: str, task_id: str) -> Task:
        result = await self._rpc(
            agent, "tasks/get", {"id": task_id}, 10, retries=1
        )
        return Task(**result)

    async def cancel(self, agent: str, task_id: str) -> Task:
        result = await self._rpc(
            agent, "tasks/cancel", {"id": task_id}, 10, retries=1
        )
        return Task(**result)

    async def call_tool(
        self, agent: str, tool: str, arguments: dict, timeout=60
    ) -> dict:
        # A tool may have arbitrary side effects and has no idempotency key.
        return await self._rpc(
            agent,
            "tools/call",
            {"tool": tool, "arguments": arguments},
            timeout,
            retries=1,
        )

    async def broadcast(self, prompt: str, runtime=None) -> list[Task]:
        agents = await self.discover()
        return await asyncio.gather(*(
            self.send_message(a.name, Message(role="user", parts=[TextPart(text=prompt)]),
                              runtime=runtime)
            for a in agents
        ))
