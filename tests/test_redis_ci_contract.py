from __future__ import annotations

import tomllib
from pathlib import Path

CI_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
PROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_default_ci_installs_and_runs_state_extra() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "uv sync --locked --extra test --extra state" in workflow
    assert "uv run --locked --extra test --extra state python scripts/run_ci.py" in workflow
    project = tomllib.loads(PROJECT.read_text(encoding="utf-8"))
    assert "redis[hiredis]==8.1.0" in project["project"]["optional-dependencies"]["test"]
