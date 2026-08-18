"""Small lifecycle-safe async Redis client for the state service."""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any, Protocol

from redis import asyncio as redis_asyncio

from .config import RedisConfig


class RedisClientError(RuntimeError):
    """Raised for client lifecycle, connectivity, or command failures."""


class _RedisPool(Protocol):
    async def ping(self) -> bool: ...

    async def execute_command(self, command: str, *args: object) -> Any: ...

    async def aclose(self) -> None: ...


class _RedisFactory(Protocol):
    def from_url(self, url: str, **kwargs: object) -> _RedisPool: ...


_COMMAND = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


class RedisClient:
    """Own one redis-py async pool and make its lifecycle explicit.

    ``connect`` publishes a pool only after a successful PING. Pools whose
    health check or close fails remain owned until cleanup can be retried.
    Public errors never retain a sensitive underlying exception chain.
    """

    def __init__(
        self,
        config: RedisConfig,
        *,
        redis_factory: _RedisFactory = redis_asyncio.Redis,
    ) -> None:
        self.config = config
        self._redis_factory = redis_factory
        self._pool: _RedisPool | None = None
        self._cleanup_pool: _RedisPool | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def connected(self) -> bool:
        """Whether a healthy pool has been published to this client."""

        return self._pool is not None and not self._closed

    async def connect(self) -> None:
        """Create and health-check the pool exactly once."""

        if self._closed:
            raise RedisClientError("Redis client is closed")
        if self._pool is not None:
            return
        async with self._lock:
            if self._closed:
                raise RedisClientError("Redis client is closed")
            if self._pool is not None:
                return
            await self._retry_pending_cleanup()

            pool: _RedisPool | None = None
            factory_failed = False
            try:
                pool = self._redis_factory.from_url(
                    self.config.url,
                    **self.config.client_kwargs(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                factory_failed = True
            if factory_failed or pool is None:
                raise RedisClientError("Redis connection failed") from None

            ping_failed = False
            try:
                ping_result = await pool.ping()
            except asyncio.CancelledError:
                self._cleanup_pool = pool
                if await _try_close_pool(pool):
                    self._cleanup_pool = None
                raise
            except Exception:
                ping_failed = True
                ping_result = False

            if ping_failed:
                self._cleanup_pool = pool
                if await _try_close_pool(pool):
                    self._cleanup_pool = None
                raise RedisClientError("Redis connection failed") from None
            if ping_result is not True:
                self._cleanup_pool = pool
                if await _try_close_pool(pool):
                    self._cleanup_pool = None
                raise RedisClientError("Redis PING failed") from None
            self._pool = pool

    async def ping(self) -> bool:
        """Run a live PING after ensuring the pool is connected."""

        pool = await self._pool_for_operation()
        ping_failed = False
        try:
            result = await pool.ping()
        except asyncio.CancelledError:
            raise
        except Exception:
            ping_failed = True
            result = False
        if ping_failed or result is not True:
            raise RedisClientError("Redis PING failed") from None
        return True

    async def execute(self, command: str, *args: object) -> Any:
        """Execute one Redis command while keeping payloads out of errors."""

        if type(command) is not str or _COMMAND.fullmatch(command) is None:
            raise RedisClientError("Redis command must be one non-empty single token")
        pool = await self._pool_for_operation()
        command_failed = False
        result: Any = None
        try:
            result = await pool.execute_command(command, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            command_failed = True
        if command_failed:
            raise RedisClientError("Redis command failed") from None
        return result

    async def close(self) -> None:
        """Close every owned pool; failures retain ownership for a retry."""

        async with self._lock:
            self._closed = True
            await self._close_owned("_pool")
            await self._close_owned("_cleanup_pool")

    async def __aenter__(self) -> RedisClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    async def _pool_for_operation(self) -> _RedisPool:
        await self.connect()
        pool = self._pool
        if pool is None:
            raise RedisClientError("Redis connection is unavailable")
        return pool

    async def _retry_pending_cleanup(self) -> None:
        pool = self._cleanup_pool
        if pool is None:
            return
        await _close_pool(pool)
        self._cleanup_pool = None

    async def _close_owned(self, attribute: str) -> None:
        pool = getattr(self, attribute)
        if pool is None:
            return
        await _close_pool(pool)
        setattr(self, attribute, None)


async def _try_close_pool(pool: _RedisPool) -> bool:
    """Best-effort failure cleanup without dropping pool ownership."""

    try:
        await _close_pool(pool)
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise
        return False
    except RedisClientError:
        return False
    return True


async def _close_pool(pool: _RedisPool) -> None:
    close_failed = False
    try:
        result = pool.aclose()
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception:
        close_failed = True
    if close_failed:
        raise RedisClientError("Redis pool close failed") from None
