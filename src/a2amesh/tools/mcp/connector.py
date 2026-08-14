"""Persistent MCP 2026-07-28 client connections mapped into ToolRegistry."""

from __future__ import annotations

import re
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit

from a2amesh.tools.base import Tool

_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


class McpConnection:
    """Own one MCP transport/session and its discovered tool adapters."""

    def __init__(self, config) -> None:
        self.config = config
        self.alias = self._alias(config)
        self._stack = AsyncExitStack()
        self._session = None
        self.tools: list[Tool] = []

    @staticmethod
    def _alias(config) -> str:
        configured = getattr(config, "name", None)
        if configured:
            alias = configured
        elif getattr(config, "command", None):
            alias = Path(config.command).stem
        elif getattr(config, "url", None):
            alias = urlsplit(config.url).hostname or "mcp"
        else:
            alias = "mcp"
        alias = alias.replace(".", "_").replace("-", "_")
        if not _SAFE_ALIAS.fullmatch(alias):
            raise ValueError(f"invalid MCP server alias: {alias!r}")
        return alias

    async def connect(self) -> McpConnection:
        from mcp.client.session import ClientSession

        transport = self.config.type
        if transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            if not self.config.command:
                raise ValueError("MCP stdio config requires command")
            parameters = StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
                env=dict(self.config.env) or None,
                cwd=self.config.cwd,
            )
            read_stream, write_stream = await self._stack.enter_async_context(
                stdio_client(parameters)
            )
        elif transport in {"http", "streamable-http"}:
            from httpx2 import AsyncClient, Timeout
            from mcp.client.streamable_http import streamable_http_client

            if not self.config.url:
                raise ValueError("MCP Streamable HTTP config requires url")
            parsed = urlsplit(self.config.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid MCP Streamable HTTP URL")
            http_client = await self._stack.enter_async_context(
                AsyncClient(
                    headers=dict(self.config.headers),
                    timeout=Timeout(self.config.timeout_seconds),
                    follow_redirects=False,
                )
            )
            streams = await self._stack.enter_async_context(
                streamable_http_client(self.config.url, http_client=http_client)
            )
            read_stream, write_stream = streams[:2]
        else:
            raise ValueError("legacy MCP SSE transport is not supported")

        self._session = await self._stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=self.config.timeout_seconds,
            )
        )
        await self._session.initialize()
        listing = await self._session.list_tools()
        self.tools = [self._adapt_tool(spec) for spec in listing.tools]
        return self

    def _adapt_tool(self, spec) -> Tool:
        exposed_name = f"{self.alias}__{spec.name}"

        async def invoke(**arguments) -> dict:
            if self._session is None:
                raise RuntimeError("MCP connection is closed")
            result = await self._session.call_tool(spec.name, arguments)
            payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if payload.get("isError"):
                raise RuntimeError(f"MCP tool failed: {spec.name}")
            return payload

        return Tool(
            name=exposed_name,
            description=spec.description or f"MCP tool {spec.name}",
            parameters=spec.input_schema,
            handler=invoke,
            source="mcp",
            risk="medium",
            public=False,
        )

    async def close(self) -> None:
        self._session = None
        await self._stack.aclose()


async def connect(config) -> McpConnection:
    """Connect one configured MCP server and discover its tools."""

    connection = McpConnection(config)
    try:
        return await connection.connect()
    except BaseException:
        await connection.close()
        raise
