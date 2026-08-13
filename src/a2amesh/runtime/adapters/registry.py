"""适配器注册表 + 本机探测。"""
import shutil

from .base import AgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .hermes import HermesAdapter
from .opencode import OpenCodeAdapter

ALL_ADAPTERS: list[AgentAdapter] = [
    HermesAdapter(),
    CodexAdapter(),
    ClaudeAdapter(),
    OpenCodeAdapter(),
]


def detect_adapters() -> dict[str, AgentAdapter]:
    """按本机 PATH 探测已安装的运行时。"""
    return {a.name: a for a in ALL_ADAPTERS if shutil.which(a.binary)}
