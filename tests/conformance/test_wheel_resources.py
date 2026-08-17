"""C0 contracts for official fixture resources installed in the wheel."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from a2amesh.conformance.official_fixtures import (
    OFFICIAL_FIXTURE_FILES,
    copy_official_fixtures,
    read_official_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_FILES = {
    "official_fixture_manifest.json",
    "official_agent_card.json",
    "message_user_text.json",
    "official_task_completed.json",
    "official_artifact_text.json",
    "official_task_status_update.json",
    "official_task_artifact_update.json",
    "official_task_not_found_error.json",
}


def test_official_fixture_resources_are_complete_and_readable() -> None:
    assert set(OFFICIAL_FIXTURE_FILES) == EXPECTED_FILES
    manifest = read_official_fixture("official_fixture_manifest.json")
    assert manifest["sdkVersion"] == "1.1.2"
    entries = manifest["fixtures"]
    assert isinstance(entries, list)
    assert {entry["file"] for entry in entries} == EXPECTED_FILES - {
        "official_fixture_manifest.json"
    }
    for file_name in OFFICIAL_FIXTURE_FILES:
        assert isinstance(read_official_fixture(file_name), dict)


def test_fixture_resource_reader_rejects_unknown_or_escaped_names() -> None:
    absolute_name = str(Path(Path.cwd().anchor) / "fixture.json")
    for name in ("../official_agent_card.json", absolute_name, "unknown.json"):
        with pytest.raises(ValueError):
            read_official_fixture(name)


def test_official_resources_can_be_materialized_for_mutation_tests(tmp_path: Path) -> None:
    destination = tmp_path / "fixtures"
    copied = copy_official_fixtures(destination)
    assert copied == len(EXPECTED_FILES)
    assert {path.name for path in destination.iterdir()} == EXPECTED_FILES
    assert all(path.is_file() and not path.is_symlink() for path in destination.iterdir())


def test_test_fixture_directory_has_no_duplicate_official_resources() -> None:
    legacy_directory = REPO_ROOT / "tests" / "fixtures" / "a2a_v1"
    assert not (EXPECTED_FILES & {path.name for path in legacy_directory.glob("*.json")})


def test_wheel_package_data_and_sdist_manifest_declare_resources() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]
    assert package_data["a2amesh.conformance"] == ["fixtures/a2a_v1/*.json"]
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "recursive-include src/a2amesh/conformance/fixtures *.json" in manifest
