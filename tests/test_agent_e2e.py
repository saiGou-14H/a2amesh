"""AgentRuntime 端到端测试：启动真实 agent，验证注册/card/工具/discover。"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nats
from a2amesh.a2anats.client import MeshClient
from a2amesh.config import Config
from a2amesh.runtime.agent import AgentRuntime

NATS_URL = "nats://127.0.0.1:4222"


async def main():
    cfg = Config.model_validate({
        "nats": {"url": NATS_URL, "nkey_seed_env": "A2AMESH_NKEY_SEED"},
        "agent": {"name": "win1", "description": "e2e test agent",
                  "default_runtime": "hermes", "workdir": "/root/a2amesh",
                  "runtimes": ["hermes"], "tools_dir": "./nonexistent",
                  "public_tools": ["read_file", "list_dir"]},
        "mcp": [],
    })
    rt = AgentRuntime(cfg)
    await rt.start()
    await asyncio.sleep(0.8)  # 等服务注册 + 卡片发布

    nc = await nats.connect(NATS_URL)
    client = MeshClient(nc)

    # 1. get_card：卡片含 hermes 运行时 + builtin 工具
    card = await client.get_card("win1")
    assert card.name == "win1", card
    rt_names = [r["name"] for r in card.capabilities["runtimes"]]
    tool_names = [t["name"] for t in card.capabilities["tools"]]
    assert "hermes" in rt_names, rt_names
    assert {"read_file", "list_dir", "run_shell", "write_file"} <= set(tool_names), tool_names
    print(f"✅ AgentCard: runtimes={rt_names} tools={tool_names}")

    # 2. tools/call 走完整 serve 链路
    res = await client.call_tool("win1", "list_dir", {"path": "/root/a2amesh"})
    assert "entries" in res and "src" in res["entries"], res
    print("✅ tools/call list_dir:", res["entries"])

    # 3. discover 发现
    agents = await client.discover()
    names = [a.name for a in agents]
    assert "win1" in names, names
    print("✅ discover:", names)

    await nc.close()
    await rt.close()
    print("\n🎉 AgentRuntime 端到端通过")


@pytest.mark.asyncio
async def test_agent_runtime_e2e():
    await main()


if __name__ == "__main__":
    asyncio.run(main())
