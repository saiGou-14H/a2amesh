"""Decorator for declaring custom A2AMesh tools."""

from __future__ import annotations

from .base import Tool


def tool(
    name: str,
    description: str,
    parameters: dict,
    risk: str = "low",
    public: bool = False,
):
    """Attach Tool metadata; ToolRegistry.load_custom performs registration."""

    def decorate(function):
        function.__a2amesh_tool__ = Tool(
            name=name,
            description=description,
            parameters=parameters,
            risk=risk,
            public=public,
            source="custom",
            handler=function,
        )
        return function

    return decorate
