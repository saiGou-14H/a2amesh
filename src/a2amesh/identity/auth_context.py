"""Signed internal AuthContext envelopes for the NATS binding."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType

import nkeys

from .nkey import nkey_public_key, sign_nkey, verify_nkey_signature
from .principal import Principal


@dataclass(frozen=True, slots=True)
class AuthContext:
    principal_id: str
    credential_id: str | None
    method: str
    issuer: str
    subject: str
    issued_at: int
    expires_at: int
    request_id: str
    target_agent_id: str
    alias_generation: int = 0

    @classmethod
    def create(
        cls,
        principal: Principal,
        *,
        method: str,
        issuer: str,
        subject: str,
        request_id: str,
        target_agent_id: str,
        now: int | None = None,
        ttl_seconds: int = 300,
    ) -> AuthContext:
        if ttl_seconds <= 0 or ttl_seconds > 900:
            raise ValueError("AuthContext TTL must be between 1 and 900 seconds")
        issued_at = int(time.time()) if now is None else now
        return cls(
            principal_id=principal.id,
            credential_id=principal.credential_id,
            method=method,
            issuer=issuer,
            subject=subject,
            issued_at=issued_at,
            expires_at=issued_at + ttl_seconds,
            request_id=request_id,
            target_agent_id=target_agent_id,
            alias_generation=principal.alias_generation,
        )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AuthProof:
    signer: str
    algorithm: str
    signature: str


@dataclass(frozen=True, slots=True)
class SignerPolicy:
    """Exact claims and server-side Principal provenance one signer may represent."""

    principal_ids: frozenset[str]
    methods: frozenset[str]
    subjects: frozenset[str]
    principal_bindings: Mapping[str, Principal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bindings = dict(self.principal_bindings)
        if not self.principal_ids or set(bindings) != set(self.principal_ids):
            raise ValueError(
                "signer policy requires a complete principal binding for every principal_id"
            )
        for principal_id, principal in bindings.items():
            if not isinstance(principal, Principal) or principal.id != principal_id:
                raise ValueError("signer policy principal binding does not match its key")
        object.__setattr__(self, "principal_bindings", MappingProxyType(bindings))


def sign_auth_context(context: AuthContext, key_pair: nkeys.KeyPair) -> AuthProof:
    return AuthProof(
        signer=nkey_public_key(key_pair),
        algorithm="nkey-ed25519",
        signature=sign_nkey(context.canonical_bytes(), key_pair),
    )


class AuthContextVerifier:
    """Verify signatures, expiry, target binding, and request replay."""

    def __init__(
        self,
        signer_policies: Mapping[str, SignerPolicy],
        *,
        clock_skew_seconds: int = 30,
    ) -> None:
        if not signer_policies:
            raise ValueError("at least one AuthContext signer policy is required")
        self.signer_policies = dict(signer_policies)
        self.clock_skew_seconds = clock_skew_seconds
        self._seen: dict[str, int] = {}

    def verify(
        self,
        context: AuthContext,
        proof: AuthProof,
        *,
        expected_target: str,
        now: int | None = None,
    ) -> Principal:
        current = int(time.time()) if now is None else now
        self._seen = {key: expiry for key, expiry in self._seen.items() if expiry >= current}
        policy = self.signer_policies.get(proof.signer)
        if proof.algorithm != "nkey-ed25519" or policy is None:
            raise ValueError("untrusted AuthContext signer")
        if context.principal_id not in policy.principal_ids:
            raise ValueError("signer cannot represent this principal")
        bound_principal = policy.principal_bindings.get(context.principal_id)
        if bound_principal is None:
            raise ValueError("signer has no principal binding")
        if context.credential_id != bound_principal.credential_id:
            raise ValueError("credential binding does not match signer policy")
        if context.alias_generation != bound_principal.alias_generation:
            raise ValueError("alias generation binding does not match signer policy")
        if context.method not in policy.methods:
            raise ValueError("signer cannot use this authentication method")
        if context.subject not in policy.subjects:
            raise ValueError("signer cannot represent this subject")
        if context.target_agent_id != expected_target:
            raise ValueError("AuthContext target mismatch")
        if context.issued_at > current + self.clock_skew_seconds:
            raise ValueError("AuthContext was issued in the future")
        if context.expires_at < current - self.clock_skew_seconds:
            raise ValueError("AuthContext expired")
        if context.expires_at <= context.issued_at or context.expires_at - context.issued_at > 900:
            raise ValueError("invalid AuthContext lifetime")
        if context.request_id in self._seen:
            raise ValueError("AuthContext replay detected")
        try:
            verify_nkey_signature(
                proof.signer,
                context.canonical_bytes(),
                proof.signature,
            )
        except ValueError as exc:
            raise ValueError("invalid AuthContext signature") from exc
        self._seen[context.request_id] = context.expires_at
        return bound_principal
