"""RED regressions for transitive C1-1 provenance and boundary immutability."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import nacl.signing
import nkeys
import pytest
from a2a.utils.errors import InvalidParamsError

from a2amesh.bindings.nats_v1 import AuthContext as WireAuthContext
from a2amesh.bindings.nats_v1 import (
    BindingAuthVerifier,
    BindingError,
    BindingRequestEnvelope,
    BindingValidationError,
    StreamControlAuthVerifier,
    StreamControlEnvelopeV1,
    sign_request_envelope,
    sign_stream_control_envelope,
)
from a2amesh.bindings.nats_v1.stream_control import (
    StreamAckRequestV1,
    StreamCloseRequestV1,
    StreamOpenDigestContextV1,
)
from a2amesh.bindings.nats_v1.transport import (
    V1NatsServer,
    _safe_a2a_error_fields,
    _safe_binding_error_fields,
)
from a2amesh.identity import AuthContext as LegacyAuthContext
from a2amesh.identity import (
    AuthContextVerifier,
    Principal,
    SignerPolicy,
    nkey_public_key,
    sign_auth_context,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "a2a_v1"
NOW = datetime(2026, 8, 14, 3, 1, tzinfo=UTC)


class MemoryReplayGuard:
    async def claim(
        self,
        *,
        principal_id: str,
        target_agent_id: str,
        request_id: str,
        expires_at: datetime,
    ) -> bool:
        del principal_id, target_agent_id, request_id, expires_at
        return True


def make_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    return nkeys.from_seed(
        nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    )


def legacy_policy(
    signer: str,
    *,
    methods: set[str] | frozenset[str] = frozenset({"allowed"}),
) -> SignerPolicy:
    bound = Principal("agent:caller", "agent", "cred", 0)
    return SignerPolicy(
        principal_ids={bound.id},
        methods=methods,
        subjects={"subject"},
        principal_bindings={bound.id: bound},
    )


def legacy_context(method: str, request_id: str = "request-1") -> LegacyAuthContext:
    return LegacyAuthContext(
        principal_id="agent:caller",
        credential_id="cred",
        method=method,
        issuer="issuer",
        subject="subject",
        issued_at=100,
        expires_at=200,
        request_id=request_id,
        target_agent_id="worker",
        alias_generation=0,
    )


class MutableClaim(str):
    def __new__(cls, value: str, accepted: str) -> MutableClaim:
        result = str.__new__(cls, value)
        result.accepted = accepted
        return result

    def __hash__(self) -> int:
        return str.__hash__(self)

    def __eq__(self, other: object) -> bool:
        return other == self.accepted


class MutableCredential:
    def __init__(self, accepted: str) -> None:
        self.accepted = accepted

    def __eq__(self, other: object) -> bool:
        return other == self.accepted


class ExplodingHashString(str):
    def __hash__(self) -> int:
        raise RuntimeError("hash explosion")


class MutablePrefix(str):
    accepted_prefix: str

    def __new__(cls, value: str, accepted_prefix: str) -> MutablePrefix:
        result = str.__new__(cls, value)
        result.accepted_prefix = accepted_prefix
        return result

    def startswith(self, prefix: object, *args: object) -> bool:
        del prefix, args
        return self.accepted_prefix == "allowed"


class UnknownDerived(InvalidParamsError):
    pass


@pytest.mark.parametrize(
    "value",
    [
        MutableClaim("forged-method", "allowed"),
        MutableClaim("forged-subject", "subject"),
    ],
)
def test_signer_policy_rejects_str_subclass_claims(value: str) -> None:
    with pytest.raises(ValueError, match="collection of strings"):
        SignerPolicy(
            principal_ids={"agent:caller"},
            methods={value},
            subjects={"subject"},
            principal_bindings={
                "agent:caller": Principal("agent:caller", "agent", "cred")
            },
        )


def test_principal_rejects_mutable_credential_object() -> None:
    with pytest.raises(ValueError, match="credential_id"):
        Principal("agent:caller", "agent", MutableCredential("credential-a"))  # type: ignore[arg-type]


def test_auth_context_verifier_policy_map_cannot_be_replaced() -> None:
    key_pair = make_key_pair()
    signer = nkey_public_key(key_pair)
    initial = legacy_policy(signer)
    expanded = legacy_policy(signer, methods={"allowed", "forged"})
    verifier = AuthContextVerifier({signer: initial})

    with pytest.raises((TypeError, AttributeError)):
        verifier.signer_policies[signer] = expanded  # type: ignore[index]

    context = legacy_context("forged")
    proof = sign_auth_context(context, key_pair)
    with pytest.raises(ValueError, match="authentication method"):
        verifier.verify(context, proof, expected_target="worker", now=110)


def test_nats_verifier_policy_snapshot_cannot_be_replaced() -> None:
    verifier = BindingAuthVerifier(
        {"signer": legacy_policy("signer")},
        MemoryReplayGuard(),
    )
    with pytest.raises(TypeError):
        verifier._signer_policies["forged"] = legacy_policy("forged")  # type: ignore[index]


def test_verifier_policy_snapshot_attribute_cannot_be_replaced() -> None:
    legacy_verifier = AuthContextVerifier({"signer": legacy_policy("signer")})
    nats_verifier = BindingAuthVerifier(
        {"signer": legacy_policy("signer")},
        MemoryReplayGuard(),
    )
    for verifier in (legacy_verifier, nats_verifier):
        with pytest.raises(AttributeError):
            verifier._signer_policies = {"forged": legacy_policy("forged")}  # type: ignore[attr-defined]
    stream_verifier = StreamControlAuthVerifier(
        {"signer": legacy_policy("signer")},
        MemoryReplayGuard(),
    )
    with pytest.raises(AttributeError):
        stream_verifier._common = nats_verifier  # type: ignore[assignment]


def test_auth_context_verifier_rejects_mutable_claim_before_it_can_mutate() -> None:
    mutable_method = MutableClaim("forged-method", "not-authorized")
    with pytest.raises(ValueError, match="collection of strings"):
        SignerPolicy(
            principal_ids={"agent:caller"},
            methods={mutable_method},
            subjects={"subject"},
            principal_bindings={
                "agent:caller": Principal("agent:caller", "agent", "cred")
            },
        )


def test_safe_binding_mapper_is_total_for_hostile_string_subclass() -> None:
    assert _safe_binding_error_fields(ExplodingHashString("UnknownError"), "secret") == (
        "InternalError",
        "canonical application error",
    )


def test_safe_a2a_mapper_uses_exact_official_types() -> None:
    assert _safe_a2a_error_fields(UnknownDerived(message="secret")) == (
        "InternalError",
        "canonical application error",
    )


def test_binding_error_requires_exact_bool_retryable() -> None:
    with pytest.raises(BindingValidationError, match="retryable"):
        BindingError("InternalError", "safe", 1)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_nats_verifier_rejects_bool_expected_generation() -> None:
    data = json.loads((FIXTURES / "nats_send_message_request.json").read_text())
    unsigned = BindingRequestEnvelope.from_dict(data)
    key_pair = make_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_request_envelope(unsigned, key_pair)
    bound = Principal("a2a:cli-buildbot", "a2a", "cli-buildbot", 0)
    verifier = BindingAuthVerifier(
        {
            signer: SignerPolicy(
                {bound.id}, {"a2a-bearer"}, {"cli-buildbot"}, {bound.id: bound}
            )
        },
        MemoryReplayGuard(),
    )
    with pytest.raises(BindingValidationError, match="generation"):
        await verifier.verify(
            signed,
            received_subject="a2a.v1.rpc.windows-a",
            connection_public_key=signer,
            expected_target_agent_id="windows-a",
            expected_caller_agent_id="linux-gateway",
            expected_caller_instance_id="gateway-01",
            allowed_reply_prefix="_INBOX.a2amesh.linux-gateway.",
            active_config_generation=True,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_stream_verifier_rejects_bool_expected_generation() -> None:
    data = json.loads((FIXTURES / "nats_stream_control_ack_envelope.json").read_text())
    unsigned = StreamControlEnvelopeV1.from_dict(data)
    key_pair = make_key_pair()
    signer = nkey_public_key(key_pair)
    signed = sign_stream_control_envelope(unsigned, key_pair)
    bound = Principal("a2a:cli-buildbot", "a2a", "cli-buildbot", 0)
    verifier = StreamControlAuthVerifier(
        {
            signer: SignerPolicy(
                {bound.id}, {"a2a-bearer"}, {"cli-buildbot"}, {bound.id: bound}
            )
        },
        MemoryReplayGuard(),
    )
    with pytest.raises(BindingValidationError, match="generation"):
        await verifier.verify(
            signed,
            expected_operation=unsigned.operation,
            received_subject=unsigned.operation.subject,
            connection_public_key=signer,
            expected_target_agent_id="windows-a",
            expected_caller_agent_id="linux-gateway",
            expected_caller_instance_id="gateway-01",
            allowed_reply_prefix="_INBOX.a2amesh.linux-gateway.",
            expected_config_generation=True,
            now=datetime(2026, 8, 14, 3, 26, tzinfo=UTC),
        )


def test_wire_envelope_rejects_legacy_auth_context_structurally() -> None:
    data = json.loads((FIXTURES / "nats_send_message_request.json").read_text())
    envelope = BindingRequestEnvelope.from_dict(data)
    legacy = legacy_context("a2a-bearer")
    with pytest.raises(BindingValidationError, match="authContext"):
        replace(envelope, auth_context=legacy)


def test_stream_control_rejects_legacy_auth_context_structurally() -> None:
    data = json.loads((FIXTURES / "nats_stream_control_ack_envelope.json").read_text())
    envelope = StreamControlEnvelopeV1.from_dict(data)
    legacy = legacy_context("a2a-bearer")
    with pytest.raises(BindingValidationError, match="authContext"):
        replace(envelope, auth_context=legacy)


def test_stream_envelope_rejects_mutable_reply_subject() -> None:
    data = json.loads((FIXTURES / "nats_stream_control_ack_envelope.json").read_text())
    envelope = StreamControlEnvelopeV1.from_dict(data)
    with pytest.raises(BindingValidationError, match="replySubject"):
        replace(
            envelope,
            reply_subject=MutablePrefix("_INBOX.a2amesh.attacker.bad", "allowed"),
        )


def test_stream_payloads_and_digest_context_reject_mutable_strings() -> None:
    with pytest.raises(BindingValidationError, match="streamSessionId"):
        StreamAckRequestV1(
            stream_session_id=MutableClaim("session", "session"),
            stream_open_id="open-1",
            sequence=1,
            event_seq=0,
            payload_digest="0" * 64,
        )
    with pytest.raises(BindingValidationError, match="reason"):
        StreamCloseRequestV1(
            stream_session_id="session",
            stream_open_id="open-1",
            reason=MutableClaim("CLOSED", "CLOSED"),
        )
    with pytest.raises(BindingValidationError, match="callerScope"):
        StreamOpenDigestContextV1(
            caller_scope=MutableClaim("scope", "scope"),
            response_core_principal_hash="0" * 64,
            consumer_config_digest="1" * 64,
        )


def test_nats_server_auth_reference_cannot_be_replaced() -> None:
    server = V1NatsServer(
        None,  # type: ignore[arg-type]
        agent_id="worker",
        application=object(),
        signer_policies={"signer": legacy_policy("signer")},
        replay_guard=MemoryReplayGuard(),
        identity_resolver=object(),
        active_config_generation=1,
    )
    with pytest.raises(AttributeError):
        server.auth = BindingAuthVerifier(  # type: ignore[assignment]
            {"forged": legacy_policy("forged")},
            MemoryReplayGuard(),
        )


def test_wire_auth_context_type_is_not_confused_with_legacy_context() -> None:
    assert WireAuthContext is not LegacyAuthContext
    assert asyncio.iscoroutinefunction(MemoryReplayGuard().claim)
