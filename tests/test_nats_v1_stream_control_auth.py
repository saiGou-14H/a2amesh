"""Authentication gates for signed NATS stream control envelopes."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import nacl.signing
import nkeys
import pytest

from a2amesh.bindings.nats_v1 import (
    BindingValidationError,
    StreamAckRequestV1,
    StreamControlAuthVerifier,
    StreamControlEnvelopeV1,
    StreamControlOperation,
    StreamOpenDigestContextV1,
    sign_stream_control_envelope,
)
from a2amesh.identity import SignerPolicy, nkey_public_key

FIXTURES = Path(__file__).parent / "fixtures" / "a2a_v1"
FILES = {
    "open": "nats_stream_control_open_envelope.json",
    "ack": "nats_stream_control_ack_envelope.json",
    "close": "nats_stream_control_close_envelope.json",
}
NOW = datetime(2026, 8, 14, 3, 26, tzinfo=UTC)


class MemoryReplayGuard:
    """Test double only; production must inject a durable State-backed guard."""

    def __init__(self) -> None:
        self.seen: set[tuple[str, str, str]] = set()
        self.calls: list[tuple[str, str, str, datetime]] = []

    async def claim(
        self,
        *,
        principal_id: str,
        target_agent_id: str,
        request_id: str,
        expires_at: datetime,
    ) -> bool:
        self.calls.append((principal_id, target_agent_id, request_id, expires_at))
        key = (principal_id, target_agent_id, request_id)
        if key in self.seen:
            return False
        self.seen.add(key)
        return True


def make_user_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    seed = nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    return nkeys.from_seed(seed)


def envelope(name: str) -> StreamControlEnvelopeV1:
    data = json.loads((FIXTURES / FILES[name]).read_text())
    return StreamControlEnvelopeV1.from_dict(data)


def policy(**overrides: Any) -> SignerPolicy:
    values = {
        "principal_ids": frozenset({"a2a:cli-buildbot"}),
        "methods": frozenset({"a2a-bearer"}),
        "subjects": frozenset({"cli-buildbot"}),
    }
    values.update(overrides)
    return SignerPolicy(**values)


def open_digest_context() -> StreamOpenDigestContextV1:
    return StreamOpenDigestContextV1(
        caller_scope="caller01",
        response_core_principal_hash="1" * 64,
        consumer_config_digest="2" * 64,
    )


def verify_kwargs(
    candidate: StreamControlEnvelopeV1,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "expected_operation": candidate.operation,
        "received_subject": candidate.operation.subject,
        "connection_public_key": candidate.auth_proof.signer,
        "expected_target_agent_id": "windows-a",
        "expected_caller_agent_id": "linux-gateway",
        "expected_caller_instance_id": "gateway-01",
        "allowed_reply_prefix": "_INBOX.a2amesh.linux-gateway.",
        "expected_config_generation": 42,
        "now": NOW,
    }
    if candidate.operation is StreamControlOperation.OPEN:
        values["open_digest_context"] = open_digest_context()
    values.update(overrides)
    return values


def verifier_for(
    candidate: StreamControlEnvelopeV1,
    guard: MemoryReplayGuard,
    **policy_overrides: Any,
) -> StreamControlAuthVerifier:
    return StreamControlAuthVerifier(
        {candidate.auth_proof.signer: policy(**policy_overrides)}, guard
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["open", "ack", "close"])
async def test_frozen_signed_control_envelopes_verify_and_claim_once(name: str) -> None:
    candidate = envelope(name)
    guard = MemoryReplayGuard()
    identity = await verifier_for(candidate, guard).verify(
        candidate, **verify_kwargs(candidate)
    )

    assert identity.principal_id == "a2a:cli-buildbot"
    assert identity.credential_id == "cli-buildbot"
    assert identity.signer == candidate.auth_proof.signer
    assert identity.request_id == candidate.request_id
    assert guard.calls == [
        (
            "a2a:cli-buildbot",
            "windows-a",
            candidate.request_id,
            datetime(2026, 8, 14, 3, 30, tzinfo=UTC),
        )
    ]


@pytest.mark.asyncio
async def test_verified_control_request_replay_is_rejected() -> None:
    candidate = envelope("ack")
    guard = MemoryReplayGuard()
    verifier = verifier_for(candidate, guard)

    await verifier.verify(candidate, **verify_kwargs(candidate))
    with pytest.raises(BindingValidationError, match="replay detected"):
        await verifier.verify(candidate, **verify_kwargs(candidate))
    assert len(guard.calls) == 2


@pytest.mark.asyncio
async def test_payload_or_signature_tampering_fails_before_replay_claim() -> None:
    candidate = envelope("ack")
    payload = candidate.payload
    assert isinstance(payload, StreamAckRequestV1)
    tampered_payload = replace(candidate, payload=replace(payload, sequence=2))
    invalid_signature = replace(
        candidate,
        auth_proof=replace(candidate.auth_proof, signature="invalid-signature"),
    )

    for tampered in (tampered_payload, invalid_signature):
        guard = MemoryReplayGuard()
        with pytest.raises(BindingValidationError, match="AuthProof signature"):
            await verifier_for(tampered, guard).verify(
                tampered, **verify_kwargs(tampered)
            )
        assert guard.calls == []


@pytest.mark.asyncio
async def test_stream_open_requires_exact_trusted_non_wire_digest_context() -> None:
    candidate = envelope("open")
    guard = MemoryReplayGuard()
    verifier = verifier_for(candidate, guard)

    with pytest.raises(BindingValidationError, match="trusted digest context"):
        await verifier.verify(
            candidate,
            **verify_kwargs(candidate, open_digest_context=None),
        )
    with pytest.raises(BindingValidationError, match="requestDigest mismatch"):
        await verifier.verify(
            candidate,
            **verify_kwargs(
                candidate,
                open_digest_context=replace(
                    open_digest_context(), caller_scope="other-scope"
                ),
            ),
        )
    assert guard.calls == []


@pytest.mark.asyncio
async def test_ack_and_close_reject_open_only_digest_context() -> None:
    for name in ("ack", "close"):
        candidate = envelope(name)
        guard = MemoryReplayGuard()
        with pytest.raises(BindingValidationError, match="invalid for ack/close"):
            await verifier_for(candidate, guard).verify(
                candidate,
                **verify_kwargs(
                    candidate,
                    open_digest_context=open_digest_context(),
                ),
            )
        assert guard.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "expected_error"),
    [
        ({"received_subject": "a2a.v1.stream.close"}, "wrong NATS subject"),
        ({"connection_public_key": "other"}, "NATS connection"),
        ({"expected_target_agent_id": "windows-b"}, "targetAgentId"),
        ({"expected_caller_agent_id": "other"}, "callerAgentId"),
        ({"expected_caller_instance_id": "other"}, "callerInstanceId"),
        ({"allowed_reply_prefix": "_INBOX.a2amesh.other."}, "replySubject"),
        ({"allowed_reply_prefix": "_INBOX.a2amesh.>."}, "allowed reply prefix"),
        ({"expected_config_generation": 41}, "configGeneration"),
    ],
)
async def test_transport_and_presence_bindings_fail_closed(
    override: dict[str, Any], expected_error: str
) -> None:
    candidate = envelope("ack")
    guard = MemoryReplayGuard()
    kwargs = verify_kwargs(candidate)
    kwargs.update(override)

    with pytest.raises(BindingValidationError, match=expected_error):
        await verifier_for(candidate, guard).verify(candidate, **kwargs)
    assert guard.calls == []


@pytest.mark.asyncio
async def test_control_operation_is_bound_to_the_selected_handler() -> None:
    candidate = envelope("close")
    guard = MemoryReplayGuard()
    with pytest.raises(BindingValidationError, match="does not match handler"):
        await verifier_for(candidate, guard).verify(
            candidate,
            **verify_kwargs(
                candidate,
                expected_operation=StreamControlOperation.ACK,
            ),
        )
    assert guard.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_override", "expected_error"),
    [
        ({"principal_ids": frozenset({"a2a:other"})}, "principal"),
        ({"methods": frozenset({"other"})}, "authentication method"),
        ({"subjects": frozenset({"other"})}, "represent this subject"),
    ],
)
async def test_stream_control_signer_policy_cannot_be_bypassed(
    policy_override: dict[str, Any], expected_error: str
) -> None:
    candidate = envelope("ack")
    guard = MemoryReplayGuard()
    with pytest.raises(BindingValidationError, match=expected_error):
        await verifier_for(candidate, guard, **policy_override).verify(
            candidate, **verify_kwargs(candidate)
        )
    assert guard.calls == []


@pytest.mark.asyncio
async def test_untrusted_signer_and_expired_control_envelopes_fail_before_replay() -> None:
    candidate = envelope("ack")
    other_key = make_user_key_pair()
    other_signer = nkey_public_key(other_key)

    untrusted_guard = MemoryReplayGuard()
    untrusted = StreamControlAuthVerifier(
        {other_signer: policy()},
        untrusted_guard,
    )
    with pytest.raises(BindingValidationError, match="untrusted binding signer"):
        await untrusted.verify(candidate, **verify_kwargs(candidate))
    assert untrusted_guard.calls == []

    expired_guard = MemoryReplayGuard()
    with pytest.raises(BindingValidationError, match="AuthContext expired"):
        await verifier_for(candidate, expired_guard).verify(
            candidate,
            **verify_kwargs(
                candidate,
                now=datetime(2026, 8, 14, 3, 36, tzinfo=UTC),
            ),
        )
    assert expired_guard.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["open", "ack", "close"])
async def test_ephemeral_signing_roundtrip_uses_connection_user_nkey(name: str) -> None:
    original = envelope(name)
    key_pair = make_user_key_pair()
    signed = sign_stream_control_envelope(original, key_pair)
    signer = nkey_public_key(key_pair)
    guard = MemoryReplayGuard()
    verifier = StreamControlAuthVerifier({signer: policy()}, guard)

    assert signed.auth_proof.signer == signer
    assert signed.auth_proof.signature != original.auth_proof.signature
    identity = await verifier.verify(signed, **verify_kwargs(signed))
    assert identity.signer == signer
    assert len(guard.calls) == 1
