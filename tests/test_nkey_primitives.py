"""Strict tests for reusable NKey byte-signature primitives."""

from __future__ import annotations

import nacl.signing
import nkeys
import pytest

from a2amesh.identity import (
    nkey_public_key,
    sign_nkey,
    verify_nkey_signature,
)


def make_user_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    seed = nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    return nkeys.from_seed(seed)


def test_nkey_signature_roundtrip_and_public_key_text() -> None:
    key_pair = make_user_key_pair()
    payload = b'{"canonical":true}'
    signature = sign_nkey(payload, key_pair)

    assert nkey_public_key(key_pair).startswith("U")
    assert "=" not in signature
    verify_nkey_signature(nkey_public_key(key_pair), payload, signature)


def test_nkey_signature_binds_exact_payload_bytes() -> None:
    key_pair = make_user_key_pair()
    signature = sign_nkey(b"original", key_pair)

    with pytest.raises(ValueError, match="signature"):
        verify_nkey_signature(nkey_public_key(key_pair), b"tampered", signature)


def test_noncanonical_or_wrong_length_signature_is_rejected() -> None:
    key_pair = make_user_key_pair()
    public_key = nkey_public_key(key_pair)
    signature = sign_nkey(b"payload", key_pair)

    with pytest.raises(ValueError, match="base64url"):
        verify_nkey_signature(public_key, b"payload", signature + "=")
    with pytest.raises(ValueError, match="signature length"):
        verify_nkey_signature(public_key, b"payload", "eA")


def test_invalid_nkey_checksum_is_rejected() -> None:
    key_pair = make_user_key_pair()
    public_key = nkey_public_key(key_pair)
    replacement = "A" if public_key[-1] != "A" else "B"
    corrupted = public_key[:-1] + replacement

    with pytest.raises(ValueError, match="NKey"):
        verify_nkey_signature(corrupted, b"payload", sign_nkey(b"payload", key_pair))
