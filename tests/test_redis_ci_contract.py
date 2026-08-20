from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

CI_WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
PROJECT = Path(__file__).parents[1] / "pyproject.toml"
_GITHUB_ACTIONS_SERVICE_KEYS = frozenset(
    {"credentials", "env", "image", "options", "ports", "volumes"}
)
_NATS_CONTAINER_NAME = "a2amesh-ci-nats"
_NATS_IMAGE = (
    "nats@sha256:"
    "b83efabe3e7def1e0a4a31ec6e078999bb17c80363f881df35edc70fcb6bb927"
)
_NATS_COMMAND = "-js -m 8222 -sd /tmp/a2amesh-ci-jetstream"
_NATS_HEALTH_COMMAND = "wget -q --spider http://127.0.0.1:8222/healthz"


def test_default_ci_installs_state_and_runs_real_redis_auth_replay_gate() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "uv sync --locked --extra test --extra state" in workflow
    assert "uv run --locked --extra test --extra state python scripts/run_ci.py" in workflow
    assert "services:" in workflow
    assert "redis:" in workflow
    assert (
        "redis@sha256:02f2cc4882f8bf87c79a220ac958f58c700bdec0dfb9b9ea61b62fb0e8f1bfcf" in workflow
    )
    assert "- 6379:6379" in workflow
    assert '--health-cmd "redis-cli ping"' in workflow
    assert "A2AMESH_TEST_REDIS_URL: redis://127.0.0.1:6379/15" in workflow
    assert 'A2AMESH_TEST_REDIS_DESTRUCTIVE: "1"' in workflow
    project = tomllib.loads(PROJECT.read_text(encoding="utf-8"))
    assert "redis[hiredis]==8.1.0" in project["project"]["optional-dependencies"]["test"]


def test_core_gates_redis_service_uses_only_supported_github_actions_keys() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    redis_service = workflow["jobs"]["core-gates"]["services"]["redis"]
    unsupported_keys = sorted(set(redis_service) - _GITHUB_ACTIONS_SERVICE_KEYS)
    assert unsupported_keys == []


def test_core_gates_starts_pinned_nats_jetstream_before_machine_gates() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    core_gates = workflow["jobs"]["core-gates"]
    steps = core_gates["steps"]
    step_names = [step.get("name") for step in steps]

    assert core_gates["env"]["A2AMESH_TEST_NATS_URL"] == "nats://127.0.0.1:4222"
    start_index = step_names.index("Start NATS JetStream test broker")
    wait_index = step_names.index("Wait for NATS JetStream test broker")
    gates_index = step_names.index("Run machine-readable gates")
    stop_index = step_names.index("Stop NATS JetStream test broker")
    assert start_index < wait_index < gates_index < stop_index

    start = steps[start_index]["run"]
    assert f"--name {_NATS_CONTAINER_NAME}" in start
    assert "--detach --rm" in start
    assert "--publish 127.0.0.1:4222:4222" in start
    assert f'--health-cmd "{_NATS_HEALTH_COMMAND}"' in start
    assert "--health-interval 2s" in start
    assert "--health-timeout 5s" in start
    assert "--health-retries 10" in start
    assert _NATS_IMAGE in start
    assert _NATS_COMMAND in start

    wait = steps[wait_index]["run"]
    assert "docker inspect" in wait
    assert _NATS_CONTAINER_NAME in wait
    assert '"healthy"' in wait
    assert '"unhealthy"' in wait
    assert "docker logs" in wait
    assert "seq 1 30" in wait

    stop = steps[stop_index]
    assert stop["if"] == "always()"
    assert f"docker rm --force {_NATS_CONTAINER_NAME}" in stop["run"]
