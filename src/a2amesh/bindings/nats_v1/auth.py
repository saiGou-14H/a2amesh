"""RFC 8785 and NKey authentication for NATS v1 request envelopes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Protocol

import nkeys
import rfc8785
from google.protobuf.json_format import SerializeToJsonError

from a2amesh.identity import (
    Principal,
    SignerPolicy,
    nkey_public_key,
    sign_nkey,
    verify_nkey_signature,
)

from .envelope import (
    AuthContext,
    AuthProof,
    BindingRequestEnvelope,
    BindingValidationError,
)

AUTH_ALGORITHM = "nkey-ed25519"
RPC_SUBJECT_PREFIX = "a2a.v1.rpc."
_REPLY_PREFIX = re.compile(r"^_INBOX\.a2amesh\.[A-Za-z0-9_-]+\.$")
_JSON_SAFE_MAX = 9_007_199_254_740_991


class SignedBindingEnvelope(Protocol):
    @property
    def request_id(self) -> str: ...

    @property
    def caller_instance_id(self) -> str: ...

    @property
    def config_generation(self) -> int: ...

    @property
    def caller_agent_id(self) -> str: ...

    @property
    def auth_context(self) -> AuthContext: ...

    @property
    def auth_proof(self) -> AuthProof: ...

    @property
    def target_agent_id(self) -> str: ...

    @property
    def sent_at(self) -> datetime: ...

    @property
    def deadline_at(self) -> datetime: ...

    @property
    def reply_subject(self) -> str: ...

    def signing_payload_dict(self) -> dict[str, object]: ...


class RequestReplayGuard(Protocol):
    """Durably claim a verified request ID before Core claim/dispatch."""

    async def claim(
        self,
        *,
        principal_id: str,
        target_agent_id: str,
        request_id: str,
        expires_at: datetime,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class VerifiedBindingIdentity:
    principal: Principal
    signer: str
    request_id: str

    @property
    def principal_id(self) -> str:
        return self.principal.id

    @property
    def credential_id(self) -> str | None:
        return self.principal.credential_id


def canonical_signing_bytes(envelope: SignedBindingEnvelope) -> bytes:
    """RFC 8785 bytes for the envelope excluding only authProof.signature."""
    try:
        return rfc8785.dumps(envelope.signing_payload_dict())
    except (
        rfc8785.CanonicalizationError,
        SerializeToJsonError,
        TypeError,
        UnicodeError,
    ) as exc:
        raise BindingValidationError("envelope cannot be RFC 8785 canonicalized") from exc


def sign_request_envelope(
    envelope: BindingRequestEnvelope,
    key_pair: nkeys.KeyPair,
) -> BindingRequestEnvelope:
    signer = nkey_public_key(key_pair)
    unsigned = replace(
        envelope,
        auth_proof=AuthProof(
            signer=signer,
            algorithm=AUTH_ALGORITHM,
            signature="unsigned",
        ),
    )
    return replace(
        unsigned,
        auth_proof=AuthProof(
            signer=signer,
            algorithm=AUTH_ALGORITHM,
            signature=sign_nkey(canonical_signing_bytes(unsigned), key_pair),
        ),
    )


class BindingAuthVerifier:
    """Fail-closed transport authentication before canonical Core dispatch."""

    __slots__ = (
        "_signer_policies",
        "_replay_guard",
        "_clock_skew",
        "_max_auth_lifetime",
        "_sealed",
    )
    _SEALED_FIELDS = frozenset(
        {
            "_signer_policies",
            "_replay_guard",
            "_clock_skew",
            "_max_auth_lifetime",
            "_sealed",
        }
    )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False) and name in self._SEALED_FIELDS:
            raise AttributeError("BindingAuthVerifier configuration is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        signer_policies: Mapping[str, SignerPolicy],
        replay_guard: RequestReplayGuard,
        *,
        clock_skew_seconds: int = 30,
        max_auth_lifetime_seconds: int = 900,
    ) -> None:
        if not signer_policies:
            raise ValueError("at least one binding signer policy is required")
        if replay_guard is None:
            raise ValueError("a durable request replay guard is required")
        if clock_skew_seconds < 0:
            raise ValueError("clock skew cannot be negative")
        if not 1 <= max_auth_lifetime_seconds <= 900:
            raise ValueError("max AuthContext lifetime must be between 1 and 900 seconds")
        policy_items = list(signer_policies.items())
        if any(type(key) is not str for key, _ in policy_items):
            raise ValueError("binding signer policy keys must be plain strings")
        if any(type(value) is not SignerPolicy for _, value in policy_items):
            raise ValueError("binding signer policy values must be SignerPolicy instances")
        self._signer_policies = MappingProxyType(dict(policy_items))
        self._replay_guard = replay_guard
        self._clock_skew = timedelta(seconds=clock_skew_seconds)
        self._max_auth_lifetime = timedelta(seconds=max_auth_lifetime_seconds)
        self._sealed = True

    async def verify(
        self,
        envelope: BindingRequestEnvelope,
        *,
        received_subject: str,
        connection_public_key: str,
        expected_target_agent_id: str,
        expected_caller_agent_id: str,
        expected_caller_instance_id: str,
        allowed_reply_prefix: str,
        active_config_generation: int,
        now: datetime | None = None,
    ) -> VerifiedBindingIdentity:
        if type(active_config_generation) is not int or not (
            1 <= active_config_generation <= _JSON_SAFE_MAX
        ):
            raise BindingValidationError(
                "expected config generation must be a positive safe integer"
            )
        return await self.verify_subject(
            envelope,
            received_subject=received_subject,
            expected_subject=f"{RPC_SUBJECT_PREFIX}{expected_target_agent_id}",
            connection_public_key=connection_public_key,
            expected_target_agent_id=expected_target_agent_id,
            expected_caller_agent_id=expected_caller_agent_id,
            expected_caller_instance_id=expected_caller_instance_id,
            allowed_reply_prefix=allowed_reply_prefix,
            expected_config_generation=active_config_generation,
            now=now,
        )

    async def verify_subject(
        self,
        envelope: SignedBindingEnvelope,
        *,
        received_subject: str,
        expected_subject: str,
        connection_public_key: str,
        expected_target_agent_id: str,
        expected_caller_agent_id: str,
        expected_caller_instance_id: str,
        allowed_reply_prefix: str,
        expected_config_generation: int,
        now: datetime | None = None,
    ) -> VerifiedBindingIdentity:
        if type(expected_config_generation) is not int or not (
            1 <= expected_config_generation <= _JSON_SAFE_MAX
        ):
            raise BindingValidationError(
                "expected config generation must be a positive safe integer"
            )
        current = datetime.now(UTC) if now is None else now
        if current.tzinfo is None:
            raise BindingValidationError("verification clock must be timezone-aware")
        current = current.astimezone(UTC)

        proof = envelope.auth_proof
        context = envelope.auth_context
        if type(context) is not AuthContext or type(proof) is not AuthProof:
            raise BindingValidationError("binding authContext/authProof type is invalid")
        policy = self._signer_policies.get(proof.signer)
        if proof.algorithm != AUTH_ALGORITHM or policy is None:
            raise BindingValidationError("untrusted binding signer")
        if not proof.signer.startswith("U"):
            raise BindingValidationError("binding signer must be a user NKey")
        if proof.signer != connection_public_key:
            raise BindingValidationError("AuthProof signer does not match NATS connection")
        if context.principal_id not in policy.principal_ids:
            raise BindingValidationError("signer cannot represent this principal")
        bound_principal = policy.principal_bindings.get(context.principal_id)
        if bound_principal is None:
            raise BindingValidationError("signer has no principal binding")
        if context.credential_id != bound_principal.credential_id:
            raise BindingValidationError("credential binding does not match signer policy")
        if context.method not in policy.methods:
            raise BindingValidationError("signer cannot use this authentication method")
        if context.subject not in policy.subjects:
            raise BindingValidationError("signer cannot represent this subject")

        if envelope.target_agent_id != expected_target_agent_id:
            raise BindingValidationError("targetAgentId does not match the receiving agent")
        if received_subject != expected_subject:
            raise BindingValidationError("request arrived on the wrong NATS subject")
        if envelope.caller_agent_id != expected_caller_agent_id:
            raise BindingValidationError("callerAgentId does not match authenticated presence")
        if envelope.caller_instance_id != expected_caller_instance_id:
            raise BindingValidationError("callerInstanceId does not match authenticated presence")
        if not _REPLY_PREFIX.fullmatch(allowed_reply_prefix):
            raise BindingValidationError("allowed reply prefix is invalid")
        if not envelope.reply_subject.startswith(allowed_reply_prefix):
            raise BindingValidationError("replySubject is outside the authenticated caller prefix")
        if envelope.config_generation != expected_config_generation:
            raise BindingValidationError("configGeneration does not match expected generation")

        if context.issued_at > current + self._clock_skew:
            raise BindingValidationError("AuthContext was issued in the future")
        if context.expires_at <= current - self._clock_skew:
            raise BindingValidationError("AuthContext expired")
        if context.expires_at - context.issued_at > self._max_auth_lifetime:
            raise BindingValidationError("AuthContext lifetime exceeds policy")
        if envelope.sent_at > current + self._clock_skew:
            raise BindingValidationError("request sentAt is in the future")
        if envelope.deadline_at <= current - self._clock_skew:
            raise BindingValidationError("request deadline expired")

        try:
            verify_nkey_signature(
                proof.signer,
                canonical_signing_bytes(envelope),
                proof.signature,
            )
        except ValueError as exc:
            raise BindingValidationError("invalid binding AuthProof signature") from exc

        replay_expires_at = min(context.expires_at, envelope.deadline_at)
        claimed = await self._replay_guard.claim(
            principal_id=context.principal_id,
            target_agent_id=envelope.target_agent_id,
            request_id=envelope.request_id,
            expires_at=replay_expires_at,
        )
        if not claimed:
            raise BindingValidationError("binding request replay detected")

        return VerifiedBindingIdentity(
            principal=bound_principal,
            signer=proof.signer,
            request_id=envelope.request_id,
        )
