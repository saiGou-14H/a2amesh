"""Behavioral gates for the explicit legacy private RPC compatibility adapter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from a2amesh.a2anats import (
    LegacyCompatibilityDisabledError,
    LegacyMeshClientAdapter,
    LegacyMeshServerAdapter,
)
from a2amesh.contracts.models import Message, TextPart


class NoIoNats:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def new_inbox(self):
        self.calls.append("new_inbox")
        raise AssertionError("NATS I/O must not happen while compatibility is disabled")

    async def request(self, *args, **kwargs):
        self.calls.append("request")
        raise AssertionError("NATS I/O must not happen while compatibility is disabled")

    async def subscribe(self, *args, **kwargs):
        self.calls.append("subscribe")
        raise AssertionError("NATS I/O must not happen while compatibility is disabled")

    async def publish(self, *args, **kwargs):
        self.calls.append("publish")
        raise AssertionError("NATS I/O must not happen while compatibility is disabled")

    async def flush(self):
        self.calls.append("flush")
        raise AssertionError("NATS I/O must not happen while compatibility is disabled")


class FakeMessage:
    data = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "tasks/get",
            "params": {"id": "task-1"},
        }
    ).encode()
    reply = "_INBOX.test"

    async def respond(self, payload: bytes) -> None:
        raise AssertionError(f"response I/O must not happen: {payload!r}")


@pytest.mark.asyncio
async def test_default_client_rejects_every_legacy_entrypoint_before_nats_io() -> None:
    nc = NoIoNats()
    client = LegacyMeshClientAdapter(nc)
    message = Message(role="user", parts=[TextPart(text="hello")])
    calls = [
        client.discover(),
        client.get_card("worker"),
        client.send_message("worker", message),
        client.send_message_stream("worker", message),
        client.get_task("worker", "task-1"),
        client.cancel("worker", "task-1"),
        client.call_tool("worker", "read_file", {}),
        client.broadcast("hello"),
    ]

    for call in calls:
        with pytest.raises(LegacyCompatibilityDisabledError, match="disabled"):
            await call
    assert nc.calls == []


@pytest.mark.asyncio
async def test_default_server_rejects_start_and_direct_handler_bypass_before_io() -> None:
    nc = NoIoNats()
    server = LegacyMeshServerAdapter(nc, "worker", SimpleNamespace())
    message = FakeMessage()
    calls = [
        server.start(),
        server._schedule_rpc(message),
        server._on_rpc(message),
        server._on_card(message),
        server._dispatch("tasks/get", {"id": "task-1"}, message),
    ]

    for call in calls:
        with pytest.raises(LegacyCompatibilityDisabledError, match="disabled"):
            await call
    assert nc.calls == []


def test_obsolete_mesh_client_and_server_import_shims_are_removed() -> None:
    package = Path(__file__).parents[1] / "src" / "a2amesh" / "a2anats"
    assert not (package / "client.py").exists()
    assert not (package / "server.py").exists()

    compatibility_source = (package / "compatibility.py").read_text()
    assert "class LegacyMeshClientAdapter" in compatibility_source
    assert "class LegacyMeshServerAdapter" in compatibility_source
    assert "message/send" in compatibility_source
    assert "tasks/get" in compatibility_source


def test_production_runtime_uses_named_adapter_and_cli_does_not_overclaim_listener() -> None:
    root = Path(__file__).parents[1] / "src" / "a2amesh"
    agent_source = (root / "runtime" / "agent.py").read_text()
    orchestrator_source = (root / "orchestrator" / "orchestrator.py").read_text()
    cli_source = (root / "cli.py").read_text()

    assert "LegacyMeshClientAdapter" in agent_source
    assert "LegacyMeshServerAdapter" in agent_source
    assert "from a2amesh.a2anats.client import" not in agent_source
    assert "from a2amesh.a2anats.server import" not in agent_source
    assert "LegacyMeshClientAdapter" in orchestrator_source
    assert "LegacyMeshServerAdapter" in orchestrator_source
    assert "from a2amesh.a2anats.client import" not in orchestrator_source
    assert "from a2amesh.a2anats.server import" not in orchestrator_source
    assert "legacy private RPC disabled" in cli_source
    assert "未订阅 a2a.rpc/a2a.cards" in cli_source


class FakeSubscription:
    def __init__(self, subject: str) -> None:
        self.subject = subject
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeService:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class RecordingNats:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, str, object, FakeSubscription]] = []

    async def subscribe(self, subject: str, *, queue: str, cb):
        subscription = FakeSubscription(subject)
        self.subscriptions.append((subject, queue, cb, subscription))
        return subscription


@pytest.mark.asyncio
async def test_explicit_server_opt_in_subscribes_only_legacy_literal_subjects_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nats import micro

    nc = RecordingNats()
    service = FakeService()
    service_calls: list[dict] = []

    async def add_service(connection, **kwargs):
        assert connection is nc
        service_calls.append(kwargs)
        return service

    monkeypatch.setattr(micro, "add_service", add_service)
    server = LegacyMeshServerAdapter(
        nc,
        "worker",
        SimpleNamespace(),
        enabled=True,
    )
    await server.start()

    assert [item[0] for item in nc.subscriptions] == [
        "a2a.rpc.worker",
        "a2a.cards.worker",
    ]
    assert all("a2a.v1" not in item[0] for item in nc.subscriptions)
    assert [item[1] for item in nc.subscriptions] == [
        "a2a-legacy-worker-worker",
        "a2a-legacy-card-worker",
    ]
    assert service_calls[0]["version"] == "1.0.0-legacy-compat"

    await server.close()
    assert service.stopped is True
    assert all(item[3].unsubscribed for item in nc.subscriptions)


@pytest.mark.asyncio
async def test_enabled_client_rejects_unknown_method_and_unsafe_agent_before_io() -> None:
    nc = NoIoNats()
    client = LegacyMeshClientAdapter(nc, enabled=True)

    with pytest.raises(ValueError, match="closed legacy compatibility set"):
        await client._rpc("worker", "SendMessage", {}, 1)
    with pytest.raises(ValueError, match="safe NATS subject token"):
        await client._rpc("worker.>", "tasks/get", {}, 1)
    assert nc.calls == []
