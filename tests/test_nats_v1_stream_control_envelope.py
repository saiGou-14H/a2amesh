"""Wire and canonical-signing contracts for NATS stream control envelopes."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from a2amesh.bindings.nats_v1 import (
    BindingRequestEnvelope,
    BindingValidationError,
    StreamAckRequestV1,
    StreamCloseRequestV1,
    StreamControlEnvelopeV1,
    StreamControlOperation,
    StreamOpenRequestV1,
    canonical_signing_bytes,
)
from a2amesh.identity import verify_nkey_signature

FIXTURES = Path(__file__).parent / "fixtures" / "a2a_v1"
CONTRACT = json.loads(
    (FIXTURES / "nats_stream_control_envelope_contract.json").read_text()
)
FILES = {
    "open": "nats_stream_control_open_envelope.json",
    "ack": "nats_stream_control_ack_envelope.json",
    "close": "nats_stream_control_close_envelope.json",
}
EXPECTED = {
    "open": (StreamControlOperation.OPEN, StreamOpenRequestV1),
    "ack": (StreamControlOperation.ACK, StreamAckRequestV1),
    "close": (StreamControlOperation.CLOSE, StreamCloseRequestV1),
}


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / FILES[name]).read_text())


@pytest.mark.parametrize("name", ["open", "ack", "close"])
def test_fixed_signed_envelope_roundtrip_and_operation_payload_binding(name: str) -> None:
    data = fixture(name)
    envelope = StreamControlEnvelopeV1.from_dict(data)
    operation, payload_type = EXPECTED[name]

    assert envelope.to_dict() == data
    assert StreamControlEnvelopeV1.from_json_bytes(envelope.to_json_bytes()) == envelope
    assert envelope.operation is operation
    assert envelope.expected_subject == operation.subject
    assert isinstance(envelope.payload, payload_type)
    assert envelope.stream_open_id == envelope.payload.stream_open_id


@pytest.mark.parametrize("name", ["open", "ack", "close"])
def test_frozen_rfc8785_wire_and_signing_contract_has_a_valid_nkey_signature(
    name: str,
) -> None:
    envelope = StreamControlEnvelopeV1.from_dict(fixture(name))
    expected = CONTRACT["operations"][name]
    signing = canonical_signing_bytes(envelope)
    wire = rfc8785.dumps(envelope.to_dict())

    assert CONTRACT["canonicalization"] == "RFC8785"
    assert CONTRACT["excludedFields"] == ["authProof.signature"]
    assert len(signing) == expected["signingLength"]
    assert hashlib.sha256(signing).hexdigest() == expected["signingSha256"]
    assert len(wire) == expected["wireLength"]
    assert hashlib.sha256(wire).hexdigest() == expected["wireSha256"]
    assert envelope.auth_proof.signer == expected["signer"]
    assert envelope.auth_proof.signature == expected["signature"]
    assert envelope.operation.value == expected["operation"]
    assert envelope.expected_subject == expected["subject"]
    assert b'"signature"' not in signing
    assert b'"algorithm":"nkey-ed25519"' in signing
    verify_nkey_signature(
        envelope.auth_proof.signer,
        signing,
        envelope.auth_proof.signature,
    )


def test_only_signature_is_excluded_from_control_signing_bytes() -> None:
    envelope = StreamControlEnvelopeV1.from_dict(fixture("ack"))
    changed_signature = replace(
        envelope,
        auth_proof=replace(envelope.auth_proof, signature="different-signature"),
    )
    changed_signer = replace(
        envelope,
        auth_proof=replace(envelope.auth_proof, signer="different-signer"),
    )
    changed_algorithm_data = envelope.to_dict()
    changed_algorithm_data["authProof"]["algorithm"] = "different-algorithm"

    assert canonical_signing_bytes(changed_signature) == canonical_signing_bytes(envelope)
    assert canonical_signing_bytes(changed_signer) != canonical_signing_bytes(envelope)
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamControlEnvelopeV1.from_dict(changed_algorithm_data)


def test_outer_fields_auth_context_and_payload_are_covered_by_signature_input() -> None:
    data = fixture("ack")
    original = canonical_signing_bytes(StreamControlEnvelopeV1.from_dict(data))
    mutations: list[tuple[str, object]] = [
        ("requestId", "req-stream-ack-002"),
        ("configGeneration", 43),
        ("callerAgentId", "other-gateway"),
        ("targetAgentId", "windows-b"),
        ("sentAt", "2026-08-14T03:25:01Z"),
        ("deadlineAt", "2026-08-14T03:30:01Z"),
        ("replySubject", "_INBOX.a2amesh.linux-gateway.ctrl-ack02"),
    ]
    for field, value in mutations:
        candidate = deepcopy(data)
        candidate[field] = value
        assert canonical_signing_bytes(StreamControlEnvelopeV1.from_dict(candidate)) != original

    nested_mutations = [
        ("authContext", "principalId", "a2a:other"),
        ("authContext", "credentialId", "other-credential"),
        ("authContext", "method", "other-method"),
        ("authContext", "issuer", "other-issuer"),
        ("authContext", "subject", "other-subject"),
        ("payload", "sequence", 2),
        ("payload", "eventSeq", 9),
        ("payload", "payloadDigest", "a" * 64),
    ]
    for section, field, value in nested_mutations:
        candidate = deepcopy(data)
        candidate[section][field] = value
        assert canonical_signing_bytes(StreamControlEnvelopeV1.from_dict(candidate)) != original

    instance = deepcopy(data)
    instance["callerInstanceId"] = "gateway-02"
    assert canonical_signing_bytes(StreamControlEnvelopeV1.from_dict(instance)) != original

    stream_id = deepcopy(data)
    stream_id["streamOpenId"] = "stream-open-other"
    stream_id["payload"]["streamOpenId"] = "stream-open-other"
    assert canonical_signing_bytes(StreamControlEnvelopeV1.from_dict(stream_id)) != original


def test_operation_payload_type_mismatch_and_cross_layer_identity_mismatch_fail() -> None:
    operation_mismatch = fixture("open")
    operation_mismatch["operation"] = "StreamSessionAck"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamControlEnvelopeV1.from_dict(operation_mismatch)

    stream_id_mismatch = fixture("ack")
    stream_id_mismatch["streamOpenId"] = "other-open"
    with pytest.raises(BindingValidationError, match="streamOpenId mismatch"):
        StreamControlEnvelopeV1.from_dict(stream_id_mismatch)

    caller_mismatch = fixture("open")
    caller_mismatch["callerInstanceId"] = "gateway-02"
    with pytest.raises(BindingValidationError, match="callerInstanceId mismatch"):
        StreamControlEnvelopeV1.from_dict(caller_mismatch)

    generation_mismatch = fixture("open")
    generation_mismatch["configGeneration"] = 43
    with pytest.raises(BindingValidationError, match="configGeneration mismatch"):
        StreamControlEnvelopeV1.from_dict(generation_mismatch)


def test_control_operation_cannot_enter_official_a2a_request_envelope() -> None:
    with pytest.raises(BindingValidationError, match="schema violation"):
        BindingRequestEnvelope.from_dict(fixture("open"))


def test_control_envelope_rejects_unknown_fields_boolean_generation_and_duplicate_keys() -> None:
    unknown = fixture("ack")
    unknown["callerPrincipal"] = "forbidden"
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamControlEnvelopeV1.from_dict(unknown)

    boolean_generation = fixture("ack")
    boolean_generation["configGeneration"] = True
    with pytest.raises(BindingValidationError, match="schema violation"):
        StreamControlEnvelopeV1.from_dict(boolean_generation)

    duplicate = b'{"operation":"StreamSessionAck","operation":"StreamSessionClose"}'
    with pytest.raises(BindingValidationError, match="duplicate JSON key"):
        StreamControlEnvelopeV1.from_json_bytes(duplicate)
