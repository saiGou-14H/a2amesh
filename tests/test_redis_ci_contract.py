from __future__ import annotations

import tomllib
from pathlib import Path

CI_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
PROJECT = Path(__file__).parents[1] / "pyproject.toml"


def test_default_ci_installs_state_and_runs_real_redis_auth_replay_gate() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "uv sync --locked --extra test --extra state" in workflow
    assert "uv run --locked --extra test --extra state python scripts/run_ci.py" in workflow
    assert "services:" in workflow
    assert "redis:" in workflow
    assert (
        "redis@sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf" in workflow
    )
    assert 'command: redis-server --save "" --appendonly no' in workflow
    assert "- 6379:6379" in workflow
    assert '--health-cmd "redis-cli ping"' in workflow
    assert "A2AMESH_TEST_REDIS_URL: redis://127.0.0.1:6379/15" in workflow
    assert 'A2AMESH_TEST_REDIS_DESTRUCTIVE: "1"' in workflow
    project = tomllib.loads(PROJECT.read_text(encoding="utf-8"))
    assert "redis[hiredis]==8.1.0" in project["project"]["optional-dependencies"]["test"]
