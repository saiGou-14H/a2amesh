"""Claude Code 运行时适配器（print 模式）。"""
from .base import AgentAdapter


class ClaudeAdapter(AgentAdapter):
    name = "claude"
    binary = "claude"

    def command(self, prompt, workdir, opts):
        cmd = ["claude", "-p"]
        if opts.get("max_turns"):
            cmd += ["--max-turns", str(opts["max_turns"])]
        if opts.get("output_json"):
            cmd += ["--output-format", "json"]
        return cmd + [prompt]

    def resume_command(self, session_id, prompt, workdir, opts):
        cmd = ["claude", "-p", "--resume", session_id]
        if opts.get("max_turns"):
            cmd += ["--max-turns", str(opts["max_turns"])]
        if opts.get("output_json"):
            cmd += ["--output-format", "json"]
        return cmd + [prompt]
