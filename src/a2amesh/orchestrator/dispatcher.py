"""Dispatcher：按 DAG 拓扑并行派发，指数退避重试。"""
from __future__ import annotations

import asyncio

from a2amesh.contracts.models import Message, Plan, Step, Task, TaskStatus, TextPart
from .tracker import Tracker


class Dispatcher:
    def __init__(self, client, max_attempts: int = 3, tracker: Tracker | None = None):
        self.client = client
        self.max_attempts = max_attempts
        self.tracker = tracker or Tracker()
        self.results: dict[str, Task] = {}

    async def run(self, plan: Plan):
        pending = {s.id: s for s in plan.steps}
        running: dict[str, asyncio.Task] = {}

        def ready_steps() -> list[Step]:
            return [s for s in plan.steps
                    if s.status == "pending"
                    and all(pending[d].status == "succeeded" for d in s.depends_on)]

        ready = ready_steps()
        while ready or running:
            for s in ready:
                s.status = "running"
                running[s.id] = asyncio.create_task(self._run_step(s))
            if not running:
                break
            await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for sid, t in list(running.items()):
                if t.done():
                    del running[sid]
            ready = ready_steps()

    async def _run_step(self, s: Step):
        for attempt in range(1, self.max_attempts + 1):
            self.tracker.start(s.id, attempt)
            try:
                task = await self.client.send_message(
                    s.target, Message(role="user", parts=[TextPart(text=s.prompt)]),
                    runtime=s.runtime)
                if task.status.state == "failed":
                    raise RuntimeError("task returned failed status")
                self.results[s.id] = task
                s.status = "succeeded"
                self.tracker.finish(s.id)
                return
            except Exception as e:
                self.results[s.id] = Task(
                    id=s.id, status=TaskStatus(state="failed"),
                    artifacts=[{"artifactId": "err",
                                "parts": [{"kind": "text", "text": f"step failed: {e}"}]}])
                if attempt < self.max_attempts:
                    await asyncio.sleep(2 ** (attempt - 1))
        s.status = "failed"
