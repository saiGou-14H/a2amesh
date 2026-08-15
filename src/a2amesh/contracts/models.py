"""Legacy private-prototype Pydantic models.

Compatibility-only during migration to the official ``a2a-sdk`` protobuf
objects exposed by :mod:`a2amesh.protocol`. New bindings and canonical-core
code must not import this module.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

LEGACY_COMPATIBILITY_ONLY = True


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class FilePart(BaseModel):
    kind: Literal["file"] = "file"
    file: dict  # {name, mimeType?, uri?}


class DataPart(BaseModel):
    kind: Literal["data"] = "data"
    data: dict


Part = TextPart | FilePart | DataPart


class Message(BaseModel):
    role: Literal["user", "agent"]
    parts: list[Part]


class Artifact(BaseModel):
    artifactId: str
    parts: list[Part] = []


class TaskStatus(BaseModel):
    state: Literal["submitted", "working", "input-required", "completed", "failed", "canceled"]


class Task(BaseModel):
    id: str
    status: TaskStatus
    history: list[Message] = []
    artifacts: list[Artifact] = []


class RuntimeCapability(BaseModel):
    name: Literal["hermes", "codex", "claude", "opencode"]
    available: bool = True


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict
    source: Literal["builtin", "custom", "runtime", "mcp"] = "custom"
    risk: Literal["low", "medium", "high"] = "low"
    public: bool = True


class Skill(BaseModel):
    id: str
    name: str
    description: str = ""


class AgentCard(BaseModel):
    name: str
    description: str
    capabilities: dict = {}
    skills: list[Skill] = []


class Step(BaseModel):
    id: str
    target: str
    prompt: str
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    depends_on: list[str] = []
    runtime: Literal["hermes", "codex", "claude", "opencode"] | None = None


class Plan(BaseModel):
    task_id: str
    steps: list[Step]
