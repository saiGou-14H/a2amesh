"""P4 流式测试：message/stream 完整事件流（A2A 标准事件）。"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nats
from a2amesh.a2anats.client import MeshClient
from a2amesh.config import Config
from a2amesh.contracts.models import Message, TextPart
from a2amesh.runtime.adapters.base import AgentAdapter
from a2amesh.runtime.agent import AgentRuntime

NATS_URL = "nats://127.0.0.1:4222"


class SlowEchoAdapter(AgentAdapter):
    """逐行慢速输出，模拟流式产出。"""
    name = "echo"
    binary = "echo"

    def command(self, prompt, workdir, opts):
        return ["/bin/bash", "-c", "for i in 1 2 3; do echo chunk$i; sleep 0.2; done"]

    def resume_command(self, session_id, prompt, workdir, opts):
        return self.command(prompt, workdir, opts)


async def main():
    cfg = Config.model_validate({
        "nats": {"url": NATS_URL, "nkey_seed_env": "A2AMESH_NKEY_SEED"},
        "compatibility": {"legacy_private_rpc_enabled": True},
        "agent": {"name": "win1", "description": "stream test",
                  "default_runtime": "hermes", "workdir": None,
                  "runtimes": ["hermes"], "tools_dir": "./nonexistent"},
        "mcp": [],
    })
    rt = AgentRuntime(cfg, adapters={"hermes": SlowEchoAdapter()})
    await rt.start()
    await asyncio.sleep(0.5)

    nc = await nats.connect(NATS_URL)
    client = MeshClient(nc, enabled=True)

    task_id, events = await client.send_message_stream(
        "win1", Message(role="user", parts=[TextPart(text="hi")]), runtime="hermes")

    kinds = [e.get("kind") for e in events]
    print("事件序列:", kinds)
    assert kinds[0] == "task-id", kinds
    assert all(e.get("contextId") for e in events), "事件缺 contextId"
    assert any(k == "artifact-update" for k in kinds), kinds
    final = [e for e in events if e.get("kind") == "status-update" and e.get("final")]
    assert final and final[-1]["status"]["state"] == "completed", events
    texts = [e["artifact"]["parts"][0]["text"].strip()
             for e in events if e.get("kind") == "artifact-update"]
    print("流式内容:", texts)
    assert texts == ["chunk1", "chunk2", "chunk3"], texts
    print(f"✅ 流式 4 类事件按序到达（{len(events)} 个），最终 completed")

    await nc.close()
    await rt.close()
    print("\n🎉 P4 流式测试通过")


@pytest.mark.asyncio
async def test_streaming_e2e():
    await main()


if __name__ == "__main__":
    asyncio.run(main())
