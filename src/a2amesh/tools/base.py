"""Tool metadata and handler contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[dict]]
    source: str = "custom"
    risk: str = "low"
    public: bool = False
