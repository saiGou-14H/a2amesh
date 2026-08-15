"""Tests for the official protocol facade and strict ProtoJSON boundary."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from a2a import types as official
from google.protobuf.json_format import ParseError

from a2amesh import protocol
from a2amesh.contracts import models as legacy_models

FIXTURES = Path(__file__).parent / "fixtures" / "a2a_v1"


def test_facade_exports_official_types_by_identity() -> None:
    assert protocol.AgentCard is official.AgentCard
    assert protocol.Message is official.Message
    assert protocol.Part is official.Part
    assert protocol.Task is official.Task
    assert protocol.TaskState is official.TaskState
    assert protocol.TaskStatusUpdateEvent is official.TaskStatusUpdateEvent
    assert protocol.TaskArtifactUpdateEvent is official.TaskArtifactUpdateEvent
    assert protocol.StreamResponse is official.StreamResponse
    assert protocol.SendMessageRequest is official.SendMessageRequest


def test_facade_protojson_roundtrip_uses_official_names() -> None:
    fixture = json.loads((FIXTURES / "message_user_text.json").read_text())
    message = protocol.from_protojson(fixture, protocol.Message)

    assert protocol.to_protojson_dict(message) == fixture
    assert json.loads(protocol.to_protojson_bytes(message)) == fixture
    assert b'"messageId"' in protocol.to_protojson_bytes(message)
    assert b'"ROLE_USER"' in protocol.to_protojson_bytes(message)


def test_facade_rejects_unknown_parallel_fields() -> None:
    with pytest.raises(ParseError):
        protocol.from_protojson(
            {
                "messageId": "msg-001",
                "role": "ROLE_USER",
                "parts": [{"kind": "text", "text": "legacy field"}],
            },
            protocol.Message,
        )


def test_legacy_contracts_are_explicitly_compatibility_only() -> None:
    assert legacy_models.LEGACY_COMPATIBILITY_ONLY is True


def test_canonical_packages_cannot_import_parallel_protocol_models() -> None:
    source_root = Path(__file__).parents[1] / "src" / "a2amesh"
    forbidden_modules = {"a2amesh.contracts.models", "pydantic"}
    violations: list[str] = []

    for package_name in ("protocol", "core", "bindings"):
        package = source_root / package_name
        if not package.exists():
            continue
        for source in package.rglob("*.py"):
            tree = ast.parse(source.read_text(), filename=str(source))
            for node in ast.walk(tree):
                imported: set[str] = set()
                if isinstance(node, ast.Import):
                    imported = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = {node.module}
                for module in imported:
                    if any(
                        module == forbidden or module.startswith(f"{forbidden}.")
                        for forbidden in forbidden_modules
                    ):
                        violations.append(f"{source.relative_to(source_root)} imports {module}")

    assert violations == []
