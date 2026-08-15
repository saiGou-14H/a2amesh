"""Contract and negative tests for the custom NATS v1 request envelope."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest

from a2amesh.bindings.nats_v1 import (
    AuthContext,
    AuthProof,
    BindingRequestEnvelope,
    BindingValidationError,
)
from a2amesh.core import OPERATION_SPECS, Operation

FIXTURE = Path(__file__).parent / "fixtures" / "a2a_v1" / "nats_send_message_request.json"
STREAMING = {Operation.SEND_STREAMING_MESSAGE, Operation.SUBSCRIBE_TO_TASK}


def fixture_dict() -> dict:
    return json.loads(FIXTURE.read_text())


def envelope_for(operation: Operation) -> BindingRequestEnvelope:
    sent_at = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)
    return BindingRequestEnvelope(
        operation=operation,
        request_id="req-fixture-001",
        caller_instance_id="gateway-01",
        stream_open_id="stream-open-01" if operation in STREAMING else None,
        config_generation=42,
        caller_agent_id="linux-gateway",
        auth_context=AuthContext(
            principal_id="a2a:cli-buildbot",
            credential_id="cli-buildbot",
            method="a2a-bearer",
            issuer="a2amesh-gateway",
            subject="cli-buildbot",
            issued_at=sent_at,
            expires_at=sent_at + timedelta(minutes=5),
        ),
        auth_proof=AuthProof(
            signer="gateway-service-nkey-public",
            algorithm="nkey-ed25519",
            signature="fixture-signature",
        ),
        target_agent_id="windows-a",
        sent_at=sent_at,
        deadline_at=sent_at + timedelta(minutes=30),
        reply_subject="_INBOX.a2amesh.linux-gateway.random01",
        payload=OPERATION_SPECS[operation].request_type(),
    )


def test_send_message_fixture_parses_as_official_request_and_roundtrips() -> None:
    fixture = fixture_dict()
    envelope = BindingRequestEnvelope.from_dict(fixture)

    assert envelope.operation is Operation.SEND_MESSAGE
    assert envelope.payload.DESCRIPTOR.full_name == "lf.a2a.v1.SendMessageRequest"
    message = cast(Any, envelope.payload).message
    assert message.message_id == "msg-01H-fixture"
    assert message.parts[0].text == "Run the repository tests."
    assert envelope.to_dict() == fixture
    assert json.loads(envelope.to_json_bytes()) == fixture


def test_schema_operation_enum_matches_canonical_registry() -> None:
    schema = json.loads(
        files("a2amesh.schemas")
        .joinpath("nats_binding_request_v1.json")
        .read_text(encoding="utf-8")
    )
    assert set(schema["properties"]["operation"]["enum"]) == {
        operation.value for operation in Operation
    }


def test_every_operation_accepts_only_its_official_request_type() -> None:
    for operation in Operation:
        envelope = envelope_for(operation)
        parsed = BindingRequestEnvelope.from_json_bytes(envelope.to_json_bytes())
        assert isinstance(parsed.payload, OPERATION_SPECS[operation].request_type)

    envelope = envelope_for(Operation.GET_TASK)
    object.__setattr__(
        envelope,
        "payload",
        OPERATION_SPECS[Operation.SEND_MESSAGE].request_type(),
    )
    with pytest.raises(BindingValidationError, match="payload type"):
        envelope.__post_init__()


def test_stream_open_id_is_required_only_for_two_streaming_operations() -> None:
    with pytest.raises(BindingValidationError, match="streamOpenId is required"):
        object.__setattr__(
            envelope := envelope_for(Operation.SEND_STREAMING_MESSAGE),
            "stream_open_id",
            None,
        )
        envelope.__post_init__()

    with pytest.raises(BindingValidationError, match="must be null"):
        object.__setattr__(
            envelope := envelope_for(Operation.SEND_MESSAGE),
            "stream_open_id",
            "unexpected-stream",
        )
        envelope.__post_init__()


def test_legacy_jsonrpc_and_v03_method_are_rejected() -> None:
    fixture = fixture_dict()
    fixture["jsonrpc"] = "2.0"
    with pytest.raises(BindingValidationError, match="schema violation"):
        BindingRequestEnvelope.from_dict(fixture)

    fixture = fixture_dict()
    fixture["operation"] = "message/send"
    with pytest.raises(BindingValidationError, match="schema violation"):
        BindingRequestEnvelope.from_dict(fixture)


def test_unknown_legacy_payload_fields_are_rejected() -> None:
    fixture = fixture_dict()
    fixture["payload"]["message"]["parts"][0]["kind"] = "text"
    with pytest.raises(BindingValidationError, match="official A2A payload"):
        BindingRequestEnvelope.from_dict(fixture)


def test_non_empty_tenant_is_rejected_before_core() -> None:
    fixture = fixture_dict()
    fixture["payload"]["tenant"] = "forbidden-tenant"
    with pytest.raises(BindingValidationError, match="tenant must be empty"):
        BindingRequestEnvelope.from_dict(fixture)


def test_reply_subject_wildcards_and_unknown_envelope_fields_are_rejected() -> None:
    fixture = fixture_dict()
    fixture["replySubject"] = "_INBOX.>"
    with pytest.raises(BindingValidationError, match="replySubject"):
        BindingRequestEnvelope.from_dict(fixture)

    fixture = fixture_dict()
    fixture["callerPrincipal"] = "payload-is-not-identity"
    with pytest.raises(BindingValidationError, match="Additional properties"):
        BindingRequestEnvelope.from_dict(fixture)


def test_signing_payload_excludes_only_signature() -> None:
    envelope = BindingRequestEnvelope.from_dict(fixture_dict())
    signed = envelope.signing_payload_dict()

    assert "signature" not in signed["authProof"]
    assert signed["authProof"]["signer"] == envelope.auth_proof.signer
    assert signed["payload"] == envelope.to_dict()["payload"]


def test_duplicate_json_keys_are_rejected_before_schema_validation() -> None:
    duplicate = b'{"bindingUri":"one","bindingUri":"two"}'
    with pytest.raises(BindingValidationError, match="duplicate JSON key: bindingUri"):
        BindingRequestEnvelope.from_json_bytes(duplicate)


def test_time_and_generation_invariants_fail_closed() -> None:
    envelope = envelope_for(Operation.SEND_MESSAGE)
    with pytest.raises(BindingValidationError, match="configGeneration"):
        object.__setattr__(envelope, "config_generation", 0)
        envelope.__post_init__()

    fixture = deepcopy(fixture_dict())
    fixture["deadlineAt"] = fixture["sentAt"]
    with pytest.raises(BindingValidationError, match="deadlineAt"):
        BindingRequestEnvelope.from_dict(fixture)

    fixture = deepcopy(fixture_dict())
    fixture["authContext"]["issuedAt"] = "2026-08-14T02:00:00Z"
    fixture["authContext"]["expiresAt"] = "2026-08-14T02:59:59Z"
    with pytest.raises(BindingValidationError, match="not valid at sentAt"):
        BindingRequestEnvelope.from_dict(fixture)
