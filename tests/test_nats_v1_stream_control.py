"""Contract tests for NATS stream open/ack/close control payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from a2amesh.bindings.nats_v1 import (
    StreamAckRequestV1,
    StreamCloseRequestV1,
    StreamControlKind,
    StreamControlResultV1,
    StreamOpenDigestContextV1,
    StreamOpenRequestV1,
    StreamSessionState,
)
from a2amesh.bindings.nats_v1.envelope import BindingValidationError
from a2amesh.core import Operation

FIXTURES = Path(__file__).parent / "fixtures" / "a2a_v1"
CONTRACT = json.loads((FIXTURES / "nats_stream_control_contract.json").read_text())


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"nats_stream_{name}.json").read_text())


def digest_context() -> StreamOpenDigestContextV1:
    data = CONTRACT["digestContext"]
    return StreamOpenDigestContextV1(
        caller_scope=data["callerScope"],
        response_core_principal_hash=data["responseCorePrincipalHash"],
        consumer_config_digest=data["consumerConfigDigest"],
    )


@pytest.mark.parametrize(
    ("name", "parser"),
    [
        ("open_request", StreamOpenRequestV1),
        ("ack_request", StreamAckRequestV1),
        ("close_request", StreamCloseRequestV1),
        ("control_result", StreamControlResultV1),
    ],
)
def test_fixed_control_fixtures_roundtrip_with_frozen_digests(
    name: str, parser: type
) -> None:
    fixture_data = fixture(name)
    parsed = parser.from_dict(fixture_data)
    canonical = parsed.canonical_bytes()
    contract_name = name.removesuffix("_request").removesuffix("_control")
    if contract_name == "control_result":
        contract_name = "result"

    assert parsed.to_dict() == fixture_data
    assert parser.from_json_bytes(canonical) == parsed
    assert len(canonical) == CONTRACT["fixtures"][contract_name]["canonicalLength"]
    assert hashlib.sha256(canonical).hexdigest() == CONTRACT["fixtures"][contract_name][
        "sha256"
    ]


def test_open_request_digest_includes_trusted_non_wire_context() -> None:
    opened = StreamOpenRequestV1.from_dict(fixture("open_request"))
    opened.verify_request_digest(digest_context())
    assert opened.request_digest == CONTRACT["fixtures"]["open"]["requestDigest"]
    assert "callerScope" not in opened.to_dict()
    assert "responseCorePrincipalHash" not in opened.to_dict()
    assert "consumerConfigDigest" not in opened.to_dict()

    changed_contexts = [
        replace(digest_context(), caller_scope="other-scope"),
        replace(digest_context(), response_core_principal_hash="3" * 64),
        replace(digest_context(), consumer_config_digest="4" * 64),
    ]
    for context in changed_contexts:
        with pytest.raises(BindingValidationError, match="requestDigest mismatch"):
            opened.verify_request_digest(context)


def test_open_request_factory_reproduces_fixed_digest() -> None:
    opened = StreamOpenRequestV1.create(
        stream_open_id="stream-open-01H-control",
        operation=Operation.SEND_STREAMING_MESSAGE,
        task_id="task-01H-control",
        caller_instance_id="gateway-01",
        expires_at=datetime(2026, 8, 14, 3, 30, tzinfo=UTC),
        config_generation=42,
        digest_context=digest_context(),
    )
    assert opened.to_dict() == fixture("open_request")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("streamOpenId", "other-open"),
        ("operation", "SubscribeToTask"),
        ("taskId", "other-task"),
        ("callerInstanceId", "other-instance"),
        ("expiresAt", "2026-08-14T03:31:00.000Z"),
        ("configGeneration", 43),
    ],
)
def test_open_wire_mutation_requires_a_new_trusted_digest(
    field: str, value: object
) -> None:
    data = fixture("open_request")
    data[field] = value
    opened = StreamOpenRequestV1.from_dict(data)
    with pytest.raises(BindingValidationError, match="requestDigest mismatch"):
        opened.verify_request_digest(digest_context())


def test_open_schema_rejects_identity_subject_injection_and_noncanonical_time() -> None:
    for forbidden in ("principalId", "callerScope", "replySubject", "deliverySubject"):
        data = fixture("open_request")
        data[forbidden] = "attacker-controlled"
        with pytest.raises(BindingValidationError, match="schema violation"):
            StreamOpenRequestV1.from_dict(data)

    data = fixture("open_request")
    data["expiresAt"] = "2026-08-14T03:30:00Z"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamOpenRequestV1.from_dict(data)

    data = fixture("open_request")
    data["operation"] = "SendMessage"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamOpenRequestV1.from_dict(data)


def test_ack_is_exact_pending_tuple_and_rejects_boolean_sequences() -> None:
    ack = StreamAckRequestV1.from_dict(fixture("ack_request"))
    assert ack.sequence == 1
    assert ack.event_seq == 8

    for field, value in (
        ("sequence", False),
        ("sequence", 0),
        ("eventSeq", False),
        ("payloadDigest", "A" * 64),
    ):
        data = fixture("ack_request")
        data[field] = value
        with pytest.raises(BindingValidationError, match="schema violation"):
            StreamAckRequestV1.from_dict(data)

    data = fixture("ack_request")
    data["principalId"] = "forbidden"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamAckRequestV1.from_dict(data)


def test_close_reason_is_a_bounded_code_not_free_text() -> None:
    close = StreamCloseRequestV1.from_dict(fixture("close_request"))
    assert close.reason == "CALLER_REQUESTED"

    for reason in ("caller requested", "CALLER-REQUESTED", "", "A" * 65):
        data = fixture("close_request")
        data["reason"] = reason
        with pytest.raises(BindingValidationError, match="schema violation"):
            StreamCloseRequestV1.from_dict(data)

    data = fixture("close_request")
    data["subject"] = "_DELIVER.attacker"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamCloseRequestV1.from_dict(data)


def test_control_result_has_exactly_accepted_and_current_state() -> None:
    result = StreamControlResultV1.from_dict(fixture("control_result"))
    assert result.accepted is True
    assert result.current_state is StreamSessionState.ACTIVE

    for field, value in (("accepted", 1), ("currentState", "UNKNOWN")):
        data = fixture("control_result")
        data[field] = value
        with pytest.raises(BindingValidationError, match="schema violation"):
            StreamControlResultV1.from_dict(data)

    data = fixture("control_result")
    data["message"] = "must not leak diagnostics"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamControlResultV1.from_dict(data)


def test_control_subject_mapping_is_literal_and_closed() -> None:
    assert StreamControlKind.OPEN.subject == "a2a.v1.stream.open"
    assert StreamControlKind.ACK.subject == "a2a.v1.stream.ack"
    assert StreamControlKind.CLOSE.subject == "a2a.v1.stream.close"
    assert len({kind.subject for kind in StreamControlKind}) == 3
    assert all("*" not in kind.subject and ">" not in kind.subject for kind in StreamControlKind)


def test_duplicate_json_keys_are_rejected_for_every_control_wire() -> None:
    cases = [
        (StreamOpenRequestV1, b'{"schemaVersion":"1.0","schemaVersion":"2.0"}'),
        (StreamAckRequestV1, b'{"schemaVersion":"1.0","schemaVersion":"2.0"}'),
        (StreamCloseRequestV1, b'{"schemaVersion":"1.0","schemaVersion":"2.0"}'),
        (StreamControlResultV1, b'{"accepted":true,"accepted":false}'),
    ]
    for parser, payload in cases:
        with pytest.raises(BindingValidationError, match="duplicate JSON key"):
            parser.from_json_bytes(payload)


def test_digest_context_rejects_unbounded_or_noncanonical_values() -> None:
    with pytest.raises(BindingValidationError, match="callerScope"):
        replace(digest_context(), caller_scope="attacker.>")
    with pytest.raises(BindingValidationError, match="responseCorePrincipalHash"):
        replace(digest_context(), response_core_principal_hash="sha256:bad")
    with pytest.raises(BindingValidationError, match="consumerConfigDigest"):
        replace(digest_context(), consumer_config_digest="F" * 64)
