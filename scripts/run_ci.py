#!/usr/bin/env python3
"""Run the local C0 CI gates and write one machine-readable JSON report."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Gate:
    """One deterministic CI command executed without a shell."""

    gate_id: str
    command: tuple[str, ...]


_GIT_EXECUTABLE = shutil.which("git")

DEFAULT_GATES = (
    Gate("official-fixtures", (sys.executable, "scripts/verify_a2a_fixtures.py")),
    Gate(
        "docs-links",
        (
            sys.executable,
            "scripts/verify_docs_links.py",
            "--json",
            "README.md",
            "docs/specs",
        ),
    ),
    Gate("pytest", (sys.executable, "-m", "pytest", "-q")),
    Gate("ruff", (sys.executable, "-m", "ruff", "check", ".")),
    Gate(
        "compileall",
        (sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"),
    ),
    Gate("git-diff-check", ((_GIT_EXECUTABLE or "git"), "diff", "--check", "HEAD")),
)


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("UV_PROJECT_ENVIRONMENT", None)
    return environment


def _git_value(cwd: Path, *arguments: str) -> str | None:
    if _GIT_EXECUTABLE is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed git executable, shell is disabled
            (_GIT_EXECUTABLE, *arguments),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env=_clean_environment(),
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _repository_snapshot(cwd: Path) -> dict[str, Any]:
    status = _git_value(cwd, "status", "--porcelain", "--untracked-files=all")
    return {
        "head": _git_value(cwd, "rev-parse", "HEAD"),
        "tree": _git_value(cwd, "rev-parse", "HEAD^{tree}"),
        "dirty": bool(status) if status is not None else None,
    }


def _run_gate(gate: Gate, cwd: Path) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(  # noqa: S603 - repository-owned argv, shell is disabled
            gate.command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env=_clean_environment(),
        )
        return_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except OSError as exc:
        return_code = 127
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
    duration_ms = round((time.monotonic() - started) * 1000)
    return {
        "id": gate.gate_id,
        "command": list(gate.command),
        "returnCode": return_code,
        "durationMs": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
    }


def run_gates(gates: Sequence[Gate], cwd: Path) -> dict[str, Any]:
    """Execute every gate and return a complete report, even after failures."""
    resolved_cwd = cwd.resolve()
    results = [_run_gate(gate, resolved_cwd) for gate in gates]
    status = "passed" if all(item["returnCode"] == 0 for item in results) else "failed"
    return {
        "schemaVersion": "1",
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": status,
        "workingDirectory": str(resolved_cwd),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
        },
        "repository": _repository_snapshot(resolved_cwd),
        "gates": results,
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    """Atomically replace the report so readers never observe partial JSON."""
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=repository_root / ".ci-artifacts" / "ci-report.json",
    )
    args = parser.parse_args(argv)

    report = run_gates(DEFAULT_GATES, repository_root)
    write_report(report, args.report)
    passed = sum(item["returnCode"] == 0 for item in report["gates"])
    print(
        f"CI {report['status']}: {passed}/{len(report['gates'])} gates passed; "
        f"report={args.report.resolve()}"
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
