"""Closed Redis EVALSHA runner for the A0 AuthProof replay claim."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib.resources import files
from typing import Final, Protocol

from .client import RedisNoScriptError
from .key_builder import KeyBuilderError, KeyKind, KeyPart, KeyPartCodec, RedisKeyBuilder

_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_SHA1: Final = re.compile(r"^[0-9a-f]{40}$")
_AUTH_REPLAY_RESOURCE_PACKAGE: Final = "a2amesh.state.scripts"
_AUTH_REPLAY_RESOURCE_NAME: Final = "claim_auth_request.lua"


class AuthReplayClaimError(ValueError):
    """The trusted A0 replay-claim contract or script result is invalid."""


class AuthReplayClaimResult(StrEnum):
    """Closed internal outcomes of the A0 replay claim."""

    CLAIMED = "CLAIMED"
    REPLAYED = "REPLAYED"
    CORRUPT_REPLAY_KEY = "CORRUPT_REPLAY_KEY"


class _RedisCommandExecutor(Protocol):
    async def execute(self, command: str, *args: object) -> object: ...


@dataclass(frozen=True, slots=True)
class AuthReplayClaimRequest:
    """Already-verified AuthProof facts accepted by the A0 script runner.

    Cryptographic validation, signer/operation/target/reply-subject binding, and
    clock-skew calculation happen at the trusted State ingress.  This object
    only carries the safe, derived facts needed to form a replay tombstone.
    """

    signer_hash: KeyPart
    request_id_hash: KeyPart
    auth_proof_digest: str
    config_generation: int
    replay_expires_at_ms: int
    state_now_ms: int

    def __post_init__(self) -> None:
        _require_hash_part(self.signer_hash, "signer_hash")
        _require_hash_part(self.request_id_hash, "request_id_hash")
        _require_sha256(self.auth_proof_digest, "auth_proof_digest")
        _require_positive_integer(self.config_generation, "config_generation")
        _require_nonnegative_integer(self.replay_expires_at_ms, "replay_expires_at_ms")
        _require_nonnegative_integer(self.state_now_ms, "state_now_ms")
        if self.replay_expires_at_ms <= self.state_now_ms:
            raise AuthReplayClaimError("replay expiry must be later than State time")

    @property
    def replay_ttl_ms(self) -> int:
        """Return the trusted relative TTL without accepting caller wall time."""

        self.__post_init__()
        return self.replay_expires_at_ms - self.state_now_ms

    @property
    def argv(self) -> tuple[str, str, str]:
        """Return the exact string-only Lua ABI after validating this request."""

        return (
            self.auth_proof_digest,
            str(self.config_generation),
            str(self.replay_ttl_ms),
        )


@cache
def auth_replay_script_source() -> bytes:
    """Read the packaged immutable A0 Lua source as exact UTF-8 bytes."""

    try:
        source = (
            files(_AUTH_REPLAY_RESOURCE_PACKAGE).joinpath(_AUTH_REPLAY_RESOURCE_NAME).read_bytes()
        )
        source.decode("utf-8")
    except (OSError, UnicodeError):
        raise AuthReplayClaimError("auth replay script resource is unavailable") from None
    if not source or b"\x00" in source:
        raise AuthReplayClaimError("auth replay script resource is invalid")
    return source


@cache
def auth_replay_script_sha1() -> str:
    """Return Redis's non-security source identifier for the exact Lua bytes."""

    return hashlib.sha1(auth_replay_script_source(), usedforsecurity=False).hexdigest()


class AuthReplayClaimRunner:
    """Load and run only the A0 script, retrying only a proven NOSCRIPT miss."""

    def __init__(self, redis: _RedisCommandExecutor, key_builder: RedisKeyBuilder) -> None:
        if not callable(getattr(redis, "execute", None)):
            raise AuthReplayClaimError("redis executor must expose execute")
        if type(key_builder) is not RedisKeyBuilder:
            raise AuthReplayClaimError("key_builder must be RedisKeyBuilder")
        self._redis = redis
        self._key_builder = key_builder
        self._load_lock = asyncio.Lock()
        self._loaded = False

    async def claim(self, request: AuthReplayClaimRequest) -> AuthReplayClaimResult:
        """Claim an AuthProof request ID exactly once across Redis clients.

        A generic Redis failure is deliberately not retried because it cannot
        prove whether the server committed the script.  ``NOSCRIPT`` is the
        sole exception: Redis proves that the EVALSHA did not execute.
        """

        if type(request) is not AuthReplayClaimRequest:
            raise AuthReplayClaimError("request must be AuthReplayClaimRequest")
        request.__post_init__()
        try:
            key = self._key_builder.render(
                KeyKind.AUTH_REPLAY,
                signer_hash=request.signer_hash,
                request_id_hash=request.request_id_hash,
            )
        except KeyBuilderError:
            raise AuthReplayClaimError("auth replay key cannot be rendered") from None
        await self._load_script()
        try:
            result = await self._eval(key, request.argv)
        except RedisNoScriptError:
            await self._load_script(force=True)
            result = await self._eval(key, request.argv)
        return _decode_result(result)

    async def _load_script(self, *, force: bool = False) -> None:
        async with self._load_lock:
            if self._loaded and not force:
                return
            expected = auth_replay_script_sha1()
            loaded = await self._redis.execute("SCRIPT", "LOAD", auth_replay_script_source())
            if _redis_sha1(loaded) != expected:
                raise AuthReplayClaimError("loaded auth replay script identity mismatch")
            self._loaded = True

    async def _eval(self, key: bytes, argv: tuple[str, str, str]) -> object:
        return await self._redis.execute(
            "EVALSHA",
            auth_replay_script_sha1(),
            1,
            key,
            *argv,
        )


def _require_hash_part(value: object, label: str) -> KeyPart:
    if type(value) is not KeyPart or value.codec is not KeyPartCodec.SHA256_BASE64URL:
        raise AuthReplayClaimError(f"{label} must use SHA256_BASE64URL")
    return value


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise AuthReplayClaimError(f"{label} must be lowercase SHA-256 hex")
    return value


def _require_positive_integer(value: object, label: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_SAFE_INTEGER:
        raise AuthReplayClaimError(f"{label} must be a positive JSON-safe integer")
    return value


def _require_nonnegative_integer(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE_INTEGER:
        raise AuthReplayClaimError(f"{label} must be a nonnegative JSON-safe integer")
    return value


def _redis_sha1(value: object) -> str:
    if type(value) is bytes:
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            raise AuthReplayClaimError("loaded auth replay script identity is invalid") from None
    if type(value) is not str or _SHA1.fullmatch(value) is None:
        raise AuthReplayClaimError("loaded auth replay script identity is invalid")
    return value


def _decode_result(value: object) -> AuthReplayClaimResult:
    if type(value) is not int:
        raise AuthReplayClaimError("auth replay script returned invalid result")
    if value == 1:
        return AuthReplayClaimResult.CLAIMED
    if value == 0:
        return AuthReplayClaimResult.REPLAYED
    if value == -1:
        return AuthReplayClaimResult.CORRUPT_REPLAY_KEY
    raise AuthReplayClaimError("auth replay script returned invalid result")
