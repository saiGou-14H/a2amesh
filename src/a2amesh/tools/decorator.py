"""@tool 装饰器：把函数注册为工具。"""
from __future__ import annotations

from .base import Tool
from .registry import ToolRegistry


def tool(name: str, description: str, parameters: dict,
         risk: str = "low", public: bool = True):
    def deco(fn):
        ToolRegistry.global_instance().register(Tool(
            name=name, description=description, parameters=parameters,
            risk=risk, public=public, source="custom", handler=fn,
        ))
        return fn
    return deco
