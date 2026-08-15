"""Contract tests for StreamSessionOpenedV1 and its initial frame."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from google.protobuf.message import Message as ProtobufMessage

from a2amesh import protocol
from a2amesh.bindings.nats_v1 import (
    StreamSessionFrameV1,
    StreamSessionOpenedV1,
)
from a2amesh.bindings.nats_v1.envelope import BindingValidationError
from a2amesh.core import Operation

FIXTURE = Path(__file__).parent / "fixtures" / "a2a_v1" / "nats_stream_session_opened.json"
CONTRACT = (
    Path(__file__).parent
    / "fixtures"
    / "a2a_v1"
    / "nats_stream_session_opened_contract.json"
)


def fixture_dict() -> dict:
    return json.loads(FIXTURE.read_text())


def task_stream_response(state: int) -> ProtobufMessage:
    return protocol.StreamResponse(
        task=protocol.Task(
            id="task-01H-fixture",
            context_id="ctx-01H-fixture",
            status=protocol.TaskStatus(state=state),
        )
    )


def opened_for_state(state: int, *, final: bool) -> StreamSessionOpenedV1:
    frame = StreamSessionFrameV1.create(
        stream_session_id="session-01H-fixture",
        stream_open_id="stream-open-01H-fixture",
        sequence=0,
        event_seq=7,
        final=final,
        canonical_stream_response=task_stream_response(state),
    )
    return StreamSessionOpenedV1(
        stream_session_id="session-01H-fixture",
        stream_open_id="stream-open-01H-fixture",
        task_id="task-01H-fixture",
        operation=Operation.SEND_STREAMING_MESSAGE,
        caller_delivery_subject=(
            "_DELIVER.a2amesh.stream.caller01.gateway-01.stream-open-01H-fixture"
        ),
        snapshot_event_seq=7,
        expires_at=datetime(2026, 8, 14, 3, 30, tzinfo=UTC),
        initial_frame=frame,
    )


def test_fixed_open_fixture_roundtrips_as_canonical_state_bytes() -> None:
    fixture = fixture_dict()
    contract = json.loads(CONTRACT.read_text())
    opened = StreamSessionOpenedV1.from_dict(fixture)
    canonical = opened.canonical_bytes()

    assert opened.operation is Operation.SEND_STREAMING_MESSAGE
    assert opened.initial_frame.sequence == 0
    assert opened.initial_frame.event_seq == opened.snapshot_event_seq == 7
    assert opened.initial_frame.payload_digest == contract["framePayloadSha256"]
    assert opened.initial_frame.compute_payload_digest() == opened.initial_frame.payload_digest
    assert len(canonical) == contract["canonicalLength"]
    assert hashlib.sha256(canonical).hexdigest() == contract["openedResponseSha256"]
    assert opened.to_dict() == fixture
    assert json.loads(canonical) == fixture
    assert StreamSessionOpenedV1.from_json_bytes(canonical) == opened


def test_both_streaming_operations_use_the_same_open_contract() -> None:
    fixture = fixture_dict()
    fixture["operation"] = "SubscribeToTask"
    opened = StreamSessionOpenedV1.from_dict(fixture)
    assert opened.operation is Operation.SUBSCRIBE_TO_TASK

    fixture["operation"] = "SendMessage"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamSessionOpenedV1.from_dict(fixture)


def test_initial_frame_final_matches_official_task_terminal_state() -> None:
    opened_for_state(protocol.TaskState.TASK_STATE_WORKING, final=False)
    opened_for_state(protocol.TaskState.TASK_STATE_COMPLETED, final=True)
    opened_for_state(protocol.TaskState.TASK_STATE_FAILED, final=True)
    opened_for_state(protocol.TaskState.TASK_STATE_CANCELED, final=True)
    opened_for_state(protocol.TaskState.TASK_STATE_REJECTED, final=True)

    with pytest.raises(BindingValidationError, match="final does not match"):
        opened_for_state(protocol.TaskState.TASK_STATE_WORKING, final=True)
    with pytest.raises(BindingValidationError, match="final does not match"):
        opened_for_state(protocol.TaskState.TASK_STATE_COMPLETED, final=False)


def test_initial_frame_must_be_official_task_snapshot() -> None:
    response = protocol.StreamResponse(
        status_update=protocol.TaskStatusUpdateEvent(
            task_id="task-01H-fixture",
            context_id="ctx-01H-fixture",
            status=protocol.TaskStatus(state=protocol.TaskState.TASK_STATE_WORKING),
        )
    )
    frame = StreamSessionFrameV1.create(
        stream_session_id="session-01H-fixture",
        stream_open_id="stream-open-01H-fixture",
        sequence=0,
        event_seq=7,
        final=False,
        canonical_stream_response=response,
    )
    with pytest.raises(BindingValidationError, match="Task snapshot"):
        StreamSessionOpenedV1(
            stream_session_id="session-01H-fixture",
            stream_open_id="stream-open-01H-fixture",
            task_id="task-01H-fixture",
            operation=Operation.SEND_STREAMING_MESSAGE,
            caller_delivery_subject=(
                "_DELIVER.a2amesh.stream.caller01.gateway-01.stream-open-01H-fixture"
            ),
            snapshot_event_seq=7,
            expires_at=datetime(2026, 8, 14, 3, 30, tzinfo=UTC),
            initial_frame=frame,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("streamSessionId", "other-session", "streamSessionId mismatch"),
        ("taskId", "other-task", "Task ID mismatch"),
        ("snapshotEventSeq", 8, "eventSeq must equal"),
    ],
)
def test_outer_and_initial_frame_identity_must_match(
    field: str, value: object, expected_error: str
) -> None:
    fixture = fixture_dict()
    fixture[field] = value
    with pytest.raises(BindingValidationError, match=expected_error):
        StreamSessionOpenedV1.from_dict(fixture)


def test_stream_open_id_and_delivery_subject_are_bound() -> None:
    fixture = fixture_dict()
    fixture["callerDeliverySubject"] = (
        "_DELIVER.a2amesh.stream.caller01.gateway-01.other-stream"
    )
    with pytest.raises(BindingValidationError, match="bound to streamOpenId"):
        StreamSessionOpenedV1.from_dict(fixture)

    fixture = fixture_dict()
    fixture["callerDeliverySubject"] = "_DELIVER.a2amesh.stream.>"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamSessionOpenedV1.from_dict(fixture)


def test_digest_unknown_fields_sequence_and_timestamp_fail_closed() -> None:
    fixture = fixture_dict()
    fixture["initialFrame"]["payloadDigest"] = "0" * 64
    with pytest.raises(BindingValidationError, match="payloadDigest does not match"):
        StreamSessionOpenedV1.from_dict(fixture)

    fixture = fixture_dict()
    fixture["initialFrame"]["sequence"] = 1
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamSessionOpenedV1.from_dict(fixture)

    fixture = fixture_dict()
    fixture["expiresAt"] = "2026-08-14T03:30:00Z"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamSessionOpenedV1.from_dict(fixture)

    for path in (("outer",), ("initialFrame",)):
        fixture = fixture_dict()
        if path == ("outer",):
            fixture["legacyReplySubject"] = "forbidden"
        else:
            fixture["initialFrame"]["legacyCursor"] = 1
        with pytest.raises(BindingValidationError, match="schema violation"):
            StreamSessionOpenedV1.from_dict(fixture)


def test_unknown_official_stream_response_field_and_duplicate_json_key_rejected() -> None:
    fixture = fixture_dict()
    fixture["initialFrame"]["canonicalStreamResponse"]["task"]["legacyState"] = "working"
    frame = fixture["initialFrame"]
    # Recomputing a digest is intentionally unnecessary: ProtoJSON must fail first.
    frame["payloadDigest"] = frame["payloadDigest"]
    with pytest.raises(BindingValidationError, match="official StreamResponse"):
        StreamSessionOpenedV1.from_dict(fixture)

    duplicate = b'{"schemaVersion":"1.0","schemaVersion":"2.0"}'
    with pytest.raises(BindingValidationError, match="duplicate JSON key"):
        StreamSessionOpenedV1.from_json_bytes(duplicate)


def test_mutating_frame_core_requires_new_payload_digest() -> None:
    fixture = fixture_dict()
    original = StreamSessionOpenedV1.from_dict(fixture).initial_frame
    changed = deepcopy(fixture["initialFrame"])
    changed["eventSeq"] = 8

    with pytest.raises(BindingValidationError, match="payloadDigest does not match"):
        StreamSessionFrameV1.from_dict(changed)
    assert original.event_seq == 7


def test_boolean_values_cannot_impersonate_integer_sequences() -> None:
    opened = StreamSessionOpenedV1.from_dict(fixture_dict())
    frame = opened.initial_frame

    with pytest.raises(BindingValidationError, match="sequence"):
        replace(frame, sequence=False)
    with pytest.raises(BindingValidationError, match="eventSeq"):
        replace(frame, event_seq=False)
    with pytest.raises(BindingValidationError, match="final must be boolean"):
        replace(frame, final=0)
    with pytest.raises(BindingValidationError, match="snapshotEventSeq"):
        replace(opened, snapshot_event_seq=False)
