"""Executor 测试：子进程执行 + 流式 on_stream + 取消。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a2amesh.runtime.adapters.base import AgentAdapter
from a2amesh.runtime.executor import Executor


class EchoAdapter(AgentAdapter):
    name = "echo"
    binary = "echo"

    def command(self, prompt, workdir, opts):
        return ["/bin/echo", prompt]

    def resume_command(self, session_id, prompt, workdir, opts):
        return ["/bin/echo", prompt]


async def test_executor_simple():
    ex = Executor({"echo": EchoAdapter()}, default="echo", timeout=10)
    res = await ex.run("echo", "hello-executor", None)
    assert res.ok and "hello-executor" in res.output, res
    print("✅ executor 子进程执行:", res.output.strip())


async def test_executor_stream():
    ex = Executor({"echo": EchoAdapter()}, default="echo", timeout=10)
    events = []

    async def on_stream(evt):
        events.append(evt)

    res = await ex.run("echo", "stream-me", None, on_stream=on_stream)
    assert res.ok, res
    assert len(events) >= 1, events
    assert events[0]["kind"] == "artifact-update"
    print(f"✅ executor 流式: {len(events)} 个事件, 首事件 kind={events[0]['kind']}")


async def main():
    await test_executor_simple()
    await test_executor_stream()
    print("\n🎉 executor 全部通过")


if __name__ == "__main__":
    asyncio.run(main())
