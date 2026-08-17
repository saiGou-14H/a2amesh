"""C0 contracts for the machine-readable CI runner and declared tooling."""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_runner() -> ModuleType:
    path = REPO_ROOT / "scripts" / "run_ci.py"
    spec = importlib.util.spec_from_file_location("a2amesh_run_ci", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_ci_runner_records_all_gate_results_and_writes_json(tmp_path: Path) -> None:
    runner = _load_runner()
    gates = (
        runner.Gate("passing", (sys.executable, "-c", "print('ok')")),
        runner.Gate("failing", (sys.executable, "-c", "raise SystemExit(3)")),
    )
    report = runner.run_gates(gates, tmp_path)
    assert report["schemaVersion"] == "1"
    assert report["scope"] == "core-gates"
    assert report["status"] == "failed"
    assert [gate["id"] for gate in report["gates"]] == ["passing", "failing"]
    assert [gate["returnCode"] for gate in report["gates"]] == [0, 3]
    assert report["gates"][0]["stdout"] == "ok\n"

    path = tmp_path / "reports" / "ci.json"
    runner.write_report(report, path)
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_default_ci_gate_contract_is_closed() -> None:
    runner = _load_runner()
    assert [gate.gate_id for gate in runner.DEFAULT_GATES] == [
        "official-fixtures",
        "docs-links",
        "sdist-closure",
        "wheel-resources",
        "pytest",
        "ruff",
        "compileall",
        "git-diff-check",
    ]
    commands = {gate.gate_id: gate.command for gate in runner.DEFAULT_GATES}
    assert "scripts/verify_a2a_fixtures.py" in commands["official-fixtures"]
    assert "scripts/verify_docs_links.py" in commands["docs-links"]
    assert "scripts/verify_sdist_closure.py" in commands["sdist-closure"]
    assert "scripts/verify_wheel_resources.py" in commands["wheel-resources"]
    assert "tests" in commands["compileall"]


def test_development_and_browser_dependencies_are_pinned() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    optional = project["project"]["optional-dependencies"]
    assert optional["test"] == [
        "build==1.5.0",
        "httpx==0.28.1",
        "mcp==2.0.0",
        "pytest==9.1.1",
        "pytest-asyncio==1.4.0",
        "ruff==0.16.3",
        "setuptools==84.0.0",
    ]
    assert project["build-system"]["requires"] == ["setuptools==84.0.0"]
    assert optional["browser-test"] == ["playwright==1.62.0"]


def test_ci_workflow_uses_runner_and_uploads_report() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/run_ci.py" in workflow
    assert ".ci-artifacts/ci-report.json" in workflow
    assert "actions/upload-artifact" in workflow
    assert "A2AMESH_REQUIRE_BROWSER_GATE: \"1\"" in workflow
    assert "--extra browser-test" in workflow
    assert "UV_DEFAULT_INDEX: https://mirrors.aliyun.com/pypi/simple/" in workflow


def test_active_browser_command_uses_locked_declared_extras() -> None:
    plan = (REPO_ROOT / "docs" / "specs" / "A2AMesh_开发实施计划.md").read_text(
        encoding="utf-8"
    )
    assert "--with playwright" not in plan
    assert (
        "uv run --locked --extra test --extra browser-test pytest -q "
        "tests/test_architecture_browser_smoke.py"
    ) in plan


def test_non_utf8_gate_output_is_losslessly_bounded_to_text(tmp_path: Path) -> None:
    runner = _load_runner()
    gate = runner.Gate(
        "non-utf8",
        (sys.executable, "-c", "import os; os.write(1, b'\\xff')"),
    )
    report = runner.run_gates((gate,), tmp_path)
    assert report["status"] == "passed"
    assert report["gates"][0]["stdout"] == "�"


def test_internal_runner_failure_replaces_stale_green_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    report_path = tmp_path / "ci.json"
    report_path.write_text('{"status":"passed"}\n', encoding="utf-8")

    def fail_before_gates(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("probe failure")

    monkeypatch.setattr(runner, "run_gates", fail_before_gates)
    assert runner.main(["--report", str(report_path)]) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["scope"] == "core-gates"
    assert report["gates"][0]["id"] == "runner-internal"


def test_generated_ci_reports_are_ignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".ci-artifacts/" in ignored


def test_sdist_manifest_includes_ci_and_conformance_sources() -> None:
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "recursive-include scripts *.py" in manifest
    assert "recursive-include tests/conformance *.py" in manifest
    assert "include .gitignore" in manifest
    assert "include .github/workflows/ci.yml" in manifest
    assert "recursive-include docs/archive *.md" in manifest
