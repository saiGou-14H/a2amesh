from __future__ import annotations

import asyncio

import pytest

from a2amesh.state.client import RedisClient, RedisClientError
from a2amesh.state.config import RedisConfig, RedisConfigError


class FakePool:
    def __init__(self, *, ping_error: Exception | None = None) -> None:
        self.ping_error = ping_error
        self.closed = False
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    async def ping(self) -> bool:
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def execute_command(self, command: str, *args: object) -> str:
        self.commands.append((command, args))
        return "OK"

    async def aclose(self) -> None:
        self.closed = True


class FakeRedisFactory:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool
        self.calls: list[tuple[str, dict[str, object]]] = []

    def from_url(self, url: str, **kwargs: object) -> FakePool:
        self.calls.append((url, kwargs))
        return self.pool


def test_redis_config_defaults_and_env_are_strict() -> None:
    default = RedisConfig()
    assert default.url == "redis://127.0.0.1:6379/0"
    assert default.mesh_id == "default"
    assert default.decode_responses is False

    configured = RedisConfig.from_env(
        {
            "A2AMESH_REDIS_URL": "rediss://user:secret@example.test:6380/2",
            "A2AMESH_MESH_ID": "mesh-prod",
            "A2AMESH_REDIS_CONNECT_TIMEOUT_MS": "250",
            "A2AMESH_REDIS_COMMAND_TIMEOUT_MS": "750",
            "A2AMESH_REDIS_MAX_CONNECTIONS": "12",
            "A2AMESH_REDIS_HEALTH_CHECK_INTERVAL_S": "17",
        }
    )
    assert configured.url.startswith("rediss://")
    assert configured.mesh_id == "mesh-prod"
    assert configured.connect_timeout_ms == 250
    assert configured.command_timeout_ms == 750
    assert configured.max_connections == 12
    assert configured.health_check_interval_s == 17
    assert configured.decode_responses is False
    assert "secret" not in repr(configured)


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "http://localhost:6379/0"},
        {"url": "redis://"},
        {"url": "redis://localhost/0?bad=%zz"},
        {"mesh_id": "mesh with spaces"},
        {"mesh_id": "mesh{hash-tag}"},
        {"connect_timeout_ms": 0},
        {"command_timeout_ms": -1},
        {"max_connections": 0},
        {"health_check_interval_s": -1},
    ],
)
def test_redis_config_rejects_unsafe_values(changes: dict[str, object]) -> None:
    with pytest.raises(RedisConfigError):
        RedisConfig(**changes)


def test_redis_config_rejects_bad_environment_integers() -> None:
    with pytest.raises(RedisConfigError, match="CONNECT_TIMEOUT"):
        RedisConfig.from_env({"A2AMESH_REDIS_CONNECT_TIMEOUT_MS": "nope"})
    with pytest.raises(RedisConfigError, match="MESH_ID"):
        RedisConfig.from_env({"A2AMESH_MESH_ID": ""})


@pytest.mark.asyncio
async def test_client_connects_once_executes_bytes_and_closes_idempotently() -> None:
    pool = FakePool()
    factory = FakeRedisFactory(pool)
    client = RedisClient(RedisConfig(), redis_factory=factory)

    assert await asyncio.gather(client.connect(), client.connect()) == [None, None]
    assert len(factory.calls) == 1
    assert factory.calls[0][1]["decode_responses"] is False
    assert await client.execute("SET", b"key", b"value") == "OK"
    assert pool.commands == [("SET", (b"key", b"value"))]

    await client.close()
    await client.close()
    assert pool.closed is True
    with pytest.raises(RedisClientError, match="closed"):
        await client.execute("PING")


@pytest.mark.asyncio
async def test_client_ping_failure_closes_pool_and_redacts_connection_details() -> None:
    pool = FakePool(ping_error=RuntimeError("redis://user:secret@host leaked"))
    factory = FakeRedisFactory(pool)
    client = RedisClient(RedisConfig(url="redis://user:secret@host/0"), redis_factory=factory)

    with pytest.raises(RedisClientError) as raised:
        await client.connect()
    assert "secret" not in str(raised.value)
    assert pool.closed is True
    with pytest.raises(RedisClientError, match="connection failed"):
        await client.connect()
