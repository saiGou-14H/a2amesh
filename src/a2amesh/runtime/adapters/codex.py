"""Codex CLI runtime adapter with a fixed, no-side-effect preflight contract."""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass

from .base import AgentAdapter

CODEX_CLI_VERSION = "0.147.0"
_REQUIRED_EXEC_HELP = (
    "--sandbox",
    "--approve-for-me",
    "--dangerously-bypass-approvals-and-sandbox",
    "--ephemeral",
    "--skip-git-repo-check",
)


@dataclass(frozen=True, slots=True)
class CodexPreflightResult:
    executable: str | None
    version: str | None
    version_supported: bool
    help_contract_valid: bool
    authenticated: bool
    diagnostics: tuple[str, ...]

    @property
    def available(self) -> bool:
        return (
            self.executable is not None
            and self.version_supported
            and self.help_contract_valid
            and self.authenticated
        )


async def _run_probe_command(
    *command: str,
    timeout: float,
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return -1, "probe timed out"
    return process.returncode or 0, stdout.decode(errors="replace")


class CodexAdapter(AgentAdapter):
    name = "codex"
    binary = "codex"

    def command(self, prompt, workdir, opts):
        cmd = ["codex", "exec"]
        if opts.get("full_auto"):
            cmd.append("--approve-for-me")
        if opts.get("danger_full_access"):
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        return cmd + [prompt]

    def resume_command(self, session_id, prompt, workdir, opts):
        cmd = ["codex", "exec", "resume"]
        if opts.get("danger_full_access"):
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        return cmd + [session_id, prompt]

    async def preflight(self, *, timeout: float = 10.0) -> CodexPreflightResult:
        if timeout <= 0:
            raise ValueError("Codex preflight timeout must be positive")
        executable = shutil.which(self.binary)
        if executable is None:
            return CodexPreflightResult(
                executable=None,
                version=None,
                version_supported=False,
                help_contract_valid=False,
                authenticated=False,
                diagnostics=("codex executable not found",),
            )

        diagnostics: list[str] = []
        version_rc, version_output = await _run_probe_command(
            executable,
            "--version",
            timeout=timeout,
        )
        match = re.search(r"\bcodex-cli\s+([0-9]+\.[0-9]+\.[0-9]+)\b", version_output)
        version = match.group(1) if match else None
        version_supported = version_rc == 0 and version == CODEX_CLI_VERSION
        if not version_supported:
            diagnostics.append(
                f"unsupported Codex CLI version: {version or 'unknown'}; "
                f"expected {CODEX_CLI_VERSION}"
            )

        help_rc, help_output = await _run_probe_command(
            executable,
            "exec",
            "--help",
            timeout=timeout,
        )
        missing_help = tuple(flag for flag in _REQUIRED_EXEC_HELP if flag not in help_output)
        help_contract_valid = help_rc == 0 and not missing_help
        if not help_contract_valid:
            diagnostics.append(
                "Codex exec help contract missing: "
                + (", ".join(missing_help) if missing_help else "command failed")
            )

        login_rc, login_output = await _run_probe_command(
            executable,
            "login",
            "status",
            timeout=timeout,
        )
        authenticated = login_rc == 0 and "not logged in" not in login_output.lower()
        if not authenticated:
            diagnostics.append("Codex CLI is not authenticated")

        return CodexPreflightResult(
            executable=executable,
            version=version,
            version_supported=version_supported,
            help_contract_valid=help_contract_valid,
            authenticated=authenticated,
            diagnostics=tuple(diagnostics),
        )
