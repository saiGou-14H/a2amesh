"""Orchestrator：规划 → 拓扑并行派发 → 聚合；可选注册为对称 peer。"""
from __future__ import annotations

import os

import nats

from a2amesh.a2anats.client import MeshClient
from a2amesh.a2anats.errors import JsonRpcError, METHOD_NOT_FOUND
from a2amesh.a2anats.server import MeshServer
from a2amesh.config import Config
from a2amesh.contracts.models import AgentCard, Message, Skill, Task, TextPart
from a2amesh.runtime.adapters.registry import detect_adapters
from a2amesh.runtime.executor import Executor
from .aggregator import Aggregator
from .dispatcher import Dispatcher
from .planner import Planner


class Orchestrator:
    def __init__(self, client: MeshClient, planner, dispatcher=None, aggregator=None):
        self.client = client
        self.planner = planner
        self.dispatcher = dispatcher or Dispatcher(client)
        self.aggregator = aggregator or Aggregator()

    async def handle(self, prompt: str) -> Task:
        agents = await self.client.discover()
        plan = await self.planner.plan(prompt, agents)
        await self.dispatcher.run(plan)
        return self.aggregator.collect(plan, self.dispatcher.results)


class OrchestratorRuntime:
    """把编排器注册为 mesh 中的一个对称 peer（agent name = orchestrator）。"""

    def __init__(self, cfg: Config, planner=None):
        self.cfg = cfg
        self._planner_override = planner
        self.nc: nats.NATS | None = None

    async def start(self):
        from a2amesh.logging_setup import setup_logging
        setup_logging(self.cfg.observability.log_level)
        seed = os.environ.get(self.cfg.nats.nkey_seed_env)
        kwargs = {"nkeys_seed_str": seed} if seed else {}
        self.nc = await nats.connect(self.cfg.nats.url, **kwargs)
        self.client = MeshClient(self.nc)
        if self._planner_override is not None:
            planner = self._planner_override
        else:
            adapters = detect_adapters()
            executor = Executor(adapters, default=self.cfg.agent.default_runtime,
                                timeout=self.cfg.agent.task_timeout_seconds)
            planner = Planner(executor)
        self.orch = Orchestrator(self.client, planner)
        self.server = MeshServer(self.nc, "orchestrator", handler=self)
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
        task = await self.orch.handle(text)
        return task.model_dump()

    async def handle_task_stream(self, params, msg):
        raise JsonRpcError(METHOD_NOT_FOUND, "orchestrator 不支持流式")

    async def get_task(self, params):
        return {}

    async def cancel(self, params):
        return {"canceled": False}

    async def call_tool(self, params):
        raise JsonRpcError(METHOD_NOT_FOUND, "orchestrator 无工具")
