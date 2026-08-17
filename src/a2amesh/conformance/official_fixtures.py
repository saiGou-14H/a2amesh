"""Read and materialize the official A2A fixture set from package resources."""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

OFFICIAL_FIXTURE_FILES = (
    "official_fixture_manifest.json",
    "official_agent_card.json",
    "message_user_text.json",
    "official_task_completed.json",
    "official_artifact_text.json",
    "official_task_status_update.json",
    "official_task_artifact_update.json",
    "official_task_not_found_error.json",
)
_ALLOWED_FILES = frozenset(OFFICIAL_FIXTURE_FILES)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key in official fixture resource: {key}")
        result[key] = value
    return result


def _resource_root() -> Traversable:
    return files("a2amesh.conformance").joinpath("fixtures").joinpath("a2a_v1")


def _resource(file_name: str) -> Traversable:
    if not isinstance(file_name, str) or Path(file_name).name != file_name:
        raise ValueError("official fixture resource name must be a basename")
    if file_name not in _ALLOWED_FILES:
        raise ValueError(f"unknown official fixture resource: {file_name}")
    resource = _resource_root().joinpath(file_name)
    if not resource.is_file():
        raise FileNotFoundError(f"official fixture resource is missing: {file_name}")
    return resource


def read_official_fixture(file_name: str) -> dict[str, Any]:
    """Strictly read one known package fixture as a JSON object."""
    payload = json.loads(
        _resource(file_name).read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"official fixture resource must be an object: {file_name}")
    return payload


def copy_official_fixtures(destination: Path) -> int:
    """Materialize exact resource bytes for path-security and mutation tests."""
    if destination.is_symlink():
        raise ValueError("official fixture destination must not be a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.is_dir():
        raise ValueError("official fixture destination must be a directory")
    for file_name in OFFICIAL_FIXTURE_FILES:
        target = destination / file_name
        if target.is_symlink():
            raise ValueError(
                f"official fixture destination file must not be a symlink: {file_name}"
            )
        target.write_bytes(_resource(file_name).read_bytes())
    return len(OFFICIAL_FIXTURE_FILES)
