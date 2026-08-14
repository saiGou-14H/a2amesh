"""Credential verification and transport-to-principal resolution."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from .principal import AliasRegistry, Principal, issuer_hash


@dataclass(frozen=True, slots=True)
class BearerCredential:
    credential_id: str
    principal_id: str
    secret_digest: bytes
    expires_at: float | None = None
    enabled: bool = True


class CredentialStore:
    """Small in-memory credential registry used by gateway instances and tests.

    Production persistence can implement the same API in Redis. Raw secrets are
    never retained; lookup uses the public credential ID and verification uses a
    peppered HMAC with constant-time comparison.
    """

    def __init__(self, pepper: bytes, aliases: AliasRegistry | None = None) -> None:
        if len(pepper) < 32:
            raise ValueError("credential pepper must be at least 32 bytes")
        self._pepper = pepper
        self._bearers: dict[str, BearerCredential] = {}
        self._nkeys: dict[str, tuple[str, str]] = {}
        self.aliases = aliases or AliasRegistry()

    def _digest(self, secret: str) -> bytes:
        return hmac.new(self._pepper, secret.encode("utf-8"), hashlib.sha256).digest()

    def register_bearer(
        self,
        credential_id: str,
        secret: str,
        *,
        principal_id: str | None = None,
        expires_at: float | None = None,
    ) -> str:
        if not credential_id or any(ch in credential_id for ch in ". \t\r\n"):
            raise ValueError("credential ID must be a non-empty token without dots")
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("bearer secret must contain at least 32 bytes")
        principal_id = principal_id or f"a2a:{credential_id}"
        Principal(principal_id, principal_id.split(":", 1)[0])
        existing = self._bearers.get(credential_id)
        if existing and existing.principal_id != principal_id:
            raise ValueError("credential principal is immutable")
        self._bearers[credential_id] = BearerCredential(
            credential_id=credential_id,
            principal_id=principal_id,
            secret_digest=self._digest(secret),
            expires_at=expires_at,
        )
        return f"{credential_id}.{secret}"

    def disable_bearer(self, credential_id: str) -> None:
        credential = self._bearers.get(credential_id)
        if credential is None:
            raise KeyError(credential_id)
        self._bearers[credential_id] = BearerCredential(
            credential_id=credential.credential_id,
            principal_id=credential.principal_id,
            secret_digest=credential.secret_digest,
            expires_at=credential.expires_at,
            enabled=False,
        )

    def resolve_bearer(self, token: str, *, now: float | None = None) -> Principal:
        try:
            credential_id, secret = token.split(".", 1)
        except ValueError as exc:
            raise ValueError("invalid bearer credential format") from exc
        credential = self._bearers.get(credential_id)
        supplied = self._digest(secret)
        # Perform a comparison even for unknown credentials to reduce lookup timing leakage.
        expected = credential.secret_digest if credential else bytes(len(supplied))
        verified = hmac.compare_digest(supplied, expected)
        current = time.time() if now is None else now
        if (
            credential is None
            or not verified
            or not credential.enabled
            or (credential.expires_at is not None and current >= credential.expires_at)
        ):
            raise ValueError("invalid or expired bearer credential")
        principal_id, generation = self.aliases.resolve(credential.principal_id)
        return Principal(principal_id, principal_id.split(":", 1)[0], credential_id, generation)

    def register_nkey(self, public_key: str, agent_id: str) -> None:
        if not public_key.startswith("U"):
            raise ValueError("peer identity must use a user NKey")
        principal_id = f"agent:{agent_id}"
        Principal(principal_id, "agent")
        existing = self._nkeys.get(public_key)
        if existing and existing != (agent_id, principal_id):
            raise ValueError("NKey identity is immutable")
        self._nkeys[public_key] = (agent_id, principal_id)

    def resolve_nkey(self, public_key: str) -> Principal:
        try:
            _agent_id, initial = self._nkeys[public_key]
        except KeyError as exc:
            raise ValueError("unknown NKey") from exc
        principal_id, generation = self.aliases.resolve(initial)
        return Principal(principal_id, principal_id.split(":", 1)[0], public_key, generation)

    def resolve_oauth(self, issuer: str, client_id: str) -> Principal:
        if not issuer.startswith("https://") or not client_id:
            raise ValueError("OAuth issuer and client_id are required")
        initial = f"mcp:{issuer_hash(issuer)}:{client_id}"
        principal_id, generation = self.aliases.resolve(initial)
        return Principal(principal_id, principal_id.split(":", 1)[0], client_id, generation)
