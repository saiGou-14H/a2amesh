"""内置工具。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .decorator import tool
from .registry import ToolRegistry


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


async def _list_dir(path: str) -> dict:
    return {"entries": os.listdir(path or ".")}


def register_all(registry: ToolRegistry):
    registry.register(tool("read_file", "读取文件内容",
                           {"type": "object", "properties": {"path": {"type": "string"}},
                            "required": ["path"]})(_read_file))
    registry.register(tool("write_file", "写文件",
                           {"type": "object", "required": ["path", "content"],
                            "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
                           risk="medium")(_write_file))
    registry.register(tool("run_shell", "执行 shell 命令",
                           {"type": "object", "required": ["command"],
                            "properties": {"command": {"type": "string"}}},
                           risk="high")(_run_shell))
    registry.register(tool("list_dir", "列出目录",
                           {"type": "object", "properties": {"path": {"type": "string"}}})(_list_dir))
