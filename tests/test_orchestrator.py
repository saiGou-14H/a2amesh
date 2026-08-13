"""P5 编排器测试：DAG 依赖执行 + 并行 + 失败重试。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nats

from a2amesh.a2anats.client import MeshClient
from a2amesh.a2anats.server import MeshServer
from a2amesh.contracts.models import AgentCard, Message, Plan, Step, TextPart
from a2amesh.orchestrator.orchestrator import Orchestrator

NATS_URL = "nats://127.0.0.1:4222"


class EchoHandler:
    def __init__(self, name: str, fail_first: int = 0):
        self.name = name
        self.fail_first = fail_first
        self.calls = 0

    def card(self):
        return AgentCard(name=self.name, description="worker")

    async def handle_task(self, params):
        self.calls += 1
        msg = Message(**params["message"])
        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
        if self.calls <= self.fail_first:
            return {"id": "x", "status": {"state": "failed"}, "artifacts": []}
        return {"id": "x", "status": {"state": "completed"},
                "artifacts": [{"artifactId": "a1",
                               "parts": [{"kind": "text", "text": f"{self.name}:{text}"}]}]}

    async def handle_task_stream(self, p, m):
        return {"id": "x"}

    async def get_task(self, p):
        return {}

    async def cancel(self, p):
        return {}

    async def call_tool(self, p):
        return {}


class FakePlanner:
    def __init__(self, plan):
        self._plan = plan

    async def plan(self, prompt, agents):
        return self._plan


async def main():
    nc = await nats.connect(NATS_URL)
    h1, h2 = EchoHandler("worker1"), EchoHandler("worker2")
    s1 = MeshServer(nc, "worker1", h1)
    s2 = MeshServer(nc, "worker2", h2)
    await s1.start()
    await s2.start()

    # DAG：s1、s2 无依赖并行，s3 依赖 s1+s2
    plan = Plan(task_id="plan-1", steps=[
        Step(id="s1", target="worker1", prompt="A"),
        Step(id="s2", target="worker2", prompt="B"),
        Step(id="s3", target="worker1", prompt="C", depends_on=["s1", "s2"]),
    ])
    orch = Orchestrator(MeshClient(nc), FakePlanner(plan))
    task = await orch.handle("demo")

    assert task.status.state == "completed", task
    assert all(s.status == "succeeded" for s in plan.steps), plan.steps
    assert h1.calls == 2 and h2.calls == 1, (h1.calls, h2.calls)
    print(f"✅ DAG 编排: worker1×{h1.calls}（s1+s3）, worker2×{h2.calls}（s2），s3 依赖 s1+s2 正确")
    for a in task.artifacts:
        for p in a.parts:
            if isinstance(p, TextPart):
                print("   ", p.text.replace("\n", " | "))

    # 失败重试：前 2 次失败，第 3 次成功
    h3 = EchoHandler("flaky", fail_first=2)
    s3 = MeshServer(nc, "flaky", h3)
    await s3.start()
    plan2 = Plan(task_id="plan-2", steps=[Step(id="s1", target="flaky", prompt="retry me")])
    task2 = await Orchestrator(MeshClient(nc), FakePlanner(plan2)).handle("retry demo")
    assert task2.status.state == "completed", task2
    assert h3.calls == 3, h3.calls
    print(f"✅ 失败重试: flaky 前 2 次失败、第 3 次成功（共 {h3.calls} 次，指数退避）")

    await nc.close()
    print("\n🎉 P5 编排器测试通过")


if __name__ == "__main__":
    asyncio.run(main())
