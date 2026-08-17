from __future__ import annotations

import time

import nacl.signing
import nkeys
import pytest

from a2amesh.identity import (
    AliasRegistry,
    AuthContext,
    AuthContextVerifier,
    AuthProof,
    CredentialStore,
    Principal,
    SignerPolicy,
    sign_auth_context,
)


def make_user_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    seed = nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    return nkeys.from_seed(seed)


def test_bearer_credentials_are_independent_and_aliases_are_immutable():
    aliases = AliasRegistry()
    store = CredentialStore(b"p" * 32, aliases)
    secret = "s" * 32
    token = store.register_bearer("buildbot", secret)
    resolved = store.resolve_bearer(token, now=1)
    assert resolved.id == "a2a:buildbot"
    assert resolved.credential_id == "buildbot"

    generation = aliases.add("a2a:buildbot", "system:automation")
    aliased = store.resolve_bearer(token, now=1)
    assert aliased == Principal("system:automation", "system", "buildbot", generation)

    assert aliases.add("a2a:buildbot", "system:automation") == generation
    with pytest.raises(ValueError, match="immutable"):
        aliases.add("a2a:buildbot", "system:other")
    with pytest.raises(ValueError, match="alias source"):
        aliases.add("system:automation", "system:root")

    with pytest.raises(ValueError, match="invalid or expired"):
        store.resolve_bearer("buildbot." + "x" * 32, now=1)
    store.disable_bearer("buildbot")
    with pytest.raises(ValueError, match="invalid or expired"):
        store.resolve_bearer(token, now=1)


def test_nkey_and_oauth_resolve_to_canonical_machine_principals():
    aliases = AliasRegistry()
    store = CredentialStore(b"p" * 32, aliases)
    key_pair = make_user_key_pair()
    public = key_pair.public_key.decode("ascii")
    store.register_nkey(public, "windows-a")
    assert store.resolve_nkey(public).id == "agent:windows-a"
    oauth = store.resolve_oauth("https://auth.example.com", "mcp-client")
    assert oauth.id.startswith("mcp:")
    assert oauth.id.endswith(":mcp-client")


def test_signed_auth_context_binds_principal_target_expiry_and_request_id():
    key_pair = make_user_key_pair()
    signer = key_pair.public_key.decode("ascii")
    context = AuthContext.create(
        Principal("agent:windows-a", "agent", signer),
        method="nats-nkey",
        issuer="a2amesh-peer",
        subject="windows-a",
        request_id="req-1",
        target_agent_id="linux",
        now=100,
        ttl_seconds=60,
    )
    proof = sign_auth_context(context, key_pair)
    policies = {
        signer: SignerPolicy(
            principal_ids=frozenset({"agent:windows-a"}),
            methods=frozenset({"nats-nkey"}),
            subjects=frozenset({"windows-a"}),
            principal_bindings={
                "agent:windows-a": Principal(
                    "agent:windows-a", "agent", signer, 0
                )
            },
        )
    }
    verifier = AuthContextVerifier(policies)
    verified = verifier.verify(context, proof, expected_target="linux", now=110)
    assert verified.id == "agent:windows-a"

    with pytest.raises(ValueError, match="replay"):
        verifier.verify(context, proof, expected_target="linux", now=111)

    another = AuthContext.create(
        Principal("agent:windows-a", "agent", signer),
        method="nats-nkey",
        issuer="a2amesh-peer",
        subject="windows-a",
        request_id="req-2",
        target_agent_id="linux",
        now=100,
        ttl_seconds=60,
    )
    with pytest.raises(ValueError, match="target mismatch"):
        AuthContextVerifier(policies).verify(
            another,
            sign_auth_context(another, key_pair),
            expected_target="windows-b",
            now=110,
        )
    with pytest.raises(ValueError, match="expired"):
        AuthContextVerifier(policies).verify(
            another,
            sign_auth_context(another, key_pair),
            expected_target="linux",
            now=200,
        )

    tampered = AuthProof(proof.signer, proof.algorithm, proof.signature[:-2] + "aa")
    with pytest.raises(ValueError, match="signature"):
        AuthContextVerifier(policies).verify(
            another,
            tampered,
            expected_target="linux",
            now=110,
        )

    forged = AuthContext.create(
        Principal("agent:windows-b", "agent", signer),
        method="nats-nkey",
        issuer="a2amesh-peer",
        subject="windows-a",
        request_id="req-forged",
        target_agent_id="linux",
        now=100,
    )
    with pytest.raises(ValueError, match="cannot represent this principal"):
        AuthContextVerifier(policies).verify(
            forged,
            sign_auth_context(forged, key_pair),
            expected_target="linux",
            now=110,
        )


def test_expired_bearer_is_rejected():
    store = CredentialStore(b"p" * 32)
    token = store.register_bearer("short", "s" * 32, expires_at=time.time() - 1)
    with pytest.raises(ValueError, match="invalid or expired"):
        store.resolve_bearer(token)
