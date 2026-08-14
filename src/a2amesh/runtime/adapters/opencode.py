"""OpenCode 运行时适配器。"""
from .base import AgentAdapter


class OpenCodeAdapter(AgentAdapter):
    name = "opencode"
    binary = "opencode"

    def command(self, prompt, workdir, opts):
        cmd = ["opencode", "run", prompt]
        if opts.get("model"):
            cmd += ["--model", opts["model"]]
        return cmd

    def resume_command(self, session_id, prompt, workdir, opts):
        cmd = ["opencode", "run", "--session", session_id]
        if opts.get("model"):
            cmd += ["--model", opts["model"]]
        return cmd + [prompt]
