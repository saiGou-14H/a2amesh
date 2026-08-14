from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from a2amesh.config import McpConfig
from a2amesh.tools.mcp.connector import connect


@pytest.mark.asyncio
async def test_stdio_mcp_discovers_invokes_and_closes_real_server(tmp_path: Path):
    server = tmp_path / "server.py"
    server.write_text(
        """
from mcp.server.mcpserver import MCPServer

server = MCPServer("fixture")

@server.tool()
async def echo(text: str) -> dict:
    return {"echo": text}

server.run("stdio")
""",
        encoding="utf-8",
    )
    config = McpConfig(
        type="stdio",
        name="fixture",
        command=sys.executable,
        args=[str(server)],
        timeout_seconds=10,
    )
    connection = await connect(config)
    try:
        assert [tool.name for tool in connection.tools] == ["fixture__echo"]
        result = await connection.tools[0].handler(text="hello")
        assert result["content"][0]["type"] == "text"
        assert "hello" in result["content"][0]["text"]
        assert result["isError"] is False
    finally:
        await connection.close()


@pytest.mark.parametrize(
    "data",
    [
        {"type": "sse", "url": "https://example.com/sse"},
        {"type": "stdio"},
        {"type": "streamable-http", "url": "file:///tmp/mcp"},
    ],
)
def test_mcp_config_rejects_legacy_or_incomplete_transports(data):
    with pytest.raises(ValidationError):
        McpConfig.model_validate(data)
