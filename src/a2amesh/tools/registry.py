"""ToolRegistry —— 工具注册/装配/调用。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import jsonschema

from a2amesh.a2anats.errors import JsonRpcError, UNAVAILABLE
from a2amesh.contracts.models import ToolSpec
from .base import Tool


class ToolRegistry:
    _global: "ToolRegistry | None" = None

    @classmethod
    def global_instance(cls) -> "ToolRegistry":
        if cls._global is None:
            cls._global = cls()
        return cls._global

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool):
        self._tools[t.name] = t

    def load_builtin(self):
        from a2amesh.tools import builtin
        builtin.register_all(self)

    def load_custom(self, tools_dir: str):
        p = Path(tools_dir)
        if not p.is_dir():
            return
        for f in p.glob("*.py"):
            spec = importlib.util.spec_from_file_location(f"a2amesh_custom_{f.stem}", f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)  # @tool 装饰器自动注册

    async def connect_mcp(self, configs):
        from a2amesh.tools.mcp.connector import connect
        for c in configs:
            for t in await connect(c):
                self.register(t)

    def list(self) -> list[ToolSpec]:
        return [ToolSpec(name=t.name, description=t.description, parameters=t.parameters,
                         source=t.source, risk=t.risk, public=t.public)
                for t in self._tools.values()]

    async def call(self, name: str, arguments: dict) -> dict:
        t = self._tools.get(name)
        if not t:
            raise JsonRpcError(UNAVAILABLE, f"tool not found: {name}")
        jsonschema.validate(arguments, t.parameters)
        return await t.handler(**arguments)
