"""Execute a selected CLI runtime with streaming, timeout, and cancellation."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable

from a2amesh.runtime.adapters.base import AgentAdapter, TaskResult


class Executor:
    def __init__(
        self,
        adapters: dict[str, AgentAdapter],
        default: str,
        timeout: float = 600,
    ):
        self.adapters = adapters
        self.default = default
        self.timeout = timeout

    async def run(
        self,
        runtime: str,
        prompt: str,
        workdir: str | None,
        opts: dict | None = None,
        session_id: str | None = None,
        on_stream: Callable[[dict], Awaitable[None]] | None = None,
        cancel_evt: asyncio.Event | None = None,
    ) -> TaskResult:
        adapter = self.adapters[runtime]
        opts = opts or {}
        command = (
            adapter.resume_command(session_id, prompt, workdir, opts)
            if session_id
            else adapter.command(prompt, workdir, opts)
        )

        process_options = {}
        if os.name == "nt":
            # Isolate the child so termination does not target this Python process.
            process_options["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            process_options["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **process_options,
        )
        if proc.stdout is None or proc.stderr is None:
            proc.kill()
            await proc.wait()
            raise RuntimeError("subprocess pipes were not created")

        chunks: list[bytes] = []

        async def read_stdout() -> None:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                chunks.append(line)
                if on_stream:
                    await on_stream(
                        {
                            "kind": "artifact-update",
                            "artifact": {
                                "artifactId": "a1",
                                "parts": [
                                    {
                                        "kind": "text",
                                        "text": line.decode(errors="replace"),
                                    }
                                ],
                            },
                        }
                    )

        stdout_task = asyncio.create_task(read_stdout())
        stderr_task = asyncio.create_task(proc.stderr.read())
        process_task = asyncio.create_task(proc.wait())
        cancel_task = (
            asyncio.create_task(cancel_evt.wait()) if cancel_evt is not None else None
        )

        waiters = {process_task}
        if cancel_task is not None:
            waiters.add(cancel_task)

        async def cancel_and_drain_io() -> None:
            for task in (stdout_task, stderr_task, process_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                stdout_task,
                stderr_task,
                process_task,
                return_exceptions=True,
            )

        try:
            async with asyncio.timeout(self.timeout):
                done, _ = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task is not None and cancel_task in done:
                    await self._terminate(proc)
                    await cancel_and_drain_io()
                    return TaskResult(ok=False, output="canceled")

                await process_task
                await stdout_task
                stderr = await stderr_task
                return adapter.parse(b"".join(chunks), stderr, proc.returncode or 0)
        except TimeoutError:
            await self._terminate(proc)
            await cancel_and_drain_io()
            return TaskResult(ok=False, output="timeout")
        except asyncio.CancelledError:
            await self._terminate(proc)
            await cancel_and_drain_io()
            raise
        finally:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(proc.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            except (FileNotFoundError, ProcessLookupError):
                if proc.returncode is None:
                    proc.kill()
            if proc.returncode is None:
                await proc.wait()
            return

        # The child is a process-group leader (start_new_session=True).  Always
        # signal the group, even when its leader has already exited: descendants
        # may still hold inherited pipes and continue performing side effects.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except TimeoutError:
                pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if proc.returncode is None:
            await proc.wait()
