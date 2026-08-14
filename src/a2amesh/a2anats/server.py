"""MeshServer: concurrent JSON-RPC request handling over NATS."""

from __future__ import annotations

import asyncio
import json
import logging
from importlib.resources import files

import jsonschema

from a2amesh.a2anats.errors import INVALID_PARAMS, METHOD_NOT_FOUND, JsonRpcError

logger = logging.getLogger("a2amesh.server")
_RPC_SCHEMA = json.loads(
    files("a2amesh.schemas").joinpath("rpc.json").read_text(encoding="utf-8")
)


class MeshServer:
    def __init__(self, nc, agent_name: str, handler):
        self.nc = nc
        self.name = agent_name
        self.handler = handler
        self._service = None
        self._rpc_tasks: set[asyncio.Task] = set()

    async def start(self):
        from nats import micro

        self._service = await micro.add_service(
            self.nc,
            name=self.name,
            version="1.0.0",
            description="A2AMesh agent",
        )
        await self.nc.subscribe(
            f"a2a.rpc.{self.name}",
            queue=f"a2a-worker-{self.name}",
            cb=self._schedule_rpc,
        )
        await self.nc.subscribe(
            f"a2a.cards.{self.name}",
            queue=f"a2a-card-{self.name}",
            cb=self._on_card,
        )

    async def _schedule_rpc(self, msg):
        task = asyncio.create_task(self._on_rpc(msg))
        self._rpc_tasks.add(task)
        task.add_done_callback(self._rpc_tasks.discard)

    async def _on_card(self, msg):
        await msg.respond(
            json.dumps(self.handler.card().model_dump(mode="json")).encode()
        )

    async def _on_rpc(self, msg):
        request_id = None
        try:
            request = json.loads(msg.data)
            request_id = request.get("id") if isinstance(request, dict) else None
            jsonschema.validate(request, _RPC_SCHEMA)
            result = await self._dispatch(
                request["method"], request.get("params", {}), msg
            )
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except json.JSONDecodeError:
            response = self._error_response(request_id, -32700, "invalid JSON")
        except jsonschema.ValidationError as exc:
            code = METHOD_NOT_FOUND if exc.validator == "enum" else INVALID_PARAMS
            message = (
                "unknown method"
                if code == METHOD_NOT_FOUND
                else exc.message
            )
            response = self._error_response(request_id, code, message)
        except JsonRpcError as exc:
            response = self._error_response(request_id, exc.code, exc.message)
        except Exception:
            logger.exception("unhandled RPC error")
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
        if self._service is not None:
            await self._service.stop()
