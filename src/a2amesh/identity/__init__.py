"""Canonical identity primitives shared by every A2AMesh binding."""

from .auth_context import (
    AuthContext,
    AuthContextVerifier,
    AuthProof,
    SignerPolicy,
    sign_auth_context,
)
from .credentials import BearerCredential, CredentialStore
from .principal import AliasRegistry, Principal, issuer_hash

__all__ = [
    "AliasRegistry",
    "AuthContext",
    "AuthContextVerifier",
    "AuthProof",
    "BearerCredential",
    "CredentialStore",
    "Principal",
    "SignerPolicy",
    "issuer_hash",
    "sign_auth_context",
]
