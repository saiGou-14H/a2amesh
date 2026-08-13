"""内置工具。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from ..base import Tool
from ..registry import ToolRegistry


async def _read_file(path: str) -> dict:
    return {"content": Path(path).read_text(encoding="utf-8", errors="replace")}


async def _write_file(path: str, content: str) -> dict:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")
    return {"written": path}


async def _run_shell(command: str) -> dict:
    proc = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    return {"stdout": out.decode(errors="replace"), "stderr": err.decode(errors="replace"),
            "exit_code": proc.returncode}


async def _list_dir(path: str = ".") -> dict:
    return {"entries": os.listdir(path)}


def register_all(registry: ToolRegistry):
    registry.register(Tool(
        name="read_file", description="读取文件内容",
        parameters={"type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
        source="builtin", handler=_read_file))
    registry.register(Tool(
        name="write_file", description="写文件",
        parameters={"type": "object", "required": ["path", "content"],
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
        source="builtin", risk="medium", handler=_write_file))
    registry.register(Tool(
        name="run_shell", description="执行 shell 命令",
        parameters={"type": "object", "required": ["command"],
                    "properties": {"command": {"type": "string"}}},
        source="builtin", risk="high", handler=_run_shell))
    registry.register(Tool(
        name="list_dir", description="列出目录",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        source="builtin", handler=_list_dir))
