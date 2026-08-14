"""Executor 测试：子进程执行 + 流式 on_stream + 取消。"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

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


class ProcessTreeAdapter(AgentAdapter):
    name = "tree"
    binary = sys.executable

    def __init__(self, script: Path, pid_file: Path):
        self.script = script
        self.pid_file = pid_file

    def command(self, prompt, workdir, opts):
        return [sys.executable, str(self.script), str(self.pid_file)]

    def resume_command(self, session_id, prompt, workdir, opts):
        return self.command(prompt, workdir, opts)


@pytest.mark.asyncio
async def test_executor_simple():
    ex = Executor({"echo": EchoAdapter()}, default="echo", timeout=10)
    res = await ex.run("echo", "hello-executor", None)
    assert res.ok and "hello-executor" in res.output, res
    print("✅ executor 子进程执行:", res.output.strip())


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_total_timeout_includes_blocked_stream_callback():
    ex = Executor({"echo": EchoAdapter()}, default="echo", timeout=0.1)
    blocked = asyncio.Event()

    async def on_stream(_evt):
        await blocked.wait()

    result = await asyncio.wait_for(
        ex.run("echo", "callback-must-time-out", None, on_stream=on_stream),
        timeout=1,
    )
    assert not result.ok
    assert result.output == "timeout"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
@pytest.mark.asyncio
async def test_cancel_kills_descendants_that_ignore_sigterm(tmp_path: Path):
    script = tmp_path / "process_tree.py"
    pid_file = tmp_path / "child.pid"
    script.write_text(
        """import signal, subprocess, sys, time
child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
])
open(sys.argv[1], "w", encoding="utf-8").write(str(child.pid))
print("ready", flush=True)
time.sleep(60)
""",
        encoding="utf-8",
    )
    adapter = ProcessTreeAdapter(script, pid_file)
    executor = Executor({"tree": adapter}, default="tree", timeout=10)
    cancel = asyncio.Event()
    invocation = asyncio.create_task(executor.run("tree", "", None, cancel_evt=cancel))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text(encoding="utf-8"))

    cancel.set()
    result = await asyncio.wait_for(invocation, timeout=3)
    assert result.output == "canceled"

    for _ in range(100):
        stat = Path(f"/proc/{child_pid}/stat")
        if not stat.exists() or stat.read_text().split()[2] == "Z":
            break
        await asyncio.sleep(0.02)
    else:
        os.kill(child_pid, 9)
        pytest.fail("descendant process remained alive after cancellation was confirmed")


async def main():
    await test_executor_simple()
    await test_executor_stream()
    print("\n🎉 executor 全部通过")


if __name__ == "__main__":
    asyncio.run(main())
