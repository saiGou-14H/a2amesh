"""ToolRegistry —— 工具注册/装配/调用。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import jsonschema

from a2amesh.a2anats.errors import UNAVAILABLE, JsonRpcError
from a2amesh.contracts.models import ToolSpec

from .base import Tool

if TYPE_CHECKING:
    from a2amesh.tools.mcp.connector import McpConnection


class ToolRegistry:
    _global: ToolRegistry | None = None

    @classmethod
    def global_instance(cls) -> ToolRegistry:
        if cls._global is None:
            cls._global = cls()
        return cls._global

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._mcp_connections: list[McpConnection] = []

    def register(self, t: Tool, *, replace: bool = False):
        if t.name in self._tools and not replace:
            raise ValueError(f"duplicate tool name: {t.name}")
        self._tools[t.name] = t

    def load_builtin(self, workspace: str | None = None):
        from a2amesh.tools import builtin
        builtin.register_all(self, workspace=workspace)

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
            spec.loader.exec_module(mod)
            for value in vars(mod).values():
                declared = getattr(value, "__a2amesh_tool__", None)
                if declared is not None:
                    self.register(declared)

    async def connect_mcp(self, configs):
        from a2amesh.tools.mcp.connector import connect
        for c in configs:
            connection = await connect(c)
            self._mcp_connections.append(connection)
            for t in connection.tools:
                self.register(t)

    async def close(self) -> None:
        connections, self._mcp_connections = self._mcp_connections, []
        for connection in reversed(connections):
            await connection.close()

    def list(self) -> list[ToolSpec]:
        return [ToolSpec(name=t.name, description=t.description, parameters=t.parameters,
                         source=t.source, risk=t.risk, public=t.public)
                for t in self._tools.values()]

    async def call(self, name: str, arguments: dict, *, remote: bool = False) -> dict:
        t = self._tools.get(name)
        if not t:
            raise JsonRpcError(UNAVAILABLE, f"tool not found: {name}")
        if remote and not t.public:
            from a2amesh.a2anats.errors import FORBIDDEN

            raise JsonRpcError(FORBIDDEN, f"tool is not public: {name}")
        if remote and t.risk == "high":
            from a2amesh.a2anats.errors import FORBIDDEN

            raise JsonRpcError(
                FORBIDDEN,
                f"high-risk tool requires an approval mechanism: {name}",
            )
        try:
            jsonschema.validate(arguments, t.parameters)
        except jsonschema.ValidationError as exc:
            from a2amesh.a2anats.errors import INVALID_PARAMS

            raise JsonRpcError(INVALID_PARAMS, exc.message) from exc
        return await t.handler(**arguments)
