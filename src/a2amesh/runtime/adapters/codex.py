"""Codex CLI 运行时适配器。注意：需 git 仓库 + pty。"""
from .base import AgentAdapter


class CodexAdapter(AgentAdapter):
    name = "codex"
    binary = "codex"

    def command(self, prompt, workdir, opts):
        cmd = ["codex", "exec"]
        if opts.get("full_auto"):
            cmd.append("--full-auto")
        if opts.get("danger_full_access"):
            cmd += ["--sandbox", "danger-full-access"]
        return cmd + [prompt]

    def resume_command(self, session_id, prompt, workdir, opts):
        return ["codex", "--resume", session_id, "exec", prompt]
