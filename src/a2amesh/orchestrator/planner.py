"""Planner：LLM 结构化输出 Plan(DAG) + JSON Schema 校验 + 重试。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from a2amesh.contracts.models import AgentCard, Plan

PLAN_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "plan.json"


class Planner:
    def __init__(self, executor, max_retries: int = 3):
        self.executor = executor
        self.max_retries = max_retries

    async def plan(self, prompt: str, agents: list[AgentCard]) -> Plan:
        base = self._plan_prompt(prompt, self._agents_context(agents))
        p = base
        last_err: Exception | None = None
        for _ in range(self.max_retries):
            res = await self.executor.run(self.executor.default, p, None,
                                          {"output_json": True})
            try:
                return self._validate(res.output)
            except Exception as e:
                last_err = e
                p = base + f"\n\n【上一次输出不合规：{e}】\n请严格按照 JSON Schema 重新输出，只输出 JSON。"
        raise RuntimeError(f"planner failed after {self.max_retries} retries: {last_err}")

    @staticmethod
    def _agents_context(agents: list[AgentCard]) -> str:
        lines = []
        for a in agents:
            skills = ", ".join(s.name for s in a.skills)
            runtimes = ", ".join(r["name"] for r in a.capabilities.get("runtimes", []))
            lines.append(f"- {a.name}: {a.description}（运行时: {runtimes}；技能: {skills}）")
        return "\n".join(lines) or "（无在线 agent）"

    @staticmethod
    def _plan_prompt(prompt: str, ctx: str) -> str:
        return (
            "你是任务编排器。把下面的任务拆成多个子步骤，分配给可用的 agent。\n"
            f"任务：{prompt}\n"
            "可用 agent：\n" + ctx + "\n"
            "输出要求：只输出一个 JSON 对象（不要 markdown 代码块），结构如下：\n"
            '{"task_id":"<uuid>","steps":[{"id":"s1","depends_on":[],"target":"<agent名>",'
            '"runtime":"<可选>","prompt":"<子任务>","status":"pending"}]}\n'
            "规则：depends_on 列出前置步骤 id；无依赖的步骤会并行执行。"
        )

    @staticmethod
    def _extract_json(text: str) -> str:
        text = re.sub(r"```(?:json)?|```", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("输出中未找到 JSON")
        return text[start:end + 1]

    def _validate(self, output: str) -> Plan:
        import jsonschema
        schema = json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
        obj = json.loads(self._extract_json(output))
        jsonschema.validate(obj, schema)
        return Plan.model_validate(obj)
