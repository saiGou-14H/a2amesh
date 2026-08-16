"""No-side-effect preflight contract for the pinned Codex CLI adapter."""

from __future__ import annotations

import pytest

from a2amesh.runtime.adapters import codex
from a2amesh.runtime.adapters.codex import CODEX_CLI_VERSION, CodexAdapter


@pytest.mark.asyncio
async def test_codex_preflight_accepts_pinned_authenticated_cli(monkeypatch) -> None:
    monkeypatch.setattr(codex.shutil, "which", lambda binary: f"/usr/bin/{binary}")
    calls: list[tuple[str, ...]] = []

    async def run(*command: str, timeout: float):
        assert timeout == 5
        calls.append(command)
        if command[-1] == "--version":
            return 0, f"codex-cli {CODEX_CLI_VERSION}\n"
        if command[-2:] == ("exec", "--help"):
            return 0, " ".join(codex._REQUIRED_EXEC_HELP)
        return 0, "Logged in using ChatGPT"

    monkeypatch.setattr(codex, "_run_probe_command", run)
    result = await CodexAdapter().preflight(timeout=5)

    assert result.available is True
    assert result.version == CODEX_CLI_VERSION
    assert result.diagnostics == ()
    assert calls == [
        ("/usr/bin/codex", "--version"),
        ("/usr/bin/codex", "exec", "--help"),
        ("/usr/bin/codex", "login", "status"),
    ]


@pytest.mark.asyncio
async def test_codex_preflight_fails_without_executable_before_subprocess(monkeypatch) -> None:
    monkeypatch.setattr(codex.shutil, "which", lambda binary: None)

    async def must_not_run(*command: str, timeout: float):
        raise AssertionError((command, timeout))

    monkeypatch.setattr(codex, "_run_probe_command", must_not_run)
    result = await CodexAdapter().preflight()

    assert result.available is False
    assert result.executable is None
    assert result.diagnostics == ("codex executable not found",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "help_output", "login_rc", "login_output", "diagnostic"),
    [
        (
            "0.146.0",
            " ".join(codex._REQUIRED_EXEC_HELP),
            0,
            "Logged in",
            "unsupported Codex CLI version",
        ),
        (
            CODEX_CLI_VERSION,
            "--sandbox --ephemeral",
            0,
            "Logged in",
            "help contract missing",
        ),
        (
            CODEX_CLI_VERSION,
            " ".join(codex._REQUIRED_EXEC_HELP),
            1,
            "Not logged in",
            "not authenticated",
        ),
    ],
)
async def test_codex_preflight_fails_closed_on_version_help_or_auth_drift(
    monkeypatch,
    version: str,
    help_output: str,
    login_rc: int,
    login_output: str,
    diagnostic: str,
) -> None:
    monkeypatch.setattr(codex.shutil, "which", lambda binary: f"/usr/bin/{binary}")

    async def run(*command: str, timeout: float):
        del timeout
        if command[-1] == "--version":
            return 0, f"codex-cli {version}"
        if command[-2:] == ("exec", "--help"):
            return 0, help_output
        return login_rc, login_output

    monkeypatch.setattr(codex, "_run_probe_command", run)
    result = await CodexAdapter().preflight()

    assert result.available is False
    assert any(diagnostic in message for message in result.diagnostics)


@pytest.mark.asyncio
async def test_codex_preflight_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        await CodexAdapter().preflight(timeout=0)
