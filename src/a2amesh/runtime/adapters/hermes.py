"""Hermes 运行时适配器。"""
from .base import AgentAdapter


class HermesAdapter(AgentAdapter):
    name = "hermes"
    binary = "hermes"

    def command(self, prompt, workdir, opts):
        return ["hermes", "chat", "-q", prompt, "-Q"]

    def resume_command(self, session_id, prompt, workdir, opts):
        return ["hermes", "chat", "--resume", session_id, "-q", prompt, "-Q"]
