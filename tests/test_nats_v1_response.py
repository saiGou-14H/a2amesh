"""Contract tests for one-shot custom NATS v1 responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from a2amesh.bindings.nats_v1 import (
    BindingError,
    BindingResponseEnvelope,
    BindingValidationError,
)
from a2amesh.core import OPERATION_SPECS, Operation

FIXTURES = Path(__file__).parent / "fixtures" / "a2a_v1"
STREAMING = {Operation.SEND_STREAMING_MESSAGE, Operation.SUBSCRIBE_TO_TASK}


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_success_fixture_parses_as_official_send_message_response() -> None:
    fixture = load_fixture("nats_send_message_response.json")
    response = BindingResponseEnvelope.from_dict(fixture, Operation.SEND_MESSAGE)

    assert response.error is None
    assert response.payload is not None
    assert response.payload.DESCRIPTOR.full_name == "lf.a2a.v1.SendMessageResponse"
    task = cast(Any, response.payload).task
    assert task.id == "task-01H-fixture"
    assert response.to_dict() == fixture
    assert json.loads(response.to_json_bytes()) == fixture


def test_error_fixture_is_operation_independent_and_roundtrips() -> None:
    fixture = load_fixture("nats_task_not_found_response.json")
    for operation in Operation:
        response = BindingResponseEnvelope.from_dict(fixture, operation)
        assert response.payload is None
        assert response.error == BindingError(
            type="TaskNotFoundError",
            message="Task was not found or is not accessible.",
            retryable=False,
        )
        assert response.to_dict() == fixture


def test_all_unary_operations_accept_only_their_official_response_type() -> None:
    for operation, spec in OPERATION_SPECS.items():
        if operation in STREAMING:
            continue
        response = BindingResponseEnvelope(
            operation=operation,
            request_id="req-response-001",
            config_generation=42,
            payload=spec.response_type(),
        )
        parsed = BindingResponseEnvelope.from_json_bytes(
            response.to_json_bytes(), operation
        )
        assert isinstance(parsed.payload, spec.response_type)

    with pytest.raises(BindingValidationError, match="response payload"):
        BindingResponseEnvelope(
            operation=Operation.GET_TASK,
            request_id="req-response-001",
            config_generation=42,
            payload=OPERATION_SPECS[Operation.SEND_MESSAGE].response_type(),
        )


def test_response_rejects_boolean_config_generation() -> None:
    with pytest.raises(BindingValidationError, match="configGeneration"):
        BindingResponseEnvelope(
            operation=Operation.GET_TASK,
            request_id="req-response-001",
            config_generation=True,
            payload=OPERATION_SPECS[Operation.GET_TASK].response_type(),
        )


def test_streaming_success_cannot_use_one_shot_payload() -> None:
    for operation in STREAMING:
        with pytest.raises(BindingValidationError, match="StreamSessionOpenedV1"):
            BindingResponseEnvelope(
                operation=operation,
                request_id="req-response-001",
                config_generation=42,
                payload=OPERATION_SPECS[operation].response_type(),
            )

        fixture = load_fixture("nats_send_message_response.json")
        with pytest.raises(BindingValidationError, match="StreamSessionOpenedV1"):
            BindingResponseEnvelope.from_dict(fixture, operation)


def test_payload_and_error_are_strict_xor() -> None:
    base = load_fixture("nats_send_message_response.json")
    base["error"] = {
        "type": "TaskNotFoundError",
        "message": "not found",
        "retryable": False,
    }
    with pytest.raises(BindingValidationError, match="schema violation"):
        BindingResponseEnvelope.from_dict(base, Operation.SEND_MESSAGE)

    base = load_fixture("nats_send_message_response.json")
    del base["payload"]
    with pytest.raises(BindingValidationError, match="schema violation"):
        BindingResponseEnvelope.from_dict(base, Operation.SEND_MESSAGE)


def test_sequence_final_and_unknown_fields_fail_closed() -> None:
    for field, value in (("sequence", 2), ("final", False)):
        fixture = load_fixture("nats_send_message_response.json")
        fixture[field] = value
        with pytest.raises(BindingValidationError, match="schema violation"):
            BindingResponseEnvelope.from_dict(fixture, Operation.SEND_MESSAGE)

    fixture = load_fixture("nats_send_message_response.json")
    fixture["operation"] = "SendMessage"
    with pytest.raises(BindingValidationError, match="Additional properties"):
        BindingResponseEnvelope.from_dict(fixture, Operation.SEND_MESSAGE)


def test_wrong_operation_and_unknown_proto_fields_are_rejected() -> None:
    fixture = load_fixture("nats_send_message_response.json")
    with pytest.raises(BindingValidationError, match="official A2A response payload"):
        BindingResponseEnvelope.from_dict(fixture, Operation.GET_TASK)

    fixture = load_fixture("nats_send_message_response.json")
    fixture["payload"]["task"]["legacyState"] = "working"
    with pytest.raises(BindingValidationError, match="official A2A response payload"):
        BindingResponseEnvelope.from_dict(fixture, Operation.SEND_MESSAGE)


def test_duplicate_keys_and_invalid_error_shape_are_rejected() -> None:
    duplicate = b'{"requestId":"one","requestId":"two"}'
    with pytest.raises(BindingValidationError, match="duplicate JSON key: requestId"):
        BindingResponseEnvelope.from_json_bytes(duplicate, Operation.GET_TASK)

    with pytest.raises(BindingValidationError, match="error.type"):
        BindingError(type="bad/error", message="bad", retryable=False)
