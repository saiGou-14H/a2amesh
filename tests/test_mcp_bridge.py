from __future__ import annotations

import pytest

from a2amesh.contracts.models import AgentCard, Skill, Task, TaskStatus
from a2amesh.mcp_bridge import MeshMCPBridge


class FakeMeshClient:
    def __init__(self) -> None:
        self.send_calls = 0
        self.tasks: dict[str, Task] = {}

    async def discover(self):
        return [
            AgentCard(
                name="worker",
                description="test worker",
                skills=[Skill(id="test", name="test")],
            )
        ]

    async def get_card(self, agent: str):
        assert agent == "worker"
        return (await self.discover())[0]

    async def send_message(self, agent, message, **kwargs):
        assert agent == "worker"
        self.send_calls += 1
        task_id = kwargs["task_id"]
        task = self.tasks.setdefault(
            task_id,
            Task(id=task_id, status=TaskStatus(state="working"), history=[message]),
        )
        return task

    async def get_task(self, agent: str, task_id: str):
        assert agent == "worker"
        return self.tasks[task_id]

    async def cancel(self, agent: str, task_id: str):
        assert agent == "worker"
        task = Task(id=task_id, status=TaskStatus(state="canceled"))
        self.tasks[task_id] = task
        return task


@pytest.mark.asyncio
async def test_bridge_exposes_only_bounded_mesh_tools_and_stable_message_id():
    client = FakeMeshClient()
    bridge = MeshMCPBridge(client)
    assert {tool.name for tool in await bridge.server.list_tools()} == {
        "mesh_list_agents",
        "mesh_get_agent",
        "mesh_submit_task",
        "mesh_get_task",
        "mesh_cancel_task",
    }

    arguments = {
        "targetAgentId": "worker",
        "messageId": "message-001",
        "text": "run tests",
    }
    first = await bridge.server.call_tool("mesh_submit_task", arguments)
    second = await bridge.server.call_tool("mesh_submit_task", arguments)
    assert first.structured_content["taskId"] == "message-001"
    assert first.structured_content["deduplicationResult"] == "CREATED"
    assert second.structured_content["deduplicationResult"] == "DUPLICATE_SAME"
    assert client.send_calls == 2

    with pytest.raises(Exception, match="different payload"):
        await bridge.server.call_tool(
            "mesh_submit_task",
            {**arguments, "text": "different"},
        )
    assert client.send_calls == 2


@pytest.mark.asyncio
async def test_bridge_rejects_missing_or_unsafe_message_ids_before_dispatch():
    client = FakeMeshClient()
    bridge = MeshMCPBridge(client)
    with pytest.raises(Exception):
        await bridge.server.call_tool(
            "mesh_submit_task",
            {"targetAgentId": "worker", "text": "missing id"},
        )
    with pytest.raises(Exception, match="messageId"):
        await bridge.server.call_tool(
            "mesh_submit_task",
            {"targetAgentId": "worker", "messageId": "bad", "text": "x"},
        )
    assert client.send_calls == 0
