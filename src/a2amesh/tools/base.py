"""Tool 模型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[dict]]
    source: str = "custom"
    risk: str = "low"
    public: bool = True
