from __future__ import annotations

import asyncio
import traceback

import pytest
from redis.exceptions import NoScriptError

from a2amesh.state.client import RedisClient, RedisClientError, RedisNoScriptError
from a2amesh.state.config import RedisConfig, RedisConfigError


class FakePool:
    def __init__(
        self,
        *,
        ping_error: BaseException | None = None,
        ping_result: bool = True,
        close_error: Exception | None = None,
        cancel_close: bool = False,
        execute_error: Exception | None = None,
    ) -> None:
        self.ping_error = ping_error
        self.ping_result = ping_result
        self.close_error = close_error
        self.cancel_close = cancel_close
        self.execute_error = execute_error
        self.closed = False
        self.close_calls = 0
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    async def ping(self) -> bool:
        if self.ping_error is not None:
            raise self.ping_error
        return self.ping_result

    async def execute_command(self, command: str, *args: object) -> str:
        self.commands.append((command, args))
        if self.execute_error is not None:
            raise self.execute_error
        return "OK"

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.cancel_close:
            self.cancel_close = False
            raise asyncio.CancelledError
        if self.close_error is not None:
            raise self.close_error
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
        {"url": "redis://localhost/0?decode_responses=True"},
        {"url": "redis://localhost:not-a-port/0"},
        {"url": "redis://localhost:65536/0"},
        {"url": "redis://[::1/0"},
        {"url": "redis://localhost/0#"},
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


def test_redis_config_repr_never_exposes_url_path_or_credentials() -> None:
    rendered = repr(RedisConfig(url="redis://user:path-secret@localhost:6379/db-secret"))
    assert "user" not in rendered
    assert "path-secret" not in rendered
    assert "db-secret" not in rendered


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


@pytest.mark.asyncio
async def test_client_rejects_false_ping_before_publishing_pool() -> None:
    pool = FakePool(ping_result=False)
    client = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(pool))

    with pytest.raises(RedisClientError, match="PING"):
        await client.connect()
    assert client.connected is False
    assert pool.closed is True


@pytest.mark.asyncio
async def test_close_failure_and_cancellation_retain_pool_for_retry() -> None:
    close_error = RuntimeError("URL=redis://user:secret@host/0")
    pool = FakePool(close_error=close_error)
    client = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(pool))
    await client.connect()

    with pytest.raises(RedisClientError) as raised:
        await client.close()
    assert raised.value.__cause__ is None
    assert pool.close_calls == 1
    assert client._pool is pool
    pool.close_error = None
    await client.close()
    assert pool.close_calls == 2
    assert pool.closed is True

    cancelled_pool = FakePool(cancel_close=True)
    cancelled = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(cancelled_pool))
    await cancelled.connect()
    with pytest.raises(asyncio.CancelledError):
        await cancelled.close()
    assert cancelled._pool is cancelled_pool
    await cancelled.close()
    assert cancelled_pool.close_calls == 2
    assert cancelled_pool.closed is True


@pytest.mark.asyncio
async def test_connect_cleanup_failure_preserves_primary_error_and_pool_ownership() -> None:
    pool = FakePool(
        ping_error=RuntimeError("URL=redis://user:secret@host/0"),
        close_error=RuntimeError("ARG=cleanup-secret"),
    )
    client = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(pool))

    with pytest.raises(RedisClientError, match="connection failed") as raised:
        await client.connect()
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert client._pool is pool
    rendered = "".join(
        traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__)
    )
    assert "secret" not in rendered
    pool.close_error = None
    await client.close()
    assert pool.close_calls == 2
    assert pool.closed is True


@pytest.mark.asyncio
async def test_command_errors_never_expose_command_arguments_or_cause() -> None:
    pool = FakePool(execute_error=RuntimeError("ARG=payload-secret"))
    client = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(pool))

    with pytest.raises(RedisClientError) as raised:
        await client.execute("GET", b"payload-secret")
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    rendered = "".join(traceback.format_exception_only(type(raised.value), raised.value))
    assert "payload-secret" not in rendered
    assert "GET" not in str(raised.value)
    with pytest.raises(RedisClientError, match="single token") as invalid:
        await client.execute("GET SECRET", b"payload-secret")
    assert "GET SECRET" not in str(invalid.value)


@pytest.mark.asyncio
async def test_client_maps_noscript_without_retaining_server_message_or_cause() -> None:
    pool = FakePool(execute_error=NoScriptError("NOSCRIPT internal script details"))
    client = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(pool))

    with pytest.raises(RedisNoScriptError) as raised:
        await client.execute("EVALSHA", "a" * 40, 0)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "internal" not in str(raised.value)
    assert pool.commands == [("EVALSHA", ("a" * 40, 0))]


@pytest.mark.asyncio
async def test_client_treats_noscript_from_non_evalsha_as_generic_redacted_failure() -> None:
    pool = FakePool(execute_error=NoScriptError("NOSCRIPT internal script details"))
    client = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(pool))

    with pytest.raises(RedisClientError) as raised:
        await client.execute("SCRIPT", "LOAD", b"untrusted script bytes")

    assert type(raised.value) is RedisClientError
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "internal" not in str(raised.value)
    assert pool.commands == [("SCRIPT", ("LOAD", b"untrusted script bytes"))]


@pytest.mark.asyncio
async def test_connect_cancellation_survives_cleanup_failure_and_retains_pool() -> None:
    pool = FakePool(
        ping_error=asyncio.CancelledError(),
        close_error=RuntimeError("cleanup-secret"),
    )
    client = RedisClient(RedisConfig(), redis_factory=FakeRedisFactory(pool))

    with pytest.raises(asyncio.CancelledError):
        await client.connect()
    assert client.connected is False
    assert client._pool is pool
    assert pool.close_calls == 1
    pool.close_error = None
    await client.close()
    assert pool.close_calls == 2
    assert pool.closed is True
