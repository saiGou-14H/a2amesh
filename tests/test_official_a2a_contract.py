"""Pinned official A2A v1.0.1 SDK and ProtoJSON contract."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

from a2a import types as a2a_types
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtobufMessage

FIXTURES = Path(__file__).parent / "fixtures" / "a2a_v1"


def test_official_a2a_sdk_version_is_pinned() -> None:
    assert importlib.metadata.version("a2a-sdk") == "1.1.2"


def test_protocol_objects_are_official_protobuf_types() -> None:
    message = a2a_types.Message()
    assert isinstance(message, ProtobufMessage)
    assert message.DESCRIPTOR.full_name == "lf.a2a.v1.Message"


def test_official_message_protojson_fixture_roundtrip() -> None:
    fixture = json.loads((FIXTURES / "message_user_text.json").read_text())
    message = ParseDict(fixture, a2a_types.Message())

    assert message.message_id == "msg-fixture-001"
    assert message.role == a2a_types.Role.ROLE_USER
    assert message.parts[0].WhichOneof("content") == "text"
    assert MessageToDict(message) == fixture


def test_official_task_state_set_is_not_parallel_project_enum() -> None:
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
