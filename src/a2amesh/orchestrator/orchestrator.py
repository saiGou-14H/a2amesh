"""Orchestrator：规划 → 拓扑并行派发 → 聚合；可选注册为对称 peer。"""
from __future__ import annotations

import asyncio
import hashlib
import os
from uuid import uuid4

import nats
from a2amesh.a2anats.compatibility import (
    LegacyMeshClientAdapter,
    LegacyMeshServerAdapter,
)
from a2amesh.a2anats.errors import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    UNAVAILABLE,
    JsonRpcError,
)
from a2amesh.config import Config
from a2amesh.contracts.models import (
    AgentCard,
    Message,
    Skill,
    Task,
    TaskStatus,
    TextPart,
)
from a2amesh.runtime.adapters.registry import detect_adapters
from a2amesh.runtime.executor import Executor

from .aggregator import Aggregator
from .dispatcher import Dispatcher
from .planner import Planner


class Orchestrator:
    def __init__(self, client: LegacyMeshClientAdapter, planner, dispatcher=None, aggregator=None):
        self.client = client
        self.planner = planner
        self.dispatcher = dispatcher or Dispatcher(client)
        self.aggregator = aggregator or Aggregator()

    async def handle(self, prompt: str, *, task_id: str | None = None) -> Task:
        agents = await self.client.discover()
        plan = await self.planner.plan(prompt, agents)
        if task_id is not None:
            plan.task_id = task_id
        results = await self.dispatcher.run(plan)
        return self.aggregator.collect(plan, results)


class OrchestratorRuntime:
    """把编排器注册为 mesh 中的一个对称 peer（agent name = orchestrator）。"""

    def __init__(self, cfg: Config, planner=None):
        self.cfg = cfg
        self._planner_override = planner
        self.nc: nats.NATS | None = None
        self._tasks: dict[str, Task] = {}
        self._fingerprints: dict[str, str] = {}
        self._futures: dict[str, asyncio.Future[Task]] = {}
        self._task_lock = asyncio.Lock()

    async def start(self):
        from a2amesh.logging_setup import setup_logging
        setup_logging(self.cfg.observability.log_level)
        seed = os.environ.get(self.cfg.nats.nkey_seed_env)
        kwargs = {"inbox_prefix": "_INBOX.orchestrator"}
        if seed:
            kwargs["nkeys_seed_str"] = seed
        self.nc = await nats.connect(self.cfg.nats.url, **kwargs)
        legacy_enabled = self.cfg.compatibility.legacy_private_rpc_enabled
        self.client = LegacyMeshClientAdapter(self.nc, enabled=legacy_enabled)
        if self._planner_override is not None:
            planner = self._planner_override
        else:
            adapters = detect_adapters()
            executor = Executor(adapters, default=self.cfg.agent.default_runtime,
                                timeout=self.cfg.agent.task_timeout_seconds)
            planner = Planner(executor)
        self.orch = Orchestrator(self.client, planner)
        self.server = LegacyMeshServerAdapter(
            self.nc,
            "orchestrator",
            handler=self,
            enabled=legacy_enabled,
        )
        if legacy_enabled:
            await self.server.start()

    def card(self) -> AgentCard:
        return AgentCard(
            name="orchestrator",
            description="A2AMesh 任务编排器：拆解任务并按依赖并行分发",
            capabilities={"runtimes": [], "tools": []},
            skills=[Skill(id="orchestrate", name="任务编排",
                          description="把复杂任务拆解为子步骤并分发到各 agent")],
        )

    async def handle_task(self, params: dict) -> dict:
        msg = Message(**params["message"])
        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
        metadata = params.get("metadata") or {}
        task_id = metadata.get("taskId") or uuid4().hex
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        async with self._task_lock:
            existing = self._fingerprints.get(task_id)
            if existing is not None and existing != fingerprint:
                raise JsonRpcError(
                    INVALID_PARAMS,
                    "taskId already exists with different payload",
                )
            future = self._futures.get(task_id)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._futures[task_id] = future
                self._fingerprints[task_id] = fingerprint
                self._tasks[task_id] = Task(
                    id=task_id,
                    status=TaskStatus(state="working"),
                    history=[msg],
                )
                owner = True
            else:
                owner = False

        if not owner:
            return (await asyncio.shield(future)).model_dump(mode="json")
        try:
            task = await self.orch.handle(text, task_id=task_id)
            self._tasks[task_id] = task
            if not future.done():
                future.set_result(task)
            return task.model_dump(mode="json")
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()
            raise

    async def handle_task_stream(self, params, msg):
        raise JsonRpcError(METHOD_NOT_FOUND, "orchestrator 不支持流式")

    async def get_task(self, params):
        task_id = params["id"]
        task = self._tasks.get(task_id)
        if task is None:
            raise JsonRpcError(UNAVAILABLE, f"task not found: {task_id}")
        return task.model_dump(mode="json")

    async def cancel(self, params):
        raise JsonRpcError(METHOD_NOT_FOUND, "orchestrator cancel is not implemented")

    async def call_tool(self, params):
        raise JsonRpcError(METHOD_NOT_FOUND, "orchestrator 无工具")
