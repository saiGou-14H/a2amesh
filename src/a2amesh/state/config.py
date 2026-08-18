"""Validated configuration for the Redis state-plane client."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


class RedisConfigError(ValueError):
    """Raised when Redis state-plane configuration is invalid."""


_ENV_URL = "A2AMESH_REDIS_URL"
_ENV_MESH_ID = "A2AMESH_MESH_ID"
_ENV_CONNECT_TIMEOUT = "A2AMESH_REDIS_CONNECT_TIMEOUT_MS"
_ENV_COMMAND_TIMEOUT = "A2AMESH_REDIS_COMMAND_TIMEOUT_MS"
_ENV_MAX_CONNECTIONS = "A2AMESH_REDIS_MAX_CONNECTIONS"
_ENV_HEALTH_CHECK = "A2AMESH_REDIS_HEALTH_CHECK_INTERVAL_S"
_DECIMAL = re.compile(r"^[0-9]+$")
_DEFAULT_URL = "redis://127.0.0.1:6379/0"
_DEFAULT_MESH_ID = "default"
_DEFAULT_CONNECT_TIMEOUT_MS = 1_000
_DEFAULT_COMMAND_TIMEOUT_MS = 1_000
_DEFAULT_MAX_CONNECTIONS = 50
_DEFAULT_HEALTH_CHECK_INTERVAL_S = 30


@dataclass(frozen=True, slots=True, repr=False)
class RedisConfig:
    """Closed, non-secret Redis client configuration.

    The URL may contain credentials supplied by a secret store.  It is never
    included in ``repr`` or in errors raised by the client.  Responses remain
    bytes because state payloads and canonical digests are byte-sensitive.
    """

    url: str = _DEFAULT_URL
    mesh_id: str = _DEFAULT_MESH_ID
    connect_timeout_ms: int = _DEFAULT_CONNECT_TIMEOUT_MS
    command_timeout_ms: int = _DEFAULT_COMMAND_TIMEOUT_MS
    max_connections: int = _DEFAULT_MAX_CONNECTIONS
    health_check_interval_s: int = _DEFAULT_HEALTH_CHECK_INTERVAL_S
    decode_responses: bool = False

    def __post_init__(self) -> None:
        _validate_url(self.url)
        _validate_mesh_id(self.mesh_id)
        _validate_int(self.connect_timeout_ms, "connect_timeout_ms", 1, 120_000)
        _validate_int(self.command_timeout_ms, "command_timeout_ms", 1, 120_000)
        _validate_int(self.max_connections, "max_connections", 1, 10_000)
        _validate_int(
            self.health_check_interval_s,
            "health_check_interval_s",
            0,
            86_400,
        )
        if type(self.decode_responses) is not bool or self.decode_responses:
            raise RedisConfigError("decode_responses must be exactly False")

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> RedisConfig:
        """Load only the documented Redis variables from an environment map."""

        source = os.environ if values is None else values
        return cls(
            url=_env_value(source, _ENV_URL, _DEFAULT_URL),
            mesh_id=_env_value(source, _ENV_MESH_ID, _DEFAULT_MESH_ID),
            connect_timeout_ms=_env_int(
                source, _ENV_CONNECT_TIMEOUT, _DEFAULT_CONNECT_TIMEOUT_MS
            ),
            command_timeout_ms=_env_int(
                source, _ENV_COMMAND_TIMEOUT, _DEFAULT_COMMAND_TIMEOUT_MS
            ),
            max_connections=_env_int(
                source, _ENV_MAX_CONNECTIONS, _DEFAULT_MAX_CONNECTIONS
            ),
            health_check_interval_s=_env_int(
                source, _ENV_HEALTH_CHECK, _DEFAULT_HEALTH_CHECK_INTERVAL_S
            ),
        )

    def client_kwargs(self) -> dict[str, object]:
        """Return safe redis-py keyword arguments without the connection URL."""

        return {
            "decode_responses": False,
            "socket_connect_timeout": self.connect_timeout_ms / 1_000,
            "socket_timeout": self.command_timeout_ms / 1_000,
            "health_check_interval": self.health_check_interval_s,
            "max_connections": self.max_connections,
        }

    def __repr__(self) -> str:
        return (
            "RedisConfig("
            f"url={_redacted_url(self.url)!r}, "
            f"mesh_id={self.mesh_id!r}, "
            f"connect_timeout_ms={self.connect_timeout_ms!r}, "
            f"command_timeout_ms={self.command_timeout_ms!r}, "
            f"max_connections={self.max_connections!r}, "
            f"health_check_interval_s={self.health_check_interval_s!r}, "
            "decode_responses=False)"
        )


def _env_value(values: Mapping[str, str], name: str, default: str) -> str:
    value = values.get(name, default)
    if type(value) is not str or not value:
        raise RedisConfigError(f"{name} must be a non-empty string")
    return value


def _env_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    if type(raw) is not str or not _DECIMAL.fullmatch(raw):
        raise RedisConfigError(f"{name} must be a decimal integer")
    return int(raw)


def _validate_url(value: str) -> None:
    if type(value) is not str or not value or any(
        ord(char) < 0x20 or char.isspace() for char in value
    ):
        raise RedisConfigError("Redis URL must be a non-empty URL without whitespace")
    for index, char in enumerate(value):
        if char == "%" and (
            index + 2 >= len(value)
            or not all(
                candidate in "0123456789abcdefABCDEF"
                for candidate in value[index + 1 : index + 3]
            )
        ):
            raise RedisConfigError("Redis URL contains an invalid percent escape")
    if "?" in value:
        raise RedisConfigError("Redis URL query parameters are not allowed")
    if "#" in value:
        raise RedisConfigError("Redis URL fragments are not allowed")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise RedisConfigError("Redis URL authority or port is invalid") from None
    if parsed.scheme not in {"redis", "rediss"} or not hostname:
        raise RedisConfigError("Redis URL must use redis:// or rediss:// with a hostname")
    if port is not None and not 1 <= port <= 65_535:
        raise RedisConfigError("Redis URL port must be in [1, 65535]")


def _validate_mesh_id(value: str) -> None:
    if type(value) is not str or not value or len(value) > 128:
        raise RedisConfigError("mesh_id must be a non-empty string of at most 128 characters")
    if any(ord(char) < 0x20 or char.isspace() or char in "{}" for char in value):
        raise RedisConfigError("mesh_id contains whitespace, control characters, or braces")


def _validate_int(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise RedisConfigError(f"{name} must be an integer in [{minimum}, {maximum}]")


def _redacted_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.hostname is None:
            return "<invalid>"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return f"{parsed.scheme}://{host}"
    except ValueError:
        return "<invalid>"
