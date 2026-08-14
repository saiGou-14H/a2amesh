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

    async def run(self, plan: Plan) -> dict[str, Task]:
        self.validate_plan(plan)
        results: dict[str, Task] = {}
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
                running[s.id] = asyncio.create_task(
                    self._run_step(s, plan.task_id, results)
                )
            if not running:
                break
            await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for sid, t in list(running.items()):
                if t.done():
                    del running[sid]
            ready = ready_steps()

        for step in plan.steps:
            if step.status == "pending":
                step.status = "failed"
                results[step.id] = Task(
                    id=step.id,
                    status=TaskStatus(state="failed"),
                    artifacts=[
                        {
                            "artifactId": "blocked",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": "step blocked by a failed dependency",
                                }
                            ],
                        }
                    ],
                )
        return results

    async def _run_step(
        self,
        s: Step,
        plan_task_id: str,
        results: dict[str, Task],
    ) -> None:
        tracker_key = f"{plan_task_id}:{s.id}"
        for attempt in range(1, self.max_attempts + 1):
            self.tracker.start(tracker_key, attempt)
            try:
                task = await self.client.send_message(
                    s.target, Message(role="user", parts=[TextPart(text=s.prompt)]),
                    runtime=s.runtime,
                    task_id=f"{plan_task_id}:{s.id}",
                    retries=1,
                )
                if task.status.state != "completed":
                    raise RuntimeError(
                        f"task returned non-completed status: {task.status.state}"
                    )
                results[s.id] = task
                s.status = "succeeded"
                self.tracker.finish(tracker_key)
                return
            except Exception as e:
                results[s.id] = Task(
                    id=s.id, status=TaskStatus(state="failed"),
                    artifacts=[{"artifactId": "err",
                                "parts": [{"kind": "text", "text": f"step failed: {e}"}]}])
                if attempt < self.max_attempts:
                    await asyncio.sleep(2 ** (attempt - 1))
        s.status = "failed"

    @staticmethod
    def validate_plan(plan: Plan) -> None:
        non_pending = [step.id for step in plan.steps if step.status != "pending"]
        if non_pending:
            raise ValueError(
                f"new plans must contain only pending steps: {non_pending}"
            )
        ids = [step.id for step in plan.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("plan contains duplicate step ids")
        known = set(ids)
        for step in plan.steps:
            missing = set(step.depends_on) - known
            if missing:
                raise ValueError(
                    f"step {step.id} has unknown dependencies: {sorted(missing)}"
                )
            if step.id in step.depends_on:
                raise ValueError(f"step {step.id} cannot depend on itself")

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {step.id: step for step in plan.steps}

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("plan dependency graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)
