"""Core correctness and packaging regression tests."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError

import nats
from a2amesh.a2anats.compatibility import LegacyMeshClientAdapter
from a2amesh.a2anats.errors import FORBIDDEN, JsonRpcError
from a2amesh.config import Config
from a2amesh.contracts.models import Message, Plan, Step, Task, TaskStatus, TextPart
from a2amesh.memory.store import MemoryStore
from a2amesh.orchestrator.dispatcher import Dispatcher
from a2amesh.orchestrator.orchestrator import OrchestratorRuntime
from a2amesh.runtime.adapters.base import AgentAdapter
from a2amesh.runtime.agent import AgentRuntime
from a2amesh.tools.registry import ToolRegistry

NATS_URL = os.getenv("A2AMESH_TEST_NATS_URL", "nats://127.0.0.1:4222")


class SlowAdapter(AgentAdapter):
    name = "slow"
    binary = "bash"

    def command(self, prompt, workdir, opts):
        return ["/bin/bash", "-c", "echo started; sleep 10; echo finished"]

    def resume_command(self, session_id, prompt, workdir, opts):
        return self.command(prompt, workdir, opts)


@pytest_asyncio.fixture
async def nc():
    client = await nats.connect(NATS_URL)
    try:
        yield client
    finally:
        await client.close()


def make_config(name: str = "audit") -> Config:
    return Config.model_validate(
        {
            "nats": {"url": NATS_URL, "nkey_seed_env": "A2AMESH_UNUSED_SEED"},
            "compatibility": {"legacy_private_rpc_enabled": True},
            "agent": {
                "name": name,
                "description": "audit agent",
                "default_runtime": "hermes",
                "runtimes": ["hermes"],
                "workdir": None,
                "tools_dir": "./nonexistent",
                "task_timeout_seconds": 30,
            },
            "mcp": [],
        }
    )


def test_config_rejects_invalid_runtime_subject_and_timeouts():
    with pytest.raises(ValidationError):
        Config.model_validate({"agent": {"name": "x", "default_runtime": "bad"}})
    with pytest.raises(ValidationError):
        Config.model_validate({"agent": {"name": "bad.name"}})
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "agent": {
                    "name": "x",
                    "session_ttl_seconds": -1,
                    "task_timeout_seconds": 0,
                }
            }
        )


@pytest.mark.asyncio
async def test_memory_store_creates_buckets_and_persists_values(nc):
    js = nc.jetstream()
    agent = f"audit_{uuid4().hex[:8]}"
    store = await MemoryStore.create(js, agent, session_ttl_seconds=60)
    await store.session_append("s1", {"role": "user", "text": "hello"})
    await store.session_append("s1", {"role": "agent", "text": "world"})
    assert await store.session_get("s1") == [
        {"role": "user", "text": "hello"},
        {"role": "agent", "text": "world"},
    ]
    await store.mem_set("preference", "concise")
    assert await store.mem_get("preference") == "concise"
    await store.shared_set("project", "a2amesh")
    assert await store.shared_get("project") == "a2amesh"


@pytest.mark.asyncio
async def test_message_send_creates_queryable_task_and_cancel_terminates_running_process():
    cfg = make_config("audit-task")
    runtime = AgentRuntime(cfg, adapters={"hermes": SlowAdapter()})
    await runtime.start()
    caller = await nats.connect(NATS_URL)
    client = LegacyMeshClientAdapter(caller, enabled=True)
    task_id = "cancel-me"
    try:
        invocation = asyncio.create_task(
            client.send_message(
                "audit-task",
                Message(role="user", parts=[TextPart(text="slow")]),
                runtime="hermes",
                task_id=task_id,
                timeout=30,
            )
        )
        for _ in range(40):
            try:
                current = await client.get_task("audit-task", task_id)
                if current.status.state == "working":
                    break
            except Exception:
                pass
            await asyncio.sleep(0.05)
        else:
            pytest.fail("task never became queryable as working")

        cancel_result = await client.cancel("audit-task", task_id)
        assert cancel_result.status.state == "canceled"
        final = await asyncio.wait_for(invocation, timeout=3)
        assert final.status.state == "canceled"
        queried = await client.get_task("audit-task", task_id)
        assert queried.status.state == "canceled"
    finally:
        await caller.close()
        await runtime.close()


class NoopClient:
    async def send_message(self, *args, **kwargs):
        raise AssertionError("invalid plans must be rejected before dispatch")


def test_dispatcher_rejects_duplicate_missing_dependency_and_cycle():
    dispatcher = Dispatcher(NoopClient())
    invalid_plans = [
        Plan(
            task_id="duplicate",
            steps=[
                Step(id="s1", target="a", prompt="x"),
                Step(id="s1", target="b", prompt="y"),
            ],
        ),
        Plan(
            task_id="missing",
            steps=[Step(id="s1", target="a", prompt="x", depends_on=["unknown"])],
        ),
        Plan(
            task_id="cycle",
            steps=[
                Step(id="s1", target="a", prompt="x", depends_on=["s2"]),
                Step(id="s2", target="b", prompt="y", depends_on=["s1"]),
            ],
        ),
    ]
    for plan in invalid_plans:
        with pytest.raises(ValueError):
            dispatcher.validate_plan(plan)


class ConcurrentDispatchClient:
    async def send_message(self, *args, **kwargs):
        await asyncio.sleep(0.01)
        return Task(
            id=kwargs["task_id"],
            status=TaskStatus(state="completed"),
        )


@pytest.mark.asyncio
async def test_dispatcher_concurrent_runs_keep_results_isolated():
    dispatcher = Dispatcher(ConcurrentDispatchClient())
    plan_a = Plan(
        task_id="plan-a",
        steps=[Step(id="same-step", target="a", prompt="a")],
    )
    plan_b = Plan(
        task_id="plan-b",
        steps=[Step(id="same-step", target="b", prompt="b")],
    )
    results_a, results_b = await asyncio.gather(
        dispatcher.run(plan_a),
        dispatcher.run(plan_b),
    )
    assert results_a["same-step"].id == "plan-a:same-step"
    assert results_b["same-step"].id == "plan-b:same-step"


class FakeOrchestrator:
    def __init__(self):
        self.calls = 0

    async def handle(self, prompt: str, *, task_id: str | None = None):
        self.calls += 1
        await asyncio.sleep(0.01)
        return Task(id=task_id or "generated", status=TaskStatus(state="completed"))


@pytest.mark.asyncio
async def test_orchestrator_runtime_deduplicates_and_exposes_real_task():
    runtime = OrchestratorRuntime(make_config("orchestrator"), planner=object())
    fake = FakeOrchestrator()
    runtime.orch = fake  # type: ignore[assignment]
    message = Message(role="user", parts=[TextPart(text="same")])
    params = {
        "message": message.model_dump(mode="json"),
        "metadata": {"taskId": "orchestration-1"},
    }
    first, duplicate = await asyncio.gather(
        runtime.handle_task(params),
        runtime.handle_task(params),
    )
    assert first["id"] == duplicate["id"] == "orchestration-1"
    assert fake.calls == 1
    assert (await runtime.get_task({"id": "orchestration-1"}))["id"] == "orchestration-1"

    conflict = {
        "message": Message(
            role="user",
            parts=[TextPart(text="different")],
        ).model_dump(mode="json"),
        "metadata": {"taskId": "orchestration-1"},
    }
    with pytest.raises(JsonRpcError):
        await runtime.handle_task(conflict)
    with pytest.raises(JsonRpcError):
        await runtime.cancel({"id": "orchestration-1"})


def test_custom_tool_decorator_loads_into_target_registry(tmp_path):
    (tmp_path / "custom.py").write_text(
        """
