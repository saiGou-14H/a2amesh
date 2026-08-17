#!/usr/bin/env python3
"""Verify pinned official A2A SDK fixtures without project protocol adapters."""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from a2a import types as a2a_types
from a2a.server.jsonrpc_models import JSONRPCError
from a2a.utils.errors import A2A_REASON_TO_ERROR, JSON_RPC_ERROR_CODE_MAP
from google.protobuf.json_format import Error as ProtoJsonError
from google.protobuf.json_format import MessageToDict, ParseDict

_MANIFEST_NAME = "official_fixture_manifest.json"
_MANIFEST_KEYS = {
    "schemaVersion",
    "specVersion",
    "sdkPackage",
    "sdkVersion",
    "fixtures",
}
_PROTOBUF_ENTRY_KEYS = {"id", "file", "kind", "type"}
_ERROR_ENTRY_KEYS = _PROTOBUF_ENTRY_KEYS | {"reason"}
_ERROR_PAYLOAD_KEYS = {"code", "message", "data"}
_EXPECTED_FIXTURES: dict[str, dict[str, str]] = {
    "agent-card": {
        "file": "official_agent_card.json",
        "kind": "protobuf",
        "type": "AgentCard",
    },
    "message-user-text": {
        "file": "message_user_text.json",
        "kind": "protobuf",
        "type": "Message",
    },
    "task-completed": {
        "file": "official_task_completed.json",
        "kind": "protobuf",
        "type": "Task",
    },
    "artifact-text": {
        "file": "official_artifact_text.json",
        "kind": "protobuf",
        "type": "Artifact",
    },
    "task-status-update": {
        "file": "official_task_status_update.json",
        "kind": "protobuf",
        "type": "TaskStatusUpdateEvent",
    },
    "task-artifact-update": {
        "file": "official_task_artifact_update.json",
        "kind": "protobuf",
        "type": "TaskArtifactUpdateEvent",
    },
    "error-task-not-found": {
        "file": "official_task_not_found_error.json",
        "kind": "jsonrpc-error",
        "type": "JSONRPCError",
        "reason": "TASK_NOT_FOUND",
    },
}


class FixtureVerificationError(ValueError):
    """Raised when an official fixture set violates the pinned contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureVerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(payload, dict):
        raise FixtureVerificationError(f"{path.name} must contain a JSON object")
    return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FixtureVerificationError(message)


def _fixture_path(fixtures_dir: Path, file_name: str) -> Path:
    candidate = fixtures_dir / file_name
    _require(not candidate.is_symlink(), f"fixture file must not be a symlink: {file_name}")
    resolved = candidate.resolve()
    _require(
        resolved.parent == fixtures_dir,
        f"fixture file escaped fixture directory: {file_name}",
    )
    _require(resolved.is_file(), f"fixture file does not exist: {file_name}")
    return resolved


def _verify_protobuf(entry: dict[str, Any], payload: dict[str, Any]) -> None:
    type_name = entry["type"]
    message_type = getattr(a2a_types, type_name, None)
    if message_type is None:
        raise FixtureVerificationError(f"unknown official protobuf type: {type_name}")
    message = ParseDict(payload, message_type(), ignore_unknown_fields=False)
    _require(
        MessageToDict(message) == payload,
        f"fixture is not a lossless official ProtoJSON roundtrip: {entry['file']}",
    )


def _verify_jsonrpc_error(entry: dict[str, Any], payload: dict[str, Any]) -> None:
    _require(entry["type"] == "JSONRPCError", "unsupported JSON-RPC error fixture type")
    _require(set(payload) == _ERROR_PAYLOAD_KEYS, "JSON-RPC error fields must be exact")
    parsed = JSONRPCError.model_validate(payload)
    _require(
        parsed.model_dump(mode="json", exclude_none=True) == payload,
        f"fixture is not a lossless SDK JSON-RPC error roundtrip: {entry['file']}",
    )
    reason = entry["reason"]
    error_type = A2A_REASON_TO_ERROR.get(reason)
    _require(error_type is not None, f"unknown official A2A error reason: {reason}")
    _require(
        JSON_RPC_ERROR_CODE_MAP[error_type] == payload["code"],
        f"error code does not match SDK reason mapping: {entry['file']}",
    )


def verify_fixtures(fixtures_dir: Path) -> int:
    """Verify every fixture listed in the pinned manifest and return its count."""
    fixtures_dir = fixtures_dir.resolve()
    manifest = _read_json(fixtures_dir / _MANIFEST_NAME)
    _require(set(manifest) == _MANIFEST_KEYS, "official fixture manifest fields must be exact")
    _require(manifest["schemaVersion"] == "1", "unsupported fixture manifest schema")
    _require(manifest["specVersion"] == "1.0.1", "A2A spec version must be 1.0.1")
    _require(manifest["sdkPackage"] == "a2a-sdk", "unexpected SDK package")
    _require(manifest["sdkVersion"] == "1.1.2", "A2A SDK must be pinned to 1.1.2")
    _require(version("a2a-sdk") == manifest["sdkVersion"], "installed SDK version drift")

    entries = manifest["fixtures"]
    _require(isinstance(entries, list) and bool(entries), "fixture manifest must be non-empty")
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for raw_entry in entries:
        _require(isinstance(raw_entry, dict), "fixture manifest entry must be an object")
        entry = raw_entry
        kind = entry.get("kind")
        expected_keys = _PROTOBUF_ENTRY_KEYS if kind == "protobuf" else _ERROR_ENTRY_KEYS
        _require(kind in {"protobuf", "jsonrpc-error"}, f"unsupported fixture kind: {kind}")
        _require(set(entry) == expected_keys, f"fixture entry fields must be exact: {entry}")

        fixture_id = entry["id"]
        file_name = entry["file"]
        _require(isinstance(fixture_id, str) and bool(fixture_id), "fixture id must be non-empty")
        expected = _EXPECTED_FIXTURES.get(fixture_id)
        if expected is None:
            raise FixtureVerificationError(f"unexpected official fixture id: {fixture_id}")
        _require(
            all(entry.get(key) == value for key, value in expected.items()),
            f"fixture manifest mapping drift: {fixture_id}",
        )
        _require(isinstance(file_name, str), "fixture file must be a string")
        _require(Path(file_name).name == file_name, "fixture file must be a basename")
        _require(file_name.endswith(".json"), "fixture file must use .json")
        _require(fixture_id not in seen_ids, f"duplicate fixture id: {fixture_id}")
        _require(file_name not in seen_files, f"duplicate fixture file: {file_name}")
        seen_ids.add(fixture_id)
        seen_files.add(file_name)

        payload = _read_json(_fixture_path(fixtures_dir, file_name))
        if kind == "protobuf":
            _verify_protobuf(entry, payload)
        else:
            _verify_jsonrpc_error(entry, payload)
    _require(seen_ids == set(_EXPECTED_FIXTURES), "official fixture manifest set is incomplete")
    return len(entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fixtures",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "a2a_v1",
    )
    args = parser.parse_args(argv)
    try:
        count = verify_fixtures(args.fixtures)
    except (OSError, ValueError, ProtoJsonError) as exc:
        print(f"official A2A fixture verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"verified {count} official A2A fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
