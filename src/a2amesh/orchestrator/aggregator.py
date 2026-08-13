"""Aggregator：合并各步结果为最终 Task。"""
from __future__ import annotations

from a2amesh.contracts.models import Artifact, Plan, Task, TaskStatus, TextPart


class Aggregator:
    def collect(self, plan: Plan, results: dict[str, Task]) -> Task:
        parts = []
        for s in plan.steps:
            r = results.get(s.id)
            out = ""
            if r:
                out = "\n".join(p.text for a in r.artifacts for p in a.parts
                                if isinstance(p, TextPart))
            parts.append(TextPart(text=f"[{s.id}@{s.target}] {s.status}\n{out}".strip()))
        state = "completed" if all(s.status == "succeeded" for s in plan.steps) else "failed"
        return Task(id=plan.task_id, status=TaskStatus(state=state),
                    artifacts=[Artifact(artifactId="plan", parts=parts)])
