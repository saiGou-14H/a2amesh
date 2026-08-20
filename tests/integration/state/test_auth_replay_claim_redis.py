from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import pytest

from a2amesh.state.client import RedisClient
from a2amesh.state.config import RedisConfig
from a2amesh.state.key_builder import KeyKind, KeyPart, RedisKeyBuilder
from a2amesh.state.script_runner import (
    AuthReplayClaimRequest,
    AuthReplayClaimResult,
    AuthReplayClaimRunner,
    auth_replay_script_sha1,
)

REDIS_URL = os.getenv("A2AMESH_TEST_REDIS_URL")
_DESTRUCTIVE_REDIS = os.getenv("A2AMESH_TEST_REDIS_DESTRUCTIVE") == "1"
_HASH = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_OTHER_HASH = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64

pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="A2AMESH_TEST_REDIS_URL is required for real Redis auth replay integration",
)


@dataclass(frozen=True, slots=True)
class RedisTestContext:
    """One test-only, random mesh namespace and its owned replay keys."""

    client: RedisClient
    key_builder: RedisKeyBuilder

    def replay_key(self, request_id_hash: str = _HASH) -> bytes:
        return self.key_builder.render(
            KeyKind.AUTH_REPLAY,
            signer_hash=KeyPart.sha256_base64url(_HASH),
            request_id_hash=KeyPart.sha256_base64url(request_id_hash),
        )


@pytest.fixture
async def redis_context() -> AsyncIterator[RedisTestContext]:
    mesh_id = f"auth-replay-{uuid4().hex}"
    client = RedisClient(
        RedisConfig(
            url=REDIS_URL or "redis://127.0.0.1:6379/15",
            mesh_id=mesh_id,
            max_connections=128,
        )
    )
    context = RedisTestContext(client, RedisKeyBuilder(mesh_id))
    await client.connect()
    try:
        yield context
    finally:
        keys = (context.replay_key(), context.replay_key(_OTHER_HASH))
        try:
            await client.execute("DEL", *keys)
            assert await client.execute("EXISTS", *keys) == 0
        finally:
            await client.close()


def request(
    *,
    digest: str = _DIGEST_A,
    request_id_hash: str = _HASH,
) -> AuthReplayClaimRequest:
    return AuthReplayClaimRequest(
        signer_hash=KeyPart.sha256_base64url(_HASH),
        request_id_hash=KeyPart.sha256_base64url(request_id_hash),
        auth_proof_digest=digest,
        config_generation=7,
        replay_expires_at_ms=1_120_000,
        state_now_ms=1_000_000,
    )


@pytest.mark.asyncio
async def test_real_redis_claim_is_cross_client_atomic_and_replay_is_opaque(
    redis_context: RedisTestContext,
) -> None:
    second_client = RedisClient(
        RedisConfig(
            url=REDIS_URL or "redis://127.0.0.1:6379/15",
            mesh_id=redis_context.key_builder.mesh_id,
            max_connections=128,
        )
    )
    await second_client.connect()
    try:
        first = AuthReplayClaimRunner(redis_context.client, redis_context.key_builder)
        second = AuthReplayClaimRunner(second_client, redis_context.key_builder)
        claims = await asyncio.gather(
            *(
                first.claim(request()) if index % 2 == 0 else second.claim(request())
                for index in range(100)
            )
        )

        assert claims.count(AuthReplayClaimResult.CLAIMED) == 1
        assert claims.count(AuthReplayClaimResult.REPLAYED) == 99
        assert await redis_context.client.execute("GET", redis_context.replay_key()) == (
            b"v1:" + _DIGEST_A.encode() + b":7"
        )
        ttl_before = await redis_context.client.execute("PTTL", redis_context.replay_key())
        assert type(ttl_before) is int and ttl_before > 0

        assert await second.claim(request(digest=_DIGEST_B)) is AuthReplayClaimResult.REPLAYED
        assert await redis_context.client.execute("GET", redis_context.replay_key()) == (
            b"v1:" + _DIGEST_A.encode() + b":7"
        )
        ttl_after = await redis_context.client.execute("PTTL", redis_context.replay_key())
        assert type(ttl_after) is int and 0 < ttl_after <= ttl_before

        script_exists = await redis_context.client.execute(
            "SCRIPT", "EXISTS", auth_replay_script_sha1()
        )
        assert script_exists == [1]
    finally:
        await second_client.close()


@pytest.mark.asyncio
async def test_real_redis_fails_closed_for_non_string_replay_key(
    redis_context: RedisTestContext,
) -> None:
    runner = AuthReplayClaimRunner(redis_context.client, redis_context.key_builder)

    await redis_context.client.execute("LPUSH", redis_context.replay_key(), b"collision")

    assert await runner.claim(request()) is AuthReplayClaimResult.CORRUPT_REPLAY_KEY
    assert await redis_context.client.execute("TYPE", redis_context.replay_key()) == b"list"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _DESTRUCTIVE_REDIS,
    reason="A2AMESH_TEST_REDIS_DESTRUCTIVE=1 is required for SCRIPT FLUSH integration",
)
async def test_isolated_redis_reloads_after_noscript_for_fresh_transport_retry(
    redis_context: RedisTestContext,
) -> None:
    runner = AuthReplayClaimRunner(redis_context.client, redis_context.key_builder)

    assert await runner.claim(request()) is AuthReplayClaimResult.CLAIMED
    await redis_context.client.execute("SCRIPT", "FLUSH")

    fresh_transport_retry = request(
        digest=_DIGEST_B,
        request_id_hash=_OTHER_HASH,
    )
    assert await runner.claim(fresh_transport_retry) is AuthReplayClaimResult.CLAIMED

    same_auth_proof_replay = AuthReplayClaimRunner(
        redis_context.client,
        redis_context.key_builder,
    )
    assert await same_auth_proof_replay.claim(request()) is AuthReplayClaimResult.REPLAYED
