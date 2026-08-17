"""Authentication tests for complete NATS v1 request envelopes."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import nacl.signing
import nkeys
import pytest

from a2amesh.bindings.nats_v1 import (
    BindingAuthVerifier,
    BindingRequestEnvelope,
    BindingValidationError,
    canonical_signing_bytes,
    sign_request_envelope,
)
from a2amesh.identity import Principal, SignerPolicy, nkey_public_key

FIXTURE = Path(__file__).parent / "fixtures" / "a2a_v1" / "nats_send_message_request.json"
SIGNING_CONTRACT = (
    Path(__file__).parent / "fixtures" / "a2a_v1" / "nats_request_signing_contract.json"
)
NOW = datetime(2026, 8, 14, 3, 1, tzinfo=UTC)


class MemoryReplayGuard:
    """Test double only; production must inject a durable State-backed guard."""

    def __init__(self) -> None:
        self.seen: set[tuple[str, str, str]] = set()
        self.calls = 0

    async def claim(
        self,
        *,
        principal_id: str,
        target_agent_id: str,
        request_id: str,
        expires_at: datetime,
    ) -> bool:
        del expires_at
        self.calls += 1
        key = (principal_id, target_agent_id, request_id)
        if key in self.seen:
            return False
        self.seen.add(key)
        return True


def make_user_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    seed = nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    return nkeys.from_seed(seed)


def unsigned_fixture() -> BindingRequestEnvelope:
    return BindingRequestEnvelope.from_dict(json.loads(FIXTURE.read_text()))


def policy_for(signer: str, **overrides: Any) -> SignerPolicy:
    values = {
        "principal_ids": frozenset({"a2a:cli-buildbot"}),
        "methods": frozenset({"a2a-bearer"}),
        "subjects": frozenset({"cli-buildbot"}),
        "principal_bindings": {
            "a2a:cli-buildbot": Principal(
                "a2a:cli-buildbot", "a2a", "cli-buildbot", 0
            )
        },
    }
    values.update(overrides)
    if "principal_bindings" not in overrides:
        values["principal_bindings"] = {
            principal_id: Principal(
                principal_id,
                principal_id.split(":", 1)[0],
                "cli-buildbot",
                0,
            )
            for principal_id in values["principal_ids"]
        }
    return SignerPolicy(**values)


def verify_kwargs(signer: str) -> dict[str, Any]:
    return {
        "received_subject": "a2a.v1.rpc.windows-a",
        "connection_public_key": signer,
        "expected_target_agent_id": "windows-a",
        "expected_caller_agent_id": "linux-gateway",
        "expected_caller_instance_id": "gateway-01",
        "allowed_reply_prefix": "_INBOX.a2amesh.linux-gateway.",
        "active_config_generation": 42,
        "now": NOW,
    }


@pytest.mark.asyncio
async def test_signed_envelope_verifies_and_claims_replay_after_signature() -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    guard = MemoryReplayGuard()
    verifier = BindingAuthVerifier({signer: policy_for(signer)}, guard)

    identity = await verifier.verify(signed, **verify_kwargs(signer))

    assert identity.principal == Principal(
        "a2a:cli-buildbot", "a2a", credential_id="cli-buildbot"
    )
    assert identity.principal_id == "a2a:cli-buildbot"
    assert identity.credential_id == "cli-buildbot"
    assert identity.signer == signer
    assert guard.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [True, 1.0])
async def test_shared_verifier_rejects_non_exact_generation_before_replay(
    generation: object,
) -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    unsigned = replace(unsigned_fixture(), config_generation=1)
    signed = sign_request_envelope(unsigned, key_pair)
    guard = MemoryReplayGuard()
    verifier = BindingAuthVerifier({signer: policy_for(signer)}, guard)
    kwargs = verify_kwargs(signer)
    kwargs["active_config_generation"] = generation

    with pytest.raises(BindingValidationError, match="generation"):
        await verifier.verify(signed, **kwargs)
    assert guard.calls == 0


@pytest.mark.asyncio
async def test_signed_envelope_credential_claim_must_match_server_binding() -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    guard = MemoryReplayGuard()
    verifier = BindingAuthVerifier(
        {
            signer: policy_for(
                signer,
                principal_bindings={
                    "a2a:cli-buildbot": Principal(
                        "a2a:cli-buildbot", "a2a", "credential-from-state", 7
                    )
                },
            )
        },
        guard,
    )

    with pytest.raises(BindingValidationError, match="credential binding"):
        await verifier.verify(signed, **verify_kwargs(signer))
    assert guard.calls == 0


@pytest.mark.asyncio
async def test_verified_nats_identity_uses_server_bound_alias_generation() -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    bound = Principal("a2a:cli-buildbot", "a2a", "cli-buildbot", 7)
    verifier = BindingAuthVerifier(
        {
            signer: policy_for(
                signer,
                principal_bindings={bound.id: bound},
            )
        },
        MemoryReplayGuard(),
    )

    identity = await verifier.verify(signed, **verify_kwargs(signer))

    assert identity.principal == bound


@pytest.mark.asyncio
async def test_payload_tampering_fails_before_replay_claim() -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    tampered_data = signed.to_dict()
    tampered_data["payload"]["message"]["parts"][0]["text"] = "tampered"
    tampered = BindingRequestEnvelope.from_dict(tampered_data)
    guard = MemoryReplayGuard()
    verifier = BindingAuthVerifier({signer: policy_for(signer)}, guard)

    with pytest.raises(BindingValidationError, match="AuthProof signature"):
        await verifier.verify(tampered, **verify_kwargs(signer))
    assert guard.calls == 0


@pytest.mark.asyncio
async def test_replayed_verified_request_is_rejected_by_injected_guard() -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    guard = MemoryReplayGuard()
    verifier = BindingAuthVerifier({signer: policy_for(signer)}, guard)

    await verifier.verify(signed, **verify_kwargs(signer))
    with pytest.raises(BindingValidationError, match="replay detected"):
        await verifier.verify(signed, **verify_kwargs(signer))
    assert guard.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "expected_error"),
    [
        ({"connection_public_key": "other"}, "NATS connection"),
        ({"received_subject": "a2a.v1.rpc.windows-b"}, "wrong NATS subject"),
        ({"expected_target_agent_id": "windows-b"}, "targetAgentId"),
        ({"expected_caller_agent_id": "other"}, "callerAgentId"),
        ({"expected_caller_instance_id": "other"}, "callerInstanceId"),
        ({"allowed_reply_prefix": "_INBOX.a2amesh.other."}, "replySubject"),
        ({"allowed_reply_prefix": "_INBOX.a2amesh.>."}, "allowed reply prefix"),
        ({"active_config_generation": 41}, "configGeneration"),
    ],
)
async def test_transport_bindings_fail_closed(
    override: dict[str, Any], expected_error: str
) -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    kwargs = verify_kwargs(signer)
    kwargs.update(override)
    verifier = BindingAuthVerifier(
        {signer: policy_for(signer)}, MemoryReplayGuard()
    )

    with pytest.raises(BindingValidationError, match=expected_error):
        await verifier.verify(signed, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_override", "expected_error"),
    [
        ({"principal_ids": frozenset({"a2a:other"})}, "principal"),
        ({"methods": frozenset({"other"})}, "authentication method"),
        ({"subjects": frozenset({"other"})}, "represent this subject"),
    ],
)
async def test_signer_policy_cannot_be_bypassed(
    policy_override: dict[str, Any], expected_error: str
) -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    verifier = BindingAuthVerifier(
        {signer: policy_for(signer, **policy_override)}, MemoryReplayGuard()
    )

    with pytest.raises(BindingValidationError, match=expected_error):
        await verifier.verify(signed, **verify_kwargs(signer))


@pytest.mark.asyncio
async def test_expiry_future_and_lifetime_rules_are_enforced() -> None:
    key_pair = make_user_key_pair()
    signer = nkey_public_key(key_pair)
    verifier = BindingAuthVerifier(
        {signer: policy_for(signer)}, MemoryReplayGuard(), clock_skew_seconds=0
    )

    expired = sign_request_envelope(unsigned_fixture(), key_pair)
    with pytest.raises(BindingValidationError, match="AuthContext expired"):
        await verifier.verify(
            expired,
            **{**verify_kwargs(signer), "now": datetime(2026, 8, 14, 3, 6, tzinfo=UTC)},
        )

    base = unsigned_fixture()
    future_sent = NOW + timedelta(minutes=5)
    future_context = replace(
        base.auth_context,
        issued_at=future_sent,
        expires_at=future_sent + timedelta(minutes=5),
    )
    future = sign_request_envelope(
        replace(
            base,
            auth_context=future_context,
            sent_at=future_sent,
            deadline_at=future_sent + timedelta(minutes=10),
        ),
        key_pair,
    )
    with pytest.raises(BindingValidationError, match="issued in the future"):
        await verifier.verify(future, **verify_kwargs(signer))

    long_context = replace(
        base.auth_context,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=30),
    )
    too_long = sign_request_envelope(replace(base, auth_context=long_context), key_pair)
    with pytest.raises(BindingValidationError, match="lifetime exceeds"):
        await verifier.verify(too_long, **verify_kwargs(signer))


def test_fixed_rfc8785_signing_digest_and_exact_exclusion() -> None:
    contract = json.loads(SIGNING_CONTRACT.read_text())
    envelope = unsigned_fixture()
    canonical = canonical_signing_bytes(envelope)

    assert len(canonical) == contract["canonicalLength"]
    assert hashlib.sha256(canonical).hexdigest() == contract["sha256"]
    assert contract["excludedFields"] == ["authProof.signature"]
    assert b'"signature"' not in canonical
    assert b'"algorithm":"nkey-ed25519"' in canonical
    assert b'"signer":"gateway-service-nkey-public"' in canonical


def test_changing_only_signature_preserves_canonical_bytes_but_signer_does_not() -> None:
    key_pair = make_user_key_pair()
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    changed_signature = replace(
        signed,
        auth_proof=replace(signed.auth_proof, signature="different-signature"),
    )
    changed_signer = replace(
        signed,
        auth_proof=replace(signed.auth_proof, signer="different-signer"),
    )

    assert canonical_signing_bytes(changed_signature) == canonical_signing_bytes(signed)
    assert canonical_signing_bytes(changed_signer) != canonical_signing_bytes(signed)


def test_non_finite_metadata_cannot_enter_rfc8785_signature() -> None:
    envelope = unsigned_fixture()
    cast(Any, envelope.payload).metadata.update({"notFinite": float("nan")})

    with pytest.raises(BindingValidationError, match="RFC 8785 canonicalized"):
        canonical_signing_bytes(envelope)


def test_signature_covers_signer_algorithm_and_every_envelope_field() -> None:
    key_pair = make_user_key_pair()
    signed = sign_request_envelope(unsigned_fixture(), key_pair)
    original = canonical_signing_bytes(signed)
    data = signed.to_dict()

    mutations = {
        "operation": "GetTask",
        "requestId": "req-different",
        "callerInstanceId": "gateway-02",
        "configGeneration": 43,
        "callerAgentId": "other-gateway",
        "targetAgentId": "windows-b",
        "sentAt": "2026-08-14T03:00:01Z",
        "deadlineAt": "2026-08-14T03:29:59Z",
        "replySubject": "_INBOX.a2amesh.linux-gateway.random02",
    }
    for field, value in mutations.items():
        mutated = deepcopy(data)
        mutated[field] = value
        if field == "operation":
            mutated["payload"] = {}
        candidate = BindingRequestEnvelope.from_dict(mutated)
        assert canonical_signing_bytes(candidate) != original

    nested_mutations = [
        ("authContext", "principalId", "a2a:other"),
        ("authContext", "credentialId", "other-credential"),
        ("authContext", "method", "other-method"),
        ("authContext", "issuer", "other-issuer"),
        ("authContext", "subject", "other-subject"),
        ("authProof", "signer", "other-signer"),
    ]
    for section, field, value in nested_mutations:
        mutated = deepcopy(data)
        mutated[section][field] = value
        candidate = BindingRequestEnvelope.from_dict(mutated)
        assert canonical_signing_bytes(candidate) != original

    payload_mutation = deepcopy(data)
    payload_mutation["payload"]["message"]["parts"][0]["text"] = "other text"
    assert canonical_signing_bytes(
        BindingRequestEnvelope.from_dict(payload_mutation)
    ) != original

    stream_one = deepcopy(data)
    stream_one["operation"] = "SendStreamingMessage"
    stream_one["streamOpenId"] = "stream-open-01"
    stream_two = deepcopy(stream_one)
    stream_two["streamOpenId"] = "stream-open-02"
    assert canonical_signing_bytes(
        BindingRequestEnvelope.from_dict(stream_one)
    ) != canonical_signing_bytes(BindingRequestEnvelope.from_dict(stream_two))
