from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from a2amesh.state.client import RedisClientError, RedisNoScriptError
from a2amesh.state.key_builder import KeyKind, KeyPart, RedisKeyBuilder
from a2amesh.state.script_runner import (
    AuthReplayClaimError,
    AuthReplayClaimRequest,
    AuthReplayClaimResult,
    AuthReplayClaimRunner,
    auth_replay_script_sha1,
    auth_replay_script_source,
)

_HASH = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


class FakeRedis:
    def __init__(self, *eval_results: object) -> None:
        self.eval_results = list(eval_results)
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, command: str, *args: object) -> object:
        self.commands.append((command, args))
        if command == "SCRIPT":
            assert args[0] == "LOAD"
            source = args[1]
            assert type(source) is bytes
            return hashlib.sha1(source, usedforsecurity=False).hexdigest().encode("ascii")
        assert command == "EVALSHA"
        result = self.eval_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def request(
    *,
    auth_proof_digest: str = _DIGEST_A,
    replay_expires_at_ms: int = 1_060_000,
    state_now_ms: int = 1_000_000,
) -> AuthReplayClaimRequest:
    return AuthReplayClaimRequest(
        signer_hash=KeyPart.sha256_base64url(_HASH),
        request_id_hash=KeyPart.sha256_base64url(_HASH),
        auth_proof_digest=auth_proof_digest,
        config_generation=7,
        replay_expires_at_ms=replay_expires_at_ms,
        state_now_ms=state_now_ms,
    )


def runner(redis: FakeRedis) -> AuthReplayClaimRunner:
    return AuthReplayClaimRunner(redis, RedisKeyBuilder("mesh-a"))


@pytest.mark.asyncio
async def test_claim_uses_exact_builder_key_and_frozen_evalsha_abi() -> None:
    redis = FakeRedis(1)
    claim = request()
    subject = runner(redis)

    result = await subject.claim(claim)

    assert result is AuthReplayClaimResult.CLAIMED
    source = auth_replay_script_source()
    assert type(source) is bytes
    assert auth_replay_script_sha1() == hashlib.sha1(source, usedforsecurity=False).hexdigest()
    key = RedisKeyBuilder("mesh-a").render(
        KeyKind.AUTH_REPLAY,
        signer_hash=KeyPart.sha256_base64url(_HASH),
        request_id_hash=KeyPart.sha256_base64url(_HASH),
    )
    assert redis.commands == [
        ("SCRIPT", ("LOAD", source)),
        (
            "EVALSHA",
            (
                auth_replay_script_sha1(),
                1,
                key,
                _DIGEST_A,
                "7",
                "60000",
            ),
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, AuthReplayClaimResult.REPLAYED),
        (-1, AuthReplayClaimResult.CORRUPT_REPLAY_KEY),
    ],
)
async def test_claim_decodes_only_closed_script_results(
    code: int, expected: AuthReplayClaimResult
) -> None:
    assert await runner(FakeRedis(code)).claim(request()) is expected


@pytest.mark.asyncio
async def test_noscript_is_reloaded_and_retried_exactly_once() -> None:
    redis = FakeRedis(RedisNoScriptError("Redis script is not loaded"), 1)

    assert await runner(redis).claim(request()) is AuthReplayClaimResult.CLAIMED
    assert [command for command, _ in redis.commands] == [
        "SCRIPT",
        "EVALSHA",
        "SCRIPT",
        "EVALSHA",
    ]


@pytest.mark.asyncio
async def test_generic_redis_failure_is_not_retried() -> None:
    redis = FakeRedis(RedisClientError("Redis command failed"))

    with pytest.raises(RedisClientError, match="Redis command failed"):
        await runner(redis).claim(request())

    assert [command for command, _ in redis.commands] == ["SCRIPT", "EVALSHA"]


@pytest.mark.asyncio
async def test_unknown_script_result_fails_closed_without_replaying() -> None:
    redis = FakeRedis(99)

    with pytest.raises(AuthReplayClaimError, match="invalid result"):
        await runner(redis).claim(request())

    assert [command for command, _ in redis.commands] == ["SCRIPT", "EVALSHA"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda: {
            "signer_hash": KeyPart.safe_token("attacker-value"),
        },
        lambda: {
            "auth_proof_digest": "ATTACKER-VALUE",
        },
        lambda: {
            "config_generation": 0,
        },
        lambda: {
            "replay_expires_at_ms": 1_000_000,
        },
        lambda: {
            "state_now_ms": True,
        },
    ],
)
def test_request_rejects_untrusted_or_expired_values_without_echoing_them(mutate) -> None:
    values: dict[str, object] = {
        "signer_hash": KeyPart.sha256_base64url(_HASH),
        "request_id_hash": KeyPart.sha256_base64url(_HASH),
        "auth_proof_digest": _DIGEST_A,
        "config_generation": 7,
        "replay_expires_at_ms": 1_060_000,
        "state_now_ms": 1_000_000,
    }
    values.update(mutate())

    with pytest.raises(AuthReplayClaimError) as raised:
        AuthReplayClaimRequest(**values)  # type: ignore[arg-type]

    assert "attacker-value" not in str(raised.value)
    assert "ATTACKER-VALUE" not in str(raised.value)


def test_integration_gate_uses_precise_cleanup_and_destructive_opt_in() -> None:
    integration = (
        Path(__file__).resolve().parents[2]
        / "integration"
        / "state"
        / "test_auth_replay_claim_redis.py"
    ).read_text(encoding="utf-8")

    assert '"FLUSHDB"' not in integration
    assert "A2AMESH_TEST_REDIS_DESTRUCTIVE" in integration
    assert '"SCRIPT", "FLUSH"' in integration


def test_runner_source_is_packaged_and_does_not_embed_claim_message() -> None:
    source = auth_replay_script_source().decode("utf-8")

    assert "claim_message" not in source
    assert "redis.call('TYPE'" in source
    assert "redis.call('SET'" in source
    assert "SCRIPT FLUSH" not in source
