"""Explicit, default-off adapter for the deprecated private NATS RPC binding.

This module is migration-only. It must never be used as a fallback from ``a2a.v1``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from importlib.resources import files
from uuid import uuid4

import jsonschema

from a2amesh.a2anats.compatibility_policy import (
    LEGACY_PRIVATE_RPC_METHODS,
    LegacyCompatibilityPolicy,
)
from a2amesh.a2anats.errors import INVALID_PARAMS, METHOD_NOT_FOUND, JsonRpcError
from a2amesh.contracts.models import AgentCard, Message, Task, TextPart

logger = logging.getLogger(__name__)
_RPC_SCHEMA = json.loads(
    files("a2amesh.schemas").joinpath("rpc.json").read_text(encoding="utf-8")
)


class LegacyMeshClientAdapter:
    """Migration-only client for ``a2a.rpc.*``; disabled unless explicitly enabled."""

    def __init__(self, nc, *, enabled: bool = False):
        self.nc = nc
        self.policy = LegacyCompatibilityPolicy(enabled=enabled)
        if enabled:
            logger.warning("deprecated private NATS RPC client compatibility is enabled")

    async def discover(self) -> list[AgentCard]:
        """Use the deprecated service/Card discovery path when explicitly enabled."""
        self.policy.require_enabled("discover legacy agents")
        inbox = self.nc.new_inbox()
        names: set[str] = set()

        async def cb(msg):
            try:
                data = json.loads(msg.data)
                if data.get("name"):
                    names.add(data["name"])
            except Exception:
                logger.debug(
                    "ignored malformed legacy service discovery response",
                    exc_info=True,
                )

        sub = await self.nc.subscribe(inbox, cb=cb)
        await self.nc.publish("$SRV.PING", b"", reply=inbox)
        await asyncio.sleep(1.0)
        await sub.unsubscribe()

        cards: list[AgentCard] = []
        for name in sorted(names):
            try:
                cards.append(await self.get_card(name))
            except Exception:
                logger.debug("failed to fetch legacy Agent Card for %s", name, exc_info=True)
        return cards

    async def get_card(self, agent: str) -> AgentCard:
        self.policy.require_enabled("fetch a legacy Agent Card")
        subject = self.policy.card_subject(agent)
        response = await self.nc.request(subject, b"", timeout=5)
        return AgentCard(**json.loads(response.data))

    async def _rpc(
        self,
        agent: str,
        method: str,
        params: dict,
        timeout: float,
        retries: int = 3,
    ) -> dict:
        """Call deprecated private RPC, preserving one request ID across retries."""
        import nats.errors as nats_errors

        self.policy.require_enabled(f"invoke legacy method {method}")
        if method not in LEGACY_PRIVATE_RPC_METHODS:
            raise ValueError(f"method is outside the closed legacy compatibility set: {method}")
        if retries < 1:
            raise ValueError("retries must be at least 1")
        subject = self.policy.rpc_subject(agent)
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
                response = await self.nc.request(subject, payload, timeout=timeout)
                data = json.loads(response.data)
                if not isinstance(data, dict):
                    raise RuntimeError("invalid legacy RPC response: expected an object")
                if data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
                    raise RuntimeError("invalid legacy RPC response envelope")
                has_result = "result" in data
                has_error = "error" in data
                if has_result == has_error:
                    raise RuntimeError(
                        "invalid legacy RPC response: expected result xor error"
                    )
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
        """Collect deprecated stream events from a private request inbox."""
        self.policy.require_enabled("invoke legacy method message/stream")
        subject = self.policy.rpc_subject(agent)
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
                subject,
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
                        raise RuntimeError("invalid legacy streaming terminal response")
                    Task(**data["result"])
                    break
                if not isinstance(data, dict) or "kind" not in data:
                    raise RuntimeError("invalid legacy streaming event")
                events.append(data)
        finally:
            await subscription.unsubscribe()
        return task_id, events

    async def get_task(self, agent: str, task_id: str) -> Task:
        result = await self._rpc(
            agent,
            "tasks/get",
            {"id": task_id},
            10,
            retries=1,
        )
        return Task(**result)

    async def cancel(self, agent: str, task_id: str) -> Task:
        result = await self._rpc(
            agent,
            "tasks/cancel",
            {"id": task_id},
            10,
            retries=1,
        )
        return Task(**result)

    async def call_tool(
        self,
        agent: str,
        tool: str,
        arguments: dict,
        timeout=60,
    ) -> dict:
        return await self._rpc(
            agent,
            "tools/call",
            {"tool": tool, "arguments": arguments},
            timeout,
            retries=1,
        )

    async def broadcast(self, prompt: str, runtime=None) -> list[Task]:
        self.policy.require_enabled("broadcast over legacy private RPC")
        agents = await self.discover()
        return await asyncio.gather(
            *(
                self.send_message(
                    agent.name,
                    Message(role="user", parts=[TextPart(text=prompt)]),
                    runtime=runtime,
                )
                for agent in agents
            )
        )


class LegacyMeshServerAdapter:
    """Migration-only server for ``a2a.rpc.*``; disabled unless explicitly enabled."""

    def __init__(self, nc, agent_name: str, handler, *, enabled: bool = False):
        self.nc = nc
        self.name = agent_name
        self.handler = handler
        self.policy = LegacyCompatibilityPolicy(enabled=enabled)
        self._service = None
        self._subscriptions: list = []
        self._rpc_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.policy.require_enabled("start the legacy private RPC server")
        if self._service is not None or self._subscriptions:
            raise RuntimeError("legacy compatibility server is already started")
        logger.warning(
            "deprecated private NATS RPC server compatibility is enabled for %s",
            self.name,
        )
        from nats import micro

        self._service = await micro.add_service(
            self.nc,
            name=self.name,
            version="1.0.0-legacy-compat",
            description="A2AMesh deprecated private RPC compatibility adapter",
        )
        rpc_subscription = await self.nc.subscribe(
            self.policy.rpc_subject(self.name),
            queue=f"a2a-legacy-worker-{self.name}",
            cb=self._schedule_rpc,
        )
        card_subscription = await self.nc.subscribe(
            self.policy.card_subject(self.name),
            queue=f"a2a-legacy-card-{self.name}",
            cb=self._on_card,
        )
        self._subscriptions.extend((rpc_subscription, card_subscription))

    async def _schedule_rpc(self, msg) -> None:
        self.policy.require_enabled("schedule a legacy private RPC request")
        task = asyncio.create_task(self._on_rpc(msg))
        self._rpc_tasks.add(task)
        task.add_done_callback(self._rpc_tasks.discard)

    async def _on_card(self, msg) -> None:
        self.policy.require_enabled("serve a legacy Agent Card")
        await msg.respond(
            json.dumps(self.handler.card().model_dump(mode="json")).encode()
        )

    async def _on_rpc(self, msg) -> None:
        self.policy.require_enabled("handle a legacy private RPC request")
        request_id = None
        try:
            request = json.loads(msg.data)
            request_id = request.get("id") if isinstance(request, dict) else None
            jsonschema.validate(request, _RPC_SCHEMA)
            result = await self._dispatch(
                request["method"],
                request.get("params", {}),
                msg,
            )
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except json.JSONDecodeError:
            response = self._error_response(request_id, -32700, "invalid JSON")
        except jsonschema.ValidationError as exc:
            code = METHOD_NOT_FOUND if exc.validator == "enum" else INVALID_PARAMS
            message = "unknown method" if code == METHOD_NOT_FOUND else exc.message
            response = self._error_response(request_id, code, message)
        except JsonRpcError as exc:
            response = self._error_response(request_id, exc.code, exc.message)
        except Exception:
            logger.exception("unhandled legacy RPC error")
            response = self._error_response(request_id, -32603, "internal error")
        await msg.respond(json.dumps(response).encode())

    @staticmethod
    def _error_response(request_id, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    async def _dispatch(self, method, params, msg):
        self.policy.require_enabled(f"dispatch legacy method {method}")
        if method == "message/send":
            return await self.handler.handle_task(params)
        if method == "message/stream":

            async def emit(event: dict) -> None:
                if msg.reply:
                    await self.nc.publish(msg.reply, json.dumps(event).encode())

            return await self.handler.handle_task_stream(params, emit)
        if method == "tasks/get":
            return await self.handler.get_task(params)
        if method == "tasks/cancel":
            return await self.handler.cancel(params)
        if method == "tools/call":
            return await self.handler.call_tool(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method}")

    async def close(self) -> None:
        for task in list(self._rpc_tasks):
            if not task.done():
                task.cancel()
        for subscription in self._subscriptions:
            await subscription.unsubscribe()
        self._subscriptions.clear()
        if self._service is not None:
            await self._service.stop()
            self._service = None
