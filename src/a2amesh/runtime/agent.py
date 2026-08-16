"""AgentRuntime —— 统一对称 peer 进程。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

import nats
from a2amesh.a2anats.compatibility import (
    LegacyMeshClientAdapter,
    LegacyMeshServerAdapter,
)
from a2amesh.a2anats.errors import (
    FORBIDDEN,
    INVALID_PARAMS,
    UNAVAILABLE,
    JsonRpcError,
)
from a2amesh.config import Config
from a2amesh.contracts.models import (
    AgentCard,
    Artifact,
    Message,
    RuntimeCapability,
    Skill,
    Task,
    TaskStatus,
    TextPart,
)
from a2amesh.memory.store import MemoryStore
from a2amesh.runtime.adapters.registry import detect_adapters
from a2amesh.runtime.executor import Executor
from a2amesh.tools.registry import ToolRegistry


class AgentRuntime:
    def __init__(self, cfg: Config, adapters: dict | None = None):
        self.cfg = cfg
        self._adapters_override = adapters
        self.log = logging.LoggerAdapter(
            logging.getLogger("a2amesh.agent"), {"agent": cfg.agent.name})
        self.nc: nats.NATS | None = None
        self.tools = ToolRegistry()
        self.executor: Executor | None = None
        self.server: LegacyMeshServerAdapter | None = None
        self.client: LegacyMeshClientAdapter | None = None
        self.memory: MemoryStore | None = None
        self._tasks: dict[str, Task] = {}
        self._cancels: dict[str, asyncio.Event] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._task_futures: dict[str, asyncio.Future[Task]] = {}
        self._task_fingerprints: dict[str, str] = {}
        self._task_state_lock = asyncio.Lock()

    async def start(self):
        from a2amesh.logging_setup import setup_logging
        setup_logging(self.cfg.observability.log_level)
        seed = os.environ.get(self.cfg.nats.nkey_seed_env)
        kwargs = {"inbox_prefix": f"_INBOX.{self.cfg.agent.name}"}
        if seed:
            kwargs["nkeys_seed_str"] = seed
        self.nc = await nats.connect(self.cfg.nats.url, **kwargs)

        self.tools.load_builtin(workspace=self.cfg.agent.workdir)
        self.tools.load_custom(self.cfg.agent.tools_dir)
        await self.tools.connect_mcp(self.cfg.mcp)

        if self._adapters_override:
            adapters = self._adapters_override
        else:
            allowed = set(self.cfg.agent.runtimes)
            adapters = {k: v for k, v in detect_adapters().items() if k in allowed}
        self.executor = Executor(adapters, self.cfg.agent.default_runtime,
                                 timeout=self.cfg.agent.task_timeout_seconds)
        if self.cfg.agent.default_runtime not in adapters:
            await self.nc.close()
            raise RuntimeError(
                "configured default runtime is not installed: "
                f"{self.cfg.agent.default_runtime}"
            )
        self.memory = await MemoryStore.create(
            self.nc.jetstream(),
            self.cfg.agent.name,
            session_ttl_seconds=self.cfg.agent.session_ttl_seconds,
        )
        self._register_memory_tools()
        public = set(self.cfg.agent.public_tools)
        for tool in self.tools._tools.values():
            tool.public = tool.name in public
        legacy_enabled = self.cfg.compatibility.legacy_private_rpc_enabled
        self.server = LegacyMeshServerAdapter(
            self.nc,
            self.cfg.agent.name,
            handler=self,
            enabled=legacy_enabled,
        )
        self.client = LegacyMeshClientAdapter(self.nc, enabled=legacy_enabled)
        if legacy_enabled:
            await self.server.start()

    # ---- AgentCard ----

    def card(self) -> AgentCard:
        return AgentCard(
            name=self.cfg.agent.name,
            description=self.cfg.agent.description,
            capabilities={
                "runtimes": [
                    RuntimeCapability(name=k).model_dump()
                    for k in self.executor.adapters
                ],
                "default_runtime": self.cfg.agent.default_runtime,
                "tools": [t.model_dump() for t in self.tools.list()],
            },
            skills=[Skill(id=t.name, name=t.name, description=t.description)
                    for t in self.tools.list() if t.public],
        )

    # ---- 处理任务（被调度） ----

    @staticmethod
    def _task_fingerprint(message: Message, metadata: dict) -> str:
        relevant_metadata = {
            key: metadata.get(key)
            for key in ("runtime", "workdir", "sessionId")
        }
        payload = {
            "message": message.model_dump(mode="json"),
            "metadata": relevant_metadata,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    async def _claim_task(
        self, task_id: str, fingerprint: str, current: Task
    ) -> tuple[bool, asyncio.Future[Task]]:
        """Claim a task ID or join the already-running identical request."""
        async with self._task_state_lock:
            known = self._task_fingerprints.get(task_id)
            if known is not None:
                if known != fingerprint:
                    raise JsonRpcError(
                        INVALID_PARAMS,
                        f"task id already used with different payload: {task_id}",
                    )
                future = self._task_futures.get(task_id)
                if future is None:
                    future = asyncio.get_running_loop().create_future()
                    future.set_result(self._tasks[task_id])
                return False, future

            future = asyncio.get_running_loop().create_future()
            self._task_fingerprints[task_id] = fingerprint
            self._task_futures[task_id] = future
            self._tasks[task_id] = current
            return True, future

    async def _finish_task(self, task_id: str, final: Task) -> None:
        async with self._task_state_lock:
            self._tasks[task_id] = final
            future = self._task_futures.get(task_id)
            if future is not None and not future.done():
                future.set_result(final)

    def _resolve_workdir(self, requested: str | None) -> str | None:
        """Confine remotely selected working directories to the configured root."""
        configured = self.cfg.agent.workdir
        if configured is None:
            if requested is not None:
                raise JsonRpcError(
                    FORBIDDEN,
                    "remote workdir override requires agent.workdir to be configured",
                )
            return None
        root = Path(configured).expanduser().resolve()
        candidate = Path(requested).expanduser() if requested else root
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise JsonRpcError(FORBIDDEN, "workdir is outside configured workspace")
        return str(resolved)

    async def handle_task(self, params: dict) -> dict:
        msg = Message(**params["message"])
        meta = params.get("metadata") or {}
        runtime = meta.get("runtime") or self.cfg.agent.default_runtime
        if runtime not in self.executor.adapters:
            raise JsonRpcError(UNAVAILABLE, f"runtime not available: {runtime}")
        workdir = self._resolve_workdir(meta.get("workdir"))

        task_id = meta.get("taskId") or uuid4().hex
        session_id = meta.get("sessionId")
        fingerprint = self._task_fingerprint(msg, meta)
        current = Task(id=task_id, status=TaskStatus(state="working"), history=[msg])
        owner, future = await self._claim_task(task_id, fingerprint, current)
        if not owner:
            return (await asyncio.shield(future)).model_dump(mode="json")

        cancel_evt = asyncio.Event()
        self._cancels[task_id] = cancel_evt
        running = asyncio.current_task()
        if running is not None:
            self._inflight[task_id] = running

        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
        try:
            if session_id and self.memory:
                await self.memory.session_append(
                    session_id, msg.model_dump(mode="json")
                )
            result = await self.executor.run(
                runtime,
                text,
                workdir,
                {},
                session_id=session_id,
                cancel_evt=cancel_evt,
            )
            if cancel_evt.is_set() or result.output == "canceled":
                state = "canceled"
            else:
                state = "completed" if result.ok else "failed"
            final = Task(
                id=task_id,
                status=TaskStatus(state=state),
                history=[msg],
                artifacts=[
                    Artifact(
                        artifactId="a1",
                        parts=[TextPart(text=result.output)],
                    )
                ],
            )
        except asyncio.CancelledError:
            final = Task(
                id=task_id,
                status=TaskStatus(state="canceled"),
                history=[msg],
                artifacts=[
                    Artifact(
                        artifactId="a1", parts=[TextPart(text="canceled")]
                    )
                ],
            )
            await self._finish_task(task_id, final)
            raise
        except Exception as exc:
            self.log.error("task failed: %s", exc, extra={"task_id": task_id})
            await self._to_dlq(task_id, str(exc))
            final = Task(
                id=task_id,
                status=TaskStatus(state="failed"),
                history=[msg],
                artifacts=[
                    Artifact(
                        artifactId="a1",
                        parts=[TextPart(text="task execution failed")],
                    )
                ],
            )
        finally:
            self._cancels.pop(task_id, None)
            self._inflight.pop(task_id, None)

        await self._finish_task(task_id, final)
        if session_id and self.memory:
            try:
                await self.memory.session_append(
                    session_id,
                    Message(
                        role="agent", parts=final.artifacts[0].parts
                    ).model_dump(mode="json"),
                )
            except Exception:
                self.log.warning(
                    "failed to persist task response", extra={"task_id": task_id}
                )
        return final.model_dump(mode="json")

    async def _to_dlq(self, task_id: str, error: str):
        """终失败任务进死信队列 a2a.dlq.<agent>。"""
        payload = json.dumps({"task_id": task_id, "error": error,
                              "ts": int(time.time())}).encode()
        try:
            await self.nc.publish(f"a2a.dlq.{self.cfg.agent.name}", payload)
        except Exception:
            self.log.warning("dlq publish failed")

    async def handle_task_stream(self, params: dict, emit) -> dict:
        msg = Message(**params["message"])
        meta = params.get("metadata") or {}
        runtime = meta.get("runtime") or self.cfg.agent.default_runtime
        if runtime not in self.executor.adapters:
            raise JsonRpcError(UNAVAILABLE, f"runtime not available: {runtime}")
        workdir = self._resolve_workdir(meta.get("workdir"))
        task_id = meta.get("taskId") or uuid4().hex
        ctx = meta.get("sessionId") or task_id
        current = Task(id=task_id, status=TaskStatus(state="working"), history=[msg])
        fingerprint = self._task_fingerprint(msg, meta)
        owner, future = await self._claim_task(task_id, fingerprint, current)
        await emit({"kind": "task-id", "id": task_id, "contextId": ctx})
        if not owner:
            final = await asyncio.shield(future)
            await emit(
                {
                    "kind": "status-update",
                    "taskId": task_id,
                    "contextId": ctx,
                    "status": {"state": final.status.state},
                    "final": True,
                }
            )
            return final.model_dump(mode="json")

        cancel_evt = asyncio.Event()
        self._cancels[task_id] = cancel_evt
        running = asyncio.current_task()
        if running is not None:
            self._inflight[task_id] = running
        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
        cancelled = False

        async def on_stream(event):
            await emit({**event, "taskId": task_id, "contextId": ctx})

        try:
            result = await self.executor.run(
                runtime,
                text,
                workdir,
                {},
                session_id=meta.get("sessionId"),
                on_stream=on_stream,
                cancel_evt=cancel_evt,
            )
            state = (
                "canceled"
                if cancel_evt.is_set() or result.output == "canceled"
                else ("completed" if result.ok else "failed")
            )
            output = result.output
        except asyncio.CancelledError:
            state = "canceled"
            output = "canceled"
            cancelled = True
        except Exception as exc:
            self.log.error(
                "stream task failed: %s", exc, extra={"task_id": task_id}
            )
            await self._to_dlq(task_id, str(exc))
            state = "failed"
            output = "task execution failed"
        finally:
            self._cancels.pop(task_id, None)
            self._inflight.pop(task_id, None)

        final = Task(
            id=task_id,
            status=TaskStatus(state=state),
            history=[msg],
            artifacts=[Artifact(artifactId="a1", parts=[TextPart(text=output)])],
        )
        await self._finish_task(task_id, final)
        await emit(
            {
                "kind": "status-update",
                "taskId": task_id,
                "contextId": ctx,
                "status": {"state": state},
                "final": True,
            }
        )
        if cancelled:
            raise asyncio.CancelledError
        return final.model_dump(mode="json")

    async def get_task(self, params: dict) -> dict:
        task = self._tasks.get(params["id"])
        if not task:
            raise JsonRpcError(UNAVAILABLE, f"task not found: {params['id']}")
        return task.model_dump(mode="json")

    async def cancel(self, params: dict) -> dict:
        task_id = params["id"]
        event = self._cancels.get(task_id)
        task = self._tasks.get(task_id)
        future = self._task_futures.get(task_id)
        if (
            event is None
            or task is None
            or task.status.state != "working"
            or future is None
        ):
            raise JsonRpcError(UNAVAILABLE, f"task not cancelable: {task_id}")
        event.set()
        timeout = min(max(float(getattr(self.executor, "timeout", 30)), 1), 30) + 5
        try:
            final = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError as exc:
            raise JsonRpcError(
                UNAVAILABLE,
                f"task cancellation not confirmed: {task_id}",
            ) from exc
        return final.model_dump(mode="json")

    async def call_tool(self, params: dict) -> dict:
        return await self.tools.call(
            params["tool"], params.get("arguments", {}), remote=True
        )

    def _register_memory_tools(self) -> None:
        from a2amesh.tools.base import Tool

        if self.memory is None:
            raise RuntimeError("memory store is not initialized")

        async def memory_get(key: str) -> dict:
            return {"value": await self.memory.mem_get(key)}

        async def memory_set(key: str, value: str) -> dict:
            await self.memory.mem_set(key, value)
            return {"stored": True}

        async def shared_get(key: str) -> dict:
            return {"value": await self.memory.shared_get(key)}

        async def shared_set(key: str, value: str) -> dict:
            await self.memory.shared_set(key, value)
            return {"stored": True}

        key_schema = {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        }
        set_schema = {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        }
        for tool in (
            Tool("memory_get", "读取 agent 长期记忆", key_schema, memory_get, source="builtin"),
            Tool(
                "memory_set",
                "写入 agent 长期记忆",
                set_schema,
                memory_set,
                source="builtin",
                risk="medium",
            ),
            Tool("memory_shared_get", "读取团队共享记忆", key_schema, shared_get, source="builtin"),
            Tool(
                "memory_shared_set",
                "写入团队共享记忆",
                set_schema,
                shared_set,
                source="builtin",
                risk="medium",
            ),
        ):
            self.tools.register(tool)

    async def close(self) -> None:
        for task in list(self._inflight.values()):
            if not task.done():
                task.cancel()
        if self.server is not None:
            await self.server.close()
        await self.tools.close()
        if self.nc and not self.nc.is_closed:
            await self.nc.close()

    # ---- 调度别人 ----

    async def dispatch(self, target: str, prompt: str, runtime=None):
        return await self.client.send_message(
            target, Message(role="user", parts=[TextPart(text=prompt)]), runtime=runtime,
        )

    async def broadcast(self, prompt: str, runtime=None):
        return await self.client.broadcast(prompt, runtime=runtime)
