"""Builtin workspace-scoped tools."""

from __future__ import annotations

import asyncio
import fnmatch
import os
from pathlib import Path

from a2amesh.a2anats.errors import FORBIDDEN, JsonRpcError

from ..base import Tool
from ..registry import ToolRegistry


class WorkspaceTools:
    _SENSITIVE_DIRECTORIES = {".git", ".ssh", ".aws", ".gnupg", ".kube"}
    _SENSITIVE_FILES = {
        ".env",
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
    _SENSITIVE_PATTERNS = (
        ".env.*",
        "*.key",
        "*.pem",
        "*.p12",
        "*.pfx",
        "*.seed",
        "*seeds.env",
    )
    _SENSITIVE_BASENAMES = {
        "apikey",
        "api_key",
        "credential",
        "credentials",
        "passwd",
        "password",
        "passwords",
        "secret",
        "secrets",
        "token",
        "tokens",
    }

    def __init__(self, root: str | None):
        self.root = Path(root or ".").resolve()

    @classmethod
    def is_sensitive(cls, path: Path) -> bool:
        lowered_parts = {part.lower() for part in path.parts}
        if lowered_parts & cls._SENSITIVE_DIRECTORIES:
            return True
        name = path.name.lower()
        basename = name.split(".", 1)[0]
        return (
            name in cls._SENSITIVE_FILES
            or basename in cls._SENSITIVE_BASENAMES
            or any(fnmatch.fnmatch(name, pattern) for pattern in cls._SENSITIVE_PATTERNS)
        )

    def resolve(self, value: str) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise JsonRpcError(FORBIDDEN, f"path is outside workspace: {value}")
        relative = resolved.relative_to(self.root)
        if self.is_sensitive(relative):
            raise JsonRpcError(FORBIDDEN, "access to sensitive workspace files is forbidden")
        return resolved

    async def read_file(self, path: str) -> dict:
        return {
            "content": self.resolve(path).read_text(
                encoding="utf-8", errors="replace"
            )
        }

    async def write_file(self, path: str, content: str) -> dict:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": str(target)}

    async def list_dir(self, path: str = ".") -> dict:
        directory = self.resolve(path)
        return {
            "entries": sorted(
                name
                for name in os.listdir(directory)
                if not self.is_sensitive((directory / name).relative_to(self.root))
            )
        }


async def _run_shell(command: str, workspace: str | None = None) -> dict:
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return {
        "stdout": out.decode(errors="replace"),
        "stderr": err.decode(errors="replace"),
        "exit_code": proc.returncode,
    }


def register_all(registry: ToolRegistry, workspace: str | None = None) -> None:
    scoped = WorkspaceTools(workspace)

    async def run_shell(command: str) -> dict:
        return await _run_shell(command, str(scoped.root))
    registry.register(
        Tool(
            name="read_file",
            description="读取工作目录内的文件内容",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            source="builtin",
            handler=scoped.read_file,
        )
    )
    registry.register(
        Tool(
            name="write_file",
            description="写入工作目录内的文件",
            parameters={
                "type": "object",
                "required": ["path", "content"],
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "additionalProperties": False,
            },
            source="builtin",
            risk="medium",
            handler=scoped.write_file,
        )
    )
    registry.register(
        Tool(
            name="run_shell",
            description="执行 shell 命令（默认不公开）",
            parameters={
                "type": "object",
                "required": ["command"],
                "properties": {"command": {"type": "string"}},
                "additionalProperties": False,
            },
            source="builtin",
            risk="high",
            handler=run_shell,
        )
    )
    registry.register(
        Tool(
            name="list_dir",
            description="列出工作目录内的目录",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            source="builtin",
            handler=scoped.list_dir,
        )
    )
