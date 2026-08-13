"""Planner 单元测试：JSON 提取 + schema 校验 + 重试。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a2amesh.contracts.models import AgentCard
from a2amesh.orchestrator.planner import Planner
from a2amesh.runtime.adapters.base import TaskResult


class FakeExecutor:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.default = "fake"

    async def run(self, runtime, prompt, workdir, opts, **kw):
        out = self.outputs.pop(0) if self.outputs else "{}"
        return TaskResult(ok=True, output=out)


async def main():
    good = ('{"task_id":"t1","steps":[{"id":"s1","target":"win1",'
            '"prompt":"do X","status":"pending"}]}')

    # 1. 合法输出直接解析
    plan = await Planner(FakeExecutor([good])).plan("demo", [AgentCard(name="win1", description="w")])
    assert plan.task_id == "t1" and plan.steps[0].id == "s1"
    print("✅ Planner 解析合法 Plan（含 json 提取 + schema 校验）")

    # 2. 非法（markdown 包裹）→ 重试 → 合法
    bad = '```json\n{"task_id":"t1","steps":"oops"}\n```'
    plan2 = await Planner(FakeExecutor([bad, good])).plan("demo", [AgentCard(name="win1", description="w")])
    assert plan2.task_id == "t1"
    print("✅ Planner 输出不合规时带错误重试成功")

    # 3. 多次失败 → RuntimeError
    try:
        await Planner(FakeExecutor(["not json", "still bad"]), max_retries=2).plan("demo", [])
        print("❌ 应该抛异常")
    except RuntimeError:
        print("✅ Planner 多次失败后抛 RuntimeError")

    print("\n🎉 Planner 测试通过")


if __name__ == "__main__":
    asyncio.run(main())
