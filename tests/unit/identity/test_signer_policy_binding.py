"""Regression contracts for service-side signer Principal provenance."""

from __future__ import annotations

from dataclasses import replace

import nacl.signing
import nkeys
import pytest

from a2amesh.identity import (
    AuthContext,
    AuthContextVerifier,
    Principal,
    SignerPolicy,
    sign_auth_context,
)


def make_user_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    return nkeys.from_seed(
        nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    )


def test_signer_policy_requires_a_complete_server_side_principal_binding() -> None:
    with pytest.raises(ValueError, match="principal binding"):
        SignerPolicy(
            principal_ids=frozenset({"agent:caller"}),
            methods=frozenset({"nats-nkey"}),
            subjects=frozenset({"caller"}),
        )


def test_signer_policy_binding_keeps_credential_and_alias_provenance_server_side() -> None:
    pair = make_user_key_pair()
    signer = pair.public_key.decode("ascii")
    bound = Principal("agent:caller", "agent", "credential-bound", 7)
    policy = SignerPolicy(
        principal_ids=frozenset({bound.id}),
        methods=frozenset({"nats-nkey"}),
        subjects=frozenset({"caller"}),
        principal_bindings={bound.id: bound},
    )

    assert policy.principal_bindings[bound.id] == bound
    assert policy.principal_bindings[bound.id].credential_id == "credential-bound"
    assert policy.principal_bindings[bound.id].alias_generation == 7
    assert signer.startswith("U")


def test_signer_policy_copies_mutable_claim_sets_before_authorization() -> None:
    methods = {"nats-nkey"}
    subjects = {"caller"}
    bound = Principal("agent:caller", "agent", "credential-bound", 7)
    policy = SignerPolicy(
        principal_ids={bound.id},
        methods=methods,
        subjects=subjects,
        principal_bindings={bound.id: bound},
    )

    methods.add("forged-method")
    subjects.add("forged-subject")

    assert policy.methods == frozenset({"nats-nkey"})
    assert policy.subjects == frozenset({"caller"})
    assert isinstance(policy.methods, frozenset)
    assert isinstance(policy.subjects, frozenset)


def test_principal_and_legacy_auth_context_reject_boolean_alias_generation() -> None:
    with pytest.raises(ValueError, match="alias_generation"):
        Principal("agent:caller", "agent", "credential-bound", True)

    with pytest.raises(ValueError, match="alias_generation"):
        AuthContext(
            principal_id="agent:caller",
            credential_id="credential-bound",
            method="nats-nkey",
            issuer="test",
            subject="caller",
            issued_at=100,
            expires_at=200,
            request_id="request-bool-alias",
            target_agent_id="worker",
            alias_generation=True,
        )


def test_auth_context_verifier_rejects_signed_but_unbound_credential_claim() -> None:
    pair = make_user_key_pair()
    signer = pair.public_key.decode("ascii")
    bound = Principal("agent:caller", "agent", "credential-bound", 7)
    policy = SignerPolicy(
        principal_ids=frozenset({bound.id}),
        methods=frozenset({"nats-nkey"}),
        subjects=frozenset({"caller"}),
        principal_bindings={bound.id: bound},
    )
    context = AuthContext.create(
        bound,
        method="nats-nkey",
        issuer="test",
        subject="caller",
        request_id="request-binding-001",
        target_agent_id="worker",
        now=100,
    )
    forged = replace(context, credential_id="credential-forged")

    with pytest.raises(ValueError, match="credential binding"):
        AuthContextVerifier({signer: policy}).verify(
            forged,
            sign_auth_context(forged, pair),
            expected_target="worker",
            now=110,
        )


def test_auth_context_verifier_rejects_signed_but_unbound_alias_generation() -> None:
    pair = make_user_key_pair()
    signer = pair.public_key.decode("ascii")
    bound = Principal("agent:caller", "agent", "credential-bound", 7)
    policy = SignerPolicy(
        principal_ids=frozenset({bound.id}),
        methods=frozenset({"nats-nkey"}),
        subjects=frozenset({"caller"}),
        principal_bindings={bound.id: bound},
    )
    context = AuthContext.create(
        bound,
        method="nats-nkey",
        issuer="test",
        subject="caller",
        request_id="request-alias-binding-001",
        target_agent_id="worker",
        now=100,
    )
    forged = replace(context, alias_generation=8)

    with pytest.raises(ValueError, match="alias generation binding"):
        AuthContextVerifier({signer: policy}).verify(
            forged,
            sign_auth_context(forged, pair),
            expected_target="worker",
            now=110,
        )
