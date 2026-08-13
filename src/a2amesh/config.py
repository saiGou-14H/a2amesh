"""配置加载：agents.yaml + 环境变量，pydantic 校验。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class NatsConfig(BaseModel):
    url: str = "wss://127.0.0.1:4222"
    nkey_seed_env: str = "A2AMESH_NKEY_SEED"


class AgentConfig(BaseModel):
    name: str
    description: str = ""
    default_runtime: str = "hermes"
    workdir: str | None = None
    runtimes: list[Literal["hermes", "codex", "claude", "opencode"]] = ["hermes"]
    tools_dir: str = "./tools"
    public_tools: list[str] = []
    session_ttl_seconds: int = 86400
    task_timeout_seconds: int = 600


class McpConfig(BaseModel):
    type: Literal["stdio", "http", "sse", "streamable-http"]
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    headers: dict = {}


class ObservabilityConfig(BaseModel):
    otlp_endpoint: str = ""
    log_level: str = "INFO"


class Config(BaseModel):
    nats: NatsConfig = Field(default_factory=NatsConfig)
    agent: AgentConfig
    mcp: list[McpConfig] = []
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)
