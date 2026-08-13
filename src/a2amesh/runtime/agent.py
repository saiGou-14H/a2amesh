"""AgentRuntime —— 统一对称 peer 进程。"""
from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import nats

from a2amesh.a2anats.client import MeshClient
from a2amesh.a2anats.errors import JsonRpcError, UNAVAILABLE
from a2amesh.a2anats.server import MeshServer
from a2amesh.config import Config
from a2amesh.contracts.models import (
    AgentCard, Message, RuntimeCapability, Skill, TextPart, ToolSpec,
)
from a2amesh.runtime.adapters.registry import detect_adapters
from a2amesh.runtime.executor import Executor
from a2amesh.tools.registry import ToolRegistry


class AgentRuntime:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.nc: nats.NATS | None = None
        self.tools = ToolRegistry.global_instance()
        self.executor: Executor | None = None
        self.server: MeshServer | None = None
        self.client: MeshClient | None = None
        self._tasks: dict[str, dict] = {}
        self._cancels: dict[str, asyncio.Event] = {}

    async def start(self):
        seed = os.environ.get(self.cfg.nats.nkey_seed_env)
        kwargs = {"nkeys_seed_str": seed} if seed else {}
        self.nc = await nats.connect(self.cfg.nats.url, **kwargs)

        self.tools.load_builtin()
        self.tools.load_custom(self.cfg.agent.tools_dir)
        await self.tools.connect_mcp(self.cfg.mcp)

        adapters = detect_adapters()
        allowed = set(self.cfg.agent.runtimes)
        adapters = {k: v for k, v in adapters.items() if k in allowed}
        self.executor = Executor(adapters, self.cfg.agent.default_runtime,
                                 timeout=self.cfg.agent.task_timeout_seconds)
        self.server = MeshServer(self.nc, self.cfg.agent.name, handler=self)
        self.client = MeshClient(self.nc)
        await self.server.start()

    # ---- AgentCard ----

    def card(self) -> AgentCard:
        return AgentCard(
            name=self.cfg.agent.name,
            description=self.cfg.agent.description,
            capabilities={
                "runtimes": [RuntimeCapability(name=k).model_dump() for k in self.executor.adapters],
                "default_runtime": self.cfg.agent.default_runtime,
                "tools": [t.model_dump() for t in self.tools.list()],
            },
            skills=[Skill(id=t.name, name=t.name, description=t.description)
                    for t in self.tools.list() if t.public],
        )

    # ---- 处理任务（被调度） ----

    async def handle_task(self, params: dict) -> dict:
        msg = Message(**params["message"])
        meta = params.get("metadata") or {}
        runtime = meta.get("runtime") or self.cfg.agent.default_runtime
        if runtime not in self.executor.adapters:
            raise JsonRpcError(UNAVAILABLE, f"runtime not available: {runtime}")
        task_id = uuid4().hex
        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
        res = await self.executor.run(runtime, text, meta.get("workdir"), meta,
                                      session_id=meta.get("sessionId"))
        return {
            "id": task_id,
            "status": {"state": "completed" if res.ok else "failed"},
            "artifacts": [{"artifactId": "a1", "parts": [{"kind": "text", "text": res.output}]}],
        }

    async def handle_task_stream(self, params: dict, req_msg) -> dict:
        msg = Message(**params["message"])
        meta = params.get("metadata") or {}
        runtime = meta.get("runtime") or self.cfg.agent.default_runtime
        if runtime not in self.executor.adapters:
            raise JsonRpcError(UNAVAILABLE, f"runtime not available: {runtime}")
        task_id = uuid4().hex
        await self.server.publish_stream(task_id, {"kind": "task-id", "id": task_id})
        cancel_evt = asyncio.Event()
        self._cancels[task_id] = cancel_evt
        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))

        async def on_stream(evt):
            await self.server.publish_stream(task_id, {**evt, "taskId": task_id})

        res = await self.executor.run(runtime, text, meta.get("workdir"), meta,
                                      session_id=meta.get("sessionId"),
                                      on_stream=on_stream, cancel_evt=cancel_evt)
        state = "canceled" if cancel_evt.is_set() else ("completed" if res.ok else "failed")
        await self.server.publish_stream(task_id, {"kind": "status-update",
                                                   "status": {"state": state}, "final": True})
        self._cancels.pop(task_id, None)
        return {"id": task_id}

    async def get_task(self, params: dict) -> dict:
        task = self._tasks.get(params["id"])
        if not task:
            raise JsonRpcError(UNAVAILABLE, f"task not found: {params['id']}")
        return task

    async def cancel(self, params: dict) -> dict:
        evt = self._cancels.get(params["id"])
        if evt:
            evt.set()
        return {"canceled": bool(evt)}

    async def call_tool(self, params: dict) -> dict:
        return await self.tools.call(params["tool"], params.get("arguments", {}))

    # ---- 调度别人 ----

    async def dispatch(self, target: str, prompt: str, runtime=None):
        return await self.client.send_message(
            target, Message(role="user", parts=[TextPart(text=prompt)]), runtime=runtime,
        )

    async def broadcast(self, prompt: str, runtime=None):
        return await self.client.broadcast(prompt, runtime=runtime)
