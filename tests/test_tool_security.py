from __future__ import annotations

from pathlib import Path

import pytest

from a2amesh.a2anats.errors import FORBIDDEN, JsonRpcError
from a2amesh.tools.builtin import WorkspaceTools


@pytest.mark.asyncio
async def test_workspace_tools_never_read_or_write_secret_files(tmp_path: Path):
    (tmp_path / ".env").write_text("A2AMESH_NKEY_SEED=secret", encoding="utf-8")
    (tmp_path / "cert.pem").write_text("PRIVATE", encoding="utf-8")
    sensitive_names = [
        "secret.txt",
        "passwords.yaml",
        "token.json",
        "credentials.toml",
        "api_key.txt",
    ]
    for name in sensitive_names:
        (tmp_path / name).write_text("PRIVATE", encoding="utf-8")
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    tools = WorkspaceTools(str(tmp_path))

    assert await tools.read_file("safe.txt") == {"content": "safe"}
    for name in [
        ".env",
        "cert.pem",
        ".git/config",
        ".ssh/id_ed25519",
        *sensitive_names,
    ]:
        with pytest.raises(JsonRpcError) as exc:
            await tools.read_file(name)
        assert exc.value.code == FORBIDDEN
        with pytest.raises(JsonRpcError) as write_exc:
            await tools.write_file(name, "overwrite")
        assert write_exc.value.code == FORBIDDEN

    listing = await tools.list_dir(".")
    assert "safe.txt" in listing["entries"]
    assert ".env" not in listing["entries"]
    assert "cert.pem" not in listing["entries"]
    assert not set(sensitive_names) & set(listing["entries"])
