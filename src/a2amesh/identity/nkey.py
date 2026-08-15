"""Reusable NKey Ed25519 byte-signature primitives."""

from __future__ import annotations

import base64
import binascii
import re

import nacl.exceptions
import nacl.signing
import nkeys

_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_NKEY = re.compile(r"^[A-Z2-7]+$")


def nkey_public_key(key_pair: nkeys.KeyPair) -> str:
    public = key_pair.public_key
    return public.decode("ascii") if isinstance(public, bytes) else public


def sign_nkey(payload: bytes, key_pair: nkeys.KeyPair) -> str:
    return _b64url_encode(key_pair.sign(payload))


def verify_nkey_signature(public_key: str, payload: bytes, signature: str) -> None:
    verify_key = _decode_nkey_public_key(public_key)
    decoded_signature = _b64url_decode(signature)
    if len(decoded_signature) != 64:
        raise ValueError("invalid NKey signature length")
    try:
        verify_key.verify(payload, decoded_signature)
    except nacl.exceptions.BadSignatureError as exc:
        raise ValueError("invalid NKey signature") from exc


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not value or not _BASE64URL.fullmatch(value):
        raise ValueError("invalid base64url signature encoding")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64url signature encoding") from exc
    if _b64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url signature encoding")
    return decoded


def _decode_nkey_public_key(public_key: str) -> nacl.signing.VerifyKey:
    if not public_key or not _NKEY.fullmatch(public_key):
        raise ValueError("invalid NKey encoding")
    try:
        raw = base64.b32decode(
            public_key.encode("ascii") + b"=" * (-len(public_key) % 8)
        )
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
