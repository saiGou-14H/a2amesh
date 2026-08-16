"""P1 集成测试：两 agent message/send 往返 + get_card + discover + 工具。"""
import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nats
from a2amesh.a2anats.client import MeshClient
from a2amesh.a2anats.server import MeshServer
from a2amesh.contracts.models import AgentCard, Message, TextPart
from a2amesh.tools.registry import ToolRegistry

NATS_URL = "nats://127.0.0.1:4222"


class FakeHandler:
    """模拟一个 agent 的任务处理器：把消息原样 echo 回来。"""

    def __init__(self, name: str):
        self.name = name

    def card(self) -> AgentCard:
        return AgentCard(name=self.name, description="fake handler")

    async def handle_task(self, params: dict) -> dict:
        msg = Message(**params["message"])
        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
        return {"id": "t1", "status": {"state": "completed"},
                "artifacts": [{"artifactId": "a1",
                               "parts": [{"kind": "text", "text": f"echo:{text}"}]}]}

    async def handle_task_stream(self, params, msg):
        return {"id": "t1"}

    async def get_task(self, params):
        return {"id": params["id"], "status": {"state": "completed"}}

    async def cancel(self, params):
        return {"canceled": True}

    async def call_tool(self, params):
        return {"ok": True}


@pytest.mark.asyncio
async def test_roundtrip():
    # agent_b 作为服务端
    nc_b = await nats.connect(NATS_URL)
    server_b = MeshServer(nc_b, "agent_b", FakeHandler("agent_b"), enabled=True)
    await server_b.start()

    # agent_a 作为客户端
    nc_a = await nats.connect(NATS_URL)
    client = MeshClient(nc_a, enabled=True)

    task = await client.send_message(
        "agent_b", Message(role="user", parts=[TextPart(text="hello world")]))
    assert task.status.state == "completed", task
    assert task.artifacts[0].parts[0].text == "echo:hello world"
    print("✅ message/send 往返:", task.artifacts[0].parts[0].text)

    card = await client.get_card("agent_b")
    assert card.name == "agent_b"
    print("✅ get_card:", card.name)

    agents = await client.discover()
    names = [a.name for a in agents]
    assert "agent_b" in names, names
    print("✅ discover:", names)

    await nc_a.close()
    await nc_b.close()


@pytest.mark.asyncio
async def test_tools(tmp_path: Path):
    (tmp_path / "src").mkdir()
    reg = ToolRegistry()
    reg.load_builtin(workspace=str(tmp_path))
    tools = reg.list()
    assert {t.name for t in tools} >= {"read_file", "write_file", "run_shell", "list_dir"}
    print("✅ builtin 工具:", sorted(t.name for t in tools))

    result = await reg.call("list_dir", {"path": "."})
    assert "entries" in result
    assert "src" in result["entries"]
    print("✅ tools/call list_dir:", result["entries"])


async def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        await test_tools(Path(temp_dir))
    await test_roundtrip()
    print("\n🎉 全部通过")


if __name__ == "__main__":
    asyncio.run(main())
