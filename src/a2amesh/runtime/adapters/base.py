"""AgentAdapter 接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TaskResult:
    ok: bool
    output: str


class AgentAdapter(ABC):
    name: str
    binary: str  # 用于 shutil.which 探测

    @abstractmethod
    def command(self, prompt: str, workdir: str | None, opts: dict) -> list[str]: ...

    @abstractmethod
    def resume_command(self, session_id: str, prompt: str,
                       workdir: str | None, opts: dict) -> list[str]: ...

    def parse(self, stdout: bytes, stderr: bytes, rc: int) -> TaskResult:
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        return TaskResult(ok=(rc == 0), output=(out or err or f"exit {rc}"))
