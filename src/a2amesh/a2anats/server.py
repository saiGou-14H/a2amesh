"""MeshServer —— 被其他 agent 调度。"""
from __future__ import annotations

import json

from a2amesh.a2anats.errors import JsonRpcError, METHOD_NOT_FOUND


class MeshServer:
    def __init__(self, nc, agent_name: str, handler):
        self.nc = nc
        self.name = agent_name
        self.handler = handler
        self._service = None

    async def start(self):
        from nats import micro
        self._service = await micro.add_service(
            self.nc, name=self.name, version="1.0.0",
            description="A2AMesh agent")
        await self.nc.subscribe(f"a2a.rpc.{self.name}", cb=self._on_rpc)
        await self.nc.subscribe(f"a2a.cards.{self.name}", cb=self._on_card)

    async def _on_card(self, msg):
        await msg.respond(json.dumps(self.handler.card().model_dump()).encode())

    async def _on_rpc(self, msg):
        req = json.loads(msg.data)
        try:
            result = await self._dispatch(req["method"], req.get("params", {}), msg)
            await msg.respond(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}).encode())
        except JsonRpcError as e:
            await msg.respond(json.dumps({
                "jsonrpc": "2.0", "id": req.get("id"),
                "error": {"code": e.code, "message": e.message},
            }).encode())

    async def _dispatch(self, method, params, msg):
        if method == "message/send":
            return await self.handler.handle_task(params)
        if method == "message/stream":
            return await self.handler.handle_task_stream(params, msg)
        if method == "tasks/get":
            return await self.handler.get_task(params)
        if method == "tasks/cancel":
            return await self.handler.cancel(params)
        if method == "tools/call":
            return await self.handler.call_tool(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method}")

    async def publish_stream(self, task_id: str, event: dict):
        await self.nc.publish(
            f"a2a.stream.{self.name}.{task_id}", json.dumps(event).encode()
        )
