"""Canonical identity primitives shared by every A2AMesh binding."""

from .auth_context import (
    AuthContext,
    AuthContextVerifier,
    AuthProof,
    SignerPolicy,
    sign_auth_context,
)
from .credentials import BearerCredential, CredentialStore
from .nkey import nkey_public_key, sign_nkey, verify_nkey_signature
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
    "nkey_public_key",
    "sign_auth_context",
    "sign_nkey",
    "verify_nkey_signature",
]
