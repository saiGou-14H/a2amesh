"""Signed internal AuthContext envelopes for the NATS binding."""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import nacl.exceptions
import nacl.signing
import nkeys

from .principal import Principal


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _decode_nkey_public_key(public_key: str) -> nacl.signing.VerifyKey:
    try:
        raw = base64.b32decode(public_key.encode("ascii") + b"=" * (-len(public_key) % 8))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid NKey encoding") from exc
    if len(raw) != 35:
        raise ValueError("invalid NKey public-key length")
    payload, checksum = raw[:-2], raw[-2:]
    if nkeys.crc16(payload).to_bytes(2, "little") != checksum:
        raise ValueError("invalid NKey checksum")
    if not nkeys.valid_public_prefix_byte(payload[0]):
        raise ValueError("invalid NKey public prefix")
    return nacl.signing.VerifyKey(payload[1:])


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
    """Exact identities and claims one NKey signer may represent."""

    principal_ids: frozenset[str]
    methods: frozenset[str]
    subjects: frozenset[str]


def sign_auth_context(context: AuthContext, key_pair: nkeys.KeyPair) -> AuthProof:
    public = key_pair.public_key
    signer = public.decode("ascii") if isinstance(public, bytes) else public
    signature = key_pair.sign(context.canonical_bytes())
    return AuthProof(signer=signer, algorithm="nkey-ed25519", signature=_b64url(signature))


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
        verify_key = _decode_nkey_public_key(proof.signer)
        try:
            verify_key.verify(context.canonical_bytes(), _b64url_decode(proof.signature))
        except (nacl.exceptions.BadSignatureError, ValueError) as exc:
            raise ValueError("invalid AuthContext signature") from exc
        self._seen[context.request_id] = context.expires_at
        kind = context.principal_id.split(":", 1)[0]
        return Principal(
            context.principal_id,
            kind,
            context.credential_id,
            context.alias_generation,
        )
