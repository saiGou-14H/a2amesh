"""配置加载：agents.yaml + 环境变量，pydantic 校验。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

RuntimeName = Literal["hermes", "codex", "claude", "opencode"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NatsConfig(StrictModel):
    url: str = "wss://127.0.0.1:4222"
    nkey_seed_env: str = "A2AMESH_NKEY_SEED"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if any(ch.isspace() for ch in value):
            raise ValueError("NATS URL must not contain whitespace")
        parsed = urlsplit(value)
        if parsed.scheme not in {"nats", "tls", "ws", "wss"}:
            raise ValueError("NATS URL scheme must be nats, tls, ws, or wss")
        if not parsed.hostname:
            raise ValueError("NATS URL must include a hostname")
        return value


class AgentConfig(StrictModel):
    name: str
    description: str = ""
    default_runtime: RuntimeName = "hermes"
    workdir: str | None = None
    runtimes: list[RuntimeName] = Field(default_factory=lambda: ["hermes"])
    tools_dir: str = "./tools"
    public_tools: list[str] = Field(default_factory=list)
    session_ttl_seconds: int = Field(default=86400, gt=0)
    task_timeout_seconds: int = Field(default=600, gt=0)

    @field_validator("name")
    @classmethod
    def validate_subject_token(cls, value: str) -> str:
        if not value or any(ch in value for ch in ".*> \t\r\n"):
            raise ValueError("agent name must be a non-empty NATS subject token")
        return value

    @model_validator(mode="after")
    def validate_default_runtime(self):
        if self.default_runtime not in self.runtimes:
            raise ValueError("default_runtime must be included in runtimes")
        return self


class McpConfig(StrictModel):
    type: Literal["stdio", "http", "sse", "streamable-http"]
    name: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @model_validator(mode="after")
    def validate_transport(self):
        if self.type == "sse":
            raise ValueError("legacy MCP SSE transport is not supported")
        if self.type == "stdio":
            if not self.command or self.url:
                raise ValueError("MCP stdio requires command and forbids url")
        else:
            if not self.url or self.command:
                raise ValueError("MCP Streamable HTTP requires url and forbids command")
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid MCP Streamable HTTP URL")
        return self


class CompatibilityConfig(StrictModel):
    legacy_private_rpc_enabled: StrictBool = False


class ObservabilityConfig(StrictModel):
    otlp_endpoint: str = ""
    log_level: str = "INFO"


class Config(StrictModel):
    nats: NatsConfig = Field(default_factory=NatsConfig)
    agent: AgentConfig
    mcp: list[McpConfig] = Field(default_factory=list)
    compatibility: CompatibilityConfig = Field(default_factory=CompatibilityConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)
