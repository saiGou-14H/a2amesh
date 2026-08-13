"""Executor —— 选运行时执行任务，支持流式 + 取消。"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from a2amesh.runtime.adapters.base import AgentAdapter, TaskResult


class Executor:
    def __init__(self, adapters: dict[str, AgentAdapter], default: str, timeout: int = 600):
        self.adapters = adapters
        self.default = default
        self.timeout = timeout

    async def run(self, runtime: str, prompt: str, workdir: str | None,
                  opts: dict | None = None, session_id: str | None = None,
                  on_stream: Callable[[dict], Awaitable] | None = None,
                  cancel_evt: asyncio.Event | None = None) -> TaskResult:
        adapter = self.adapters[runtime]
        opts = opts or {}
        if session_id:
            cmd = adapter.resume_command(session_id, prompt, workdir, opts)
        else:
            cmd = adapter.command(prompt, workdir, opts)

        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=workdir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        chunks: list[bytes] = []
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout)
                if not line:
                    break
                chunks.append(line)
                if on_stream:
                    await on_stream({"kind": "artifact-update",
                                     "artifact": {"artifactId": "a1", "parts": [
                                         {"kind": "text", "text": line.decode(errors="replace")}]}})
                if cancel_evt and cancel_evt.is_set():
                    proc.kill()
                    return TaskResult(ok=False, output="canceled")
        except asyncio.TimeoutError:
            proc.kill()
            return TaskResult(ok=False, output="timeout")

        out, err = await proc.communicate()
        full = b"".join(chunks) or out
        return adapter.parse(full, err, proc.returncode)
