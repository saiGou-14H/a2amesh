"""Small lifecycle-safe async Redis client for the state service."""

from __future__ import annotations

import asyncio
import inspect
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


class RedisClient:
    """Own one redis-py async pool and make its lifecycle explicit.

    ``connect`` pings before publishing the pool, so callers never observe a
    half-created connection.  All public errors omit the URL and command
    arguments; those can contain credentials or state payloads.
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
            pool: _RedisPool | None = None
            try:
                pool = self._redis_factory.from_url(
                    self.config.url,
                    **self.config.client_kwargs(),
                )
                await pool.ping()
            except asyncio.CancelledError:
                if pool is not None:
                    await _close_pool(pool)
                raise
            except Exception as exc:
                if pool is not None:
                    await _close_pool(pool)
                raise RedisClientError("Redis connection failed") from exc
            self._pool = pool

    async def ping(self) -> bool:
        """Run a live PING after ensuring the pool is connected."""

        pool = await self._pool_for_operation()
        try:
            return bool(await pool.ping())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RedisClientError("Redis PING failed") from exc

    async def execute(self, command: str, *args: object) -> Any:
        """Execute one Redis command while keeping payloads out of errors."""

        if type(command) is not str or not command.strip():
            raise RedisClientError("Redis command must be a non-empty string")
        pool = await self._pool_for_operation()
        try:
            return await pool.execute_command(command, *args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RedisClientError(
                f"Redis command failed: {command.strip().upper()}"
            ) from exc

    async def close(self) -> None:
        """Close the owned pool; safe to call repeatedly."""

        async with self._lock:
            self._closed = True
            pool, self._pool = self._pool, None
            if pool is not None:
                await _close_pool(pool)

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


async def _close_pool(pool: _RedisPool) -> None:
    try:
        result = pool.aclose()
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise RedisClientError("Redis pool close failed") from exc
