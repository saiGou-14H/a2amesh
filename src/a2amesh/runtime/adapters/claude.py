"""Claude Code 运行时适配器（print 模式）。"""
from .base import AgentAdapter


class ClaudeAdapter(AgentAdapter):
    name = "claude"
    binary = "claude"

    def command(self, prompt, workdir, opts):
        cmd = ["claude", "-p", prompt]
        if opts.get("max_turns"):
            cmd += ["--max-turns", str(opts["max_turns"])]
        if opts.get("output_json"):
            cmd.append("--output-format json")
        return cmd

    def resume_command(self, session_id, prompt, workdir, opts):
        return self.command(prompt, workdir, opts) + ["--resume", session_id]