from a2amesh.tools import tool

@tool(
    name="hello_custom",
    description="custom",
    parameters={"type": "object", "properties": {}},
)
async def hello_custom():
    return {"ok": True}
""",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.load_custom(str(tmp_path))
    assert "hello_custom" in {spec.name for spec in registry.list()}


@pytest.mark.asyncio
async def test_remote_tools_require_explicit_public_allowlist():
    cfg = make_config("audit-tools")
    cfg.agent.public_tools = ["list_dir"]
    runtime = AgentRuntime(cfg, adapters={"hermes": SlowAdapter()})
    await runtime.start()
    caller = await nats.connect(NATS_URL)
    client = LegacyMeshClientAdapter(caller, enabled=True)
    try:
        result = await client.call_tool("audit-tools", "list_dir", {"path": "."})
        assert "entries" in result
        with pytest.raises(JsonRpcError) as path_exc:
            await client.call_tool("audit-tools", "list_dir", {"path": "/etc"})
        assert path_exc.value.code == FORBIDDEN
        with pytest.raises(JsonRpcError) as exc:
            await client.call_tool("audit-tools", "run_shell", {"command": "id"})
        assert exc.value.code == FORBIDDEN
        card = await client.get_card("audit-tools")
        assert {skill.id for skill in card.skills} == {"list_dir"}
    finally:
        await caller.close()
        await runtime.close()
