"""Regression tests for blockers identified by the independent audits."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from a2amesh.a2anats.compatibility import (
    LegacyMeshClientAdapter,
    LegacyMeshServerAdapter,
)
from a2amesh.a2anats.errors import FORBIDDEN, INVALID_PARAMS, METHOD_NOT_FOUND, JsonRpcError
from a2amesh.cli import cmd_bootstrap, cmd_init
from a2amesh.cli import main as cli_main
from a2amesh.config import Config
from a2amesh.contracts.models import Message, Plan, Step, Task, TaskStatus, TextPart
from a2amesh.orchestrator.dispatcher import Dispatcher
from a2amesh.runtime.adapters.base import TaskResult
from a2amesh.runtime.adapters.claude import ClaudeAdapter
from a2amesh.runtime.adapters.codex import CodexAdapter
from a2amesh.runtime.adapters.hermes import HermesAdapter
from a2amesh.runtime.adapters.opencode import OpenCodeAdapter
from a2amesh.runtime.agent import AgentRuntime
from a2amesh.runtime.executor import Executor
from a2amesh.tools.base import Tool
from a2amesh.tools.registry import ToolRegistry


def make_config(tmp_path: Path, name: str = "audit") -> Config:
    return Config.model_validate(
        {
            "nats": {"url": "nats://127.0.0.1:4222"},
            "compatibility": {"legacy_private_rpc_enabled": True},
            "agent": {
                "name": name,
                "default_runtime": "hermes",
                "runtimes": ["hermes"],
                "workdir": str(tmp_path),
                "tools_dir": str(tmp_path / "tools"),
            },
        }
    )


class RetryNC:
    def __init__(self):
        self.requests: list[dict] = []

    async def request(self, subject, payload, timeout):
        request = json.loads(payload)
        self.requests.append(request)
        if len(self.requests) == 1:
            raise TimeoutError("lost response")
        task_id = request["params"]["metadata"]["taskId"]
        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"id": task_id, "status": {"state": "completed"}},
        }
        return SimpleNamespace(data=json.dumps(response).encode())


@pytest.mark.asyncio
async def test_send_retry_reuses_a_non_null_task_id():
    nc = RetryNC()
    task = await LegacyMeshClientAdapter(nc, enabled=True).send_message(
        "worker", Message(role="user", parts=[TextPart(text="hello")])
    )
    ids = [request["params"]["metadata"]["taskId"] for request in nc.requests]
    assert len(ids) == 2
    assert ids[0]
    assert ids[0] == ids[1] == task.id


class AlwaysTimeoutNC:
    def __init__(self):
        self.calls = 0

    async def request(self, subject, payload, timeout):
        self.calls += 1
        raise TimeoutError


@pytest.mark.asyncio
async def test_non_idempotent_tool_call_is_not_retried():
    nc = AlwaysTimeoutNC()
    with pytest.raises(TimeoutError):
        await LegacyMeshClientAdapter(nc, enabled=True).call_tool(
            "worker",
            "write_file",
            {"path": "x", "content": "y"},
        )
    assert nc.calls == 1


class StreamSubscription:
    def __init__(self):
        self.messages: asyncio.Queue = asyncio.Queue()

    async def next_msg(self, timeout):
        return await asyncio.wait_for(self.messages.get(), timeout)

    async def unsubscribe(self):
        return None


class PrivateStreamNC:
    def __init__(self):
        self.subscription = StreamSubscription()
        self.published: list[tuple[str, str]] = []

    def new_inbox(self):
        return "_INBOX.caller.private"

    async def subscribe(self, subject):
        assert subject == "_INBOX.caller.private"
        return self.subscription

    async def publish(self, subject, payload, reply=None):
        request = json.loads(payload)
        self.published.append((subject, reply))
        await self.subscription.messages.put(
            SimpleNamespace(
                data=json.dumps(
                    {"kind": "task-id", "id": "stream-task", "contextId": "ctx"}
                ).encode()
            )
        )
        await self.subscription.messages.put(
            SimpleNamespace(
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "id": "stream-task",
                            "status": {"state": "completed"},
                        },
                    }
                ).encode()
            )
        )

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_streaming_uses_private_request_inbox_not_public_stream_subject():
    nc = PrivateStreamNC()
    task_id, events = await LegacyMeshClientAdapter(nc, enabled=True).send_message_stream(
        "worker",
        Message(role="user", parts=[TextPart(text="hello")]),
        task_id="stream-task",
    )
    assert task_id == "stream-task"
    assert [event["kind"] for event in events] == ["task-id"]
    assert nc.published == [("a2a.rpc.worker", "_INBOX.caller.private")]


class PidAdapter:
    name = "pid"
    binary = "bash"

    def __init__(self, pid_file: Path):
        self.pid_file = pid_file

    def command(self, prompt, workdir, opts):
        return [
            "/bin/bash",
            "-c",
            f"printf '%s' $$ > {self.pid_file}; sleep 30",
        ]

    def resume_command(self, session_id, prompt, workdir, opts):
        return self.command(prompt, workdir, opts)

    def parse(self, stdout, stderr, rc):
        return TaskResult(ok=rc == 0, output="done")


@pytest.mark.asyncio
async def test_canceling_executor_coroutine_terminates_process_group(tmp_path):
    pid_file = tmp_path / "pid"
    executor = Executor({"pid": PidAdapter(pid_file)}, "pid", timeout=30)
    invocation = asyncio.create_task(executor.run("pid", "", str(tmp_path)))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    invocation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await invocation
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_executor_timeout_covers_slow_stream_callback(tmp_path):
    pid_file = tmp_path / "slow-callback-pid"
    executor = Executor({"pid": PidAdapter(pid_file)}, "pid", timeout=0.1)

    async def stuck_callback(event):
        await asyncio.sleep(30)

    result = await asyncio.wait_for(
        executor.run("pid", "", str(tmp_path), on_stream=stuck_callback),
        timeout=2,
    )
    assert result == TaskResult(ok=False, output="timeout")


class BlockingExecutor:
    def __init__(self):
        self.adapters = {"hermes": object()}
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return TaskResult(ok=True, output="done")


class RaisingExecutor:
    def __init__(self):
        self.adapters = {"hermes": object()}

    async def run(self, *args, **kwargs):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_duplicate_task_id_executes_once_and_conflicting_payload_is_rejected(tmp_path):
    runtime = AgentRuntime(make_config(tmp_path))
    executor = BlockingExecutor()
    runtime.executor = executor
    params = {
        "message": Message(role="user", parts=[TextPart(text="same")]).model_dump(),
        "metadata": {"taskId": "stable"},
    }
    first = asyncio.create_task(runtime.handle_task(params))
    await executor.entered.wait()
    second = asyncio.create_task(runtime.handle_task(params))
    await asyncio.sleep(0)
    executor.release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert executor.calls == 1
    assert first_result == second_result

    conflicting = {
        "message": Message(role="user", parts=[TextPart(text="different")]).model_dump(),
        "metadata": {"taskId": "stable"},
    }
    with pytest.raises(JsonRpcError) as exc:
        await runtime.handle_task(conflicting)
    assert exc.value.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_stream_failure_records_terminal_task_and_cleans_runtime_state(tmp_path):
    runtime = AgentRuntime(make_config(tmp_path))
    runtime.executor = RaisingExecutor()
    runtime._to_dlq = lambda *args: asyncio.sleep(0)
    events: list[dict] = []

    async def emit(event: dict) -> None:
        events.append(event)

    result = await runtime.handle_task_stream(
        {
            "message": Message(role="user", parts=[TextPart(text="x")]).model_dump(),
            "metadata": {"taskId": "stream-fail"},
        },
        emit,
    )
    assert result["status"]["state"] == "failed"
    assert runtime._tasks["stream-fail"].status.state == "failed"
    assert "stream-fail" not in runtime._cancels
    assert "stream-fail" not in runtime._inflight
    assert events[-1]["kind"] == "status-update"
    assert events[-1]["status"]["state"] == "failed"


class SpyExecutor:
    def __init__(self):
        self.adapters = {"hermes": object()}
        self.workdirs: list[str | None] = []
        self.options: list[dict] = []

    async def run(self, runtime, prompt, workdir, opts, *args, **kwargs):
        self.workdirs.append(workdir)
        self.options.append(opts)
        return TaskResult(ok=True, output="ok")


@pytest.mark.asyncio
async def test_remote_workdir_is_confined_to_configured_workspace(tmp_path):
    runtime = AgentRuntime(make_config(tmp_path))
    runtime.executor = SpyExecutor()
    params = {
        "message": Message(role="user", parts=[TextPart(text="x")]).model_dump(),
        "metadata": {"taskId": "outside", "workdir": str(tmp_path.parent)},
    }
    with pytest.raises(JsonRpcError) as exc:
        await runtime.handle_task(params)
    assert exc.value.code == FORBIDDEN
    assert runtime.executor.workdirs == []


@pytest.mark.asyncio
async def test_remote_metadata_cannot_enable_dangerous_runtime_flags(tmp_path):
    runtime = AgentRuntime(make_config(tmp_path))
    executor = SpyExecutor()
    runtime.executor = executor
    await runtime.handle_task(
        {
            "message": Message(role="user", parts=[TextPart(text="x")]).model_dump(),
            "metadata": {
                "taskId": "safe-options",
                "danger_full_access": True,
                "full_auto": True,
                "output_json": True,
            },
        }
    )
    assert executor.options == [{}]


class RecordingDispatchClient:
    def __init__(self):
        self.kwargs: list[dict] = []

    async def send_message(self, *args, **kwargs):
        self.kwargs.append(kwargs)
        return Task(id="remote", status=TaskStatus(state="completed"))


@pytest.mark.asyncio
async def test_dispatcher_rejects_precompleted_steps_and_uses_one_stable_attempt_id():
    dispatcher = Dispatcher(RecordingDispatchClient())
    with pytest.raises(ValueError):
        await dispatcher.run(
            Plan(
                task_id="bad",
                steps=[Step(id="s", target="a", prompt="x", status="succeeded")],
            )
        )

    plan = Plan(task_id="plan", steps=[Step(id="s", target="a", prompt="x")])
    await dispatcher.run(plan)
    assert dispatcher.client.kwargs == [
        {"runtime": None, "task_id": "plan:s", "retries": 1}
    ]


def test_cli_adapter_argv_matches_current_noninteractive_interfaces():
    assert HermesAdapter().resume_command("sid", "p", None, {}) == [
        "hermes", "chat", "--resume", "sid", "-q", "p", "-Q"
    ]
    assert CodexAdapter().command("p", None, {"full_auto": True}) == [
        "codex", "exec", "--approve-for-me", "p"
    ]
    assert CodexAdapter().resume_command("sid", "p", None, {}) == [
        "codex", "exec", "resume", "sid", "p"
    ]
    assert ClaudeAdapter().command("p", None, {"output_json": True}) == [
        "claude", "-p", "--output-format", "json", "p"
    ]
    assert OpenCodeAdapter().resume_command("sid", "p", None, {}) == [
        "opencode", "run", "--session", "sid", "p"
    ]


def test_config_forbids_typos_and_multiline_urls():
    with pytest.raises(ValidationError):
        Config.model_validate(
            {"nats": {"urls": "nats://typo:4222"}, "agent": {"name": "x"}}
        )
    with pytest.raises(ValidationError):
        Config.model_validate(
            {"nats": {"url": "nats://ok:4222\ninjected: true"}, "agent": {"name": "x"}}
        )


def test_init_and_bootstrap_keep_secret_private_and_do_not_rotate_without_force(tmp_path):
    init_args = argparse.Namespace(name="safe", nats="nats://127.0.0.1:4222", dir=str(tmp_path))
    assert cmd_init(init_args) == 0
    env_path = tmp_path / ".env"
    assert os.stat(env_path).st_mode & 0o777 == 0o600

    bootstrap_args = argparse.Namespace(dir=str(tmp_path), env="A2AMESH_NKEY_SEED", force=False)
    assert cmd_bootstrap(bootstrap_args) == 0
    first = env_path.read_text(encoding="utf-8")
    assert os.stat(env_path).st_mode & 0o777 == 0o600
    assert cmd_bootstrap(bootstrap_args) == 0
    assert env_path.read_text(encoding="utf-8") == first


def test_unimplemented_ingress_fails_instead_of_reporting_success(capsys):
    assert cli_main(["ingress"]) != 0
    assert "尚未实现" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_tool_names():
    async def handler():
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(Tool("duplicate", "one", {"type": "object"}, handler))
    with pytest.raises(ValueError):
        registry.register(Tool("duplicate", "two", {"type": "object"}, handler))


@pytest.mark.asyncio
async def test_remote_high_risk_tool_is_denied_until_approval_exists():
    async def handler():
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        Tool(
            "danger",
            "danger",
            {"type": "object", "additionalProperties": False},
            handler,
            risk="high",
            public=True,
        )
    )
    with pytest.raises(JsonRpcError) as exc:
        await registry.call("danger", {}, remote=True)
    assert exc.value.code == FORBIDDEN


class FakeMsg:
    def __init__(self, request: dict):
        self.data = json.dumps(request).encode()
        self.responses: list[dict] = []
        self.reply = "_INBOX.test"

    async def respond(self, payload: bytes):
        self.responses.append(json.loads(payload))


class ErrorHandler:
    async def call_tool(self, params):
        raise RuntimeError("secret filesystem path: /private/token")


@pytest.mark.asyncio
async def test_server_unknown_method_and_internal_error_mapping():
    server = LegacyMeshServerAdapter(SimpleNamespace(), "x", ErrorHandler(), enabled=True)
    unknown = FakeMsg({"jsonrpc": "2.0", "id": "1", "method": "bogus/do", "params": {}})
    await server._on_rpc(unknown)
    assert unknown.responses[0]["error"]["code"] == METHOD_NOT_FOUND

    internal = FakeMsg(
        {
            "jsonrpc": "2.0",
            "id": "2",
            "method": "tools/call",
            "params": {"tool": "x", "arguments": {}},
        }
    )
    await server._on_rpc(internal)
    assert internal.responses[0]["error"] == {"code": -32603, "message": "internal error"}
