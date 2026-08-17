"""C0 conformance tests for pinned official A2A SDK fixtures."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from importlib.metadata import version
from pathlib import Path
from types import ModuleType

import pytest
from a2a import types as a2a_types
from a2a.server.jsonrpc_models import JSONRPCError
from a2a.server.request_handlers import build_error_response
from a2a.utils.errors import A2A_REASON_TO_ERROR, JSON_RPC_ERROR_CODE_MAP, TaskNotFoundError
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.message import Message as ProtobufMessage

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "a2a_v1"
MANIFEST = FIXTURES / "official_fixture_manifest.json"
EXPECTED_FIXTURE_IDS = {
    "agent-card",
    "message-user-text",
    "task-completed",
    "artifact-text",
    "task-status-update",
    "task-artifact-update",
    "error-task-not-found",
}


def _load_manifest() -> dict[str, object]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _load_verifier() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify_a2a_fixtures.py"
    spec = importlib.util.spec_from_file_location("verify_a2a_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protocol_objects_are_official_protobuf_types() -> None:
    message = a2a_types.Message()
    assert isinstance(message, ProtobufMessage)
    assert message.DESCRIPTOR.full_name == "lf.a2a.v1.Message"


def test_official_task_state_set_has_no_parallel_project_enum() -> None:
    states = {value.name for value in a2a_types.TaskState.DESCRIPTOR.values}
    assert states == {
        "TASK_STATE_UNSPECIFIED",
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_AUTH_REQUIRED",
    }


def test_official_sdk_and_fixture_manifest_are_pinned() -> None:
    manifest = _load_manifest()
    assert version("a2a-sdk") == "1.1.2"
    assert manifest["schemaVersion"] == "1"
    assert manifest["specVersion"] == "1.0.1"
    assert manifest["sdkPackage"] == "a2a-sdk"
    assert manifest["sdkVersion"] == "1.1.2"
    entries = manifest["fixtures"]
    assert isinstance(entries, list)
    assert {entry["id"] for entry in entries} == EXPECTED_FIXTURE_IDS
    assert len({entry["file"] for entry in entries}) == len(entries)


def test_official_protobuf_fixtures_strictly_roundtrip() -> None:
    manifest = _load_manifest()
    entries = manifest["fixtures"]
    assert isinstance(entries, list)
    for entry in entries:
        if entry["kind"] != "protobuf":
            continue
        message_type = getattr(a2a_types, entry["type"])
        payload = json.loads((FIXTURES / entry["file"]).read_text(encoding="utf-8"))
        message = ParseDict(payload, message_type(), ignore_unknown_fields=False)
        assert MessageToDict(message) == payload


def test_official_protobuf_fixtures_reject_unknown_parallel_fields() -> None:
    manifest = _load_manifest()
    entries = manifest["fixtures"]
    assert isinstance(entries, list)
    for entry in entries:
        if entry["kind"] != "protobuf":
            continue
        message_type = getattr(a2a_types, entry["type"])
        payload = json.loads((FIXTURES / entry["file"]).read_text(encoding="utf-8"))
        payload["legacyParallelField"] = True
        with pytest.raises(ParseError):
            ParseDict(payload, message_type(), ignore_unknown_fields=False)


def test_official_task_not_found_error_fixture_matches_sdk_mapping() -> None:
    manifest = _load_manifest()
    entries = manifest["fixtures"]
    assert isinstance(entries, list)
    entry = next(item for item in entries if item["id"] == "error-task-not-found")
    payload = json.loads((FIXTURES / entry["file"]).read_text(encoding="utf-8"))
    parsed = JSONRPCError.model_validate(payload)
    assert parsed.model_dump(mode="json", exclude_none=True) == payload
    error_type = A2A_REASON_TO_ERROR[entry["reason"]]
    assert JSON_RPC_ERROR_CODE_MAP[error_type] == payload["code"]
    expected = build_error_response(
        "fixture-request",
        TaskNotFoundError(data={"taskId": "task-missing-fixture"}),
    )["error"]
    assert payload == expected


def test_official_message_fixture_preserves_semantic_fields() -> None:
    payload = json.loads((FIXTURES / "message_user_text.json").read_text(encoding="utf-8"))
    message = ParseDict(payload, a2a_types.Message(), ignore_unknown_fields=False)
    assert message.message_id == "msg-fixture-001"
    assert message.role == a2a_types.Role.ROLE_USER
    assert len(message.parts) == 1
    assert message.parts[0].text == "hello from official A2A"


def test_fixture_verifier_checks_every_manifest_entry() -> None:
    verifier = _load_verifier()
    assert verifier.verify_fixtures(FIXTURES) == len(EXPECTED_FIXTURE_IDS)


def test_fixture_verifier_returns_failure_for_mapping_drift(tmp_path: Path) -> None:
    verifier = _load_verifier()
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copied)
    path = copied / "official_fixture_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = manifest["fixtures"]
    assert isinstance(entries, list)
    next(entry for entry in entries if entry["id"] == "agent-card")["type"] = "Message"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verifier.main([str(copied)]) == 1


def test_fixture_verifier_returns_failure_for_invalid_protojson(tmp_path: Path) -> None:
    verifier = _load_verifier()
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copied)
    path = copied / "official_task_completed.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["legacyParallelField"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert verifier.main([str(copied)]) == 1


def test_fixture_verifier_rejects_symlinked_fixture(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("symlink fixture requires POSIX test permissions")
    verifier = _load_verifier()
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copied)
    path = copied / "message_user_text.json"
    path.unlink()
    try:
        path.symlink_to("official_artifact_text.json")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    assert verifier.main([str(copied)]) == 1


def test_fixture_verifier_rejects_task_not_found_wire_mutation(tmp_path: Path) -> None:
    verifier = _load_verifier()
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copied)
    path = copied / "official_task_not_found_error.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["message"] = "mutated"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert verifier.main([str(copied)]) == 1


def test_fixture_verifier_rejects_manifest_symlink(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("symlink fixture requires POSIX test permissions")
    verifier = _load_verifier()
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copied)
    manifest = copied / "official_fixture_manifest.json"
    outside = tmp_path / "manifest.json"
    shutil.copy2(manifest, outside)
    manifest.unlink()
    try:
        manifest.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    assert verifier.main([str(copied)]) == 1


def test_fixture_verifier_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    verifier = _load_verifier()
    copied = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copied)

    manifest = copied / "official_fixture_manifest.json"
    manifest_text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        manifest_text.replace(
            '"sdkVersion": "1.1.2"',
            '"sdkVersion": "1.1.2", "sdkVersion": "1.1.2"',
            1,
        ),
        encoding="utf-8",
    )
    assert verifier.main([str(copied)]) == 1
    manifest.write_text(manifest_text, encoding="utf-8")

    payload = copied / "official_agent_card.json"
    payload_text = payload.read_text(encoding="utf-8")
    payload.write_text(
        payload_text.replace(
            '"name": "A2AMesh Fixture Agent"',
            '"name": "A2AMesh Fixture Agent", "name": "A2AMesh Fixture Agent"',
            1,
        ),
        encoding="utf-8",
    )
    assert verifier.main([str(copied)]) == 1


def test_legacy_root_contract_test_was_migrated_to_conformance_suite() -> None:
    assert not (REPO_ROOT / "tests" / "test_official_a2a_contract.py").exists()
