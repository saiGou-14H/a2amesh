"""MCP 2026-07-28 server bridge exposing allow-listed Mesh operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Protocol

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from a2amesh.contracts.models import Message, TextPart

_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class AgentSummary(BaseModel):
    agentId: str
    description: str
    skills: list[str] = Field(default_factory=list)


class AgentListResult(BaseModel):
    agents: list[AgentSummary]


class AgentCardResult(BaseModel):
    name: str
    description: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)


class TaskHandle(BaseModel):
    taskId: str
    state: str
    resourceUri: str
    deduplicationResult: str | None = None


class MeshClientProtocol(Protocol):
    async def discover(self) -> list[Any]: ...

    async def get_card(self, agent: str) -> Any: ...

    async def send_message(self, agent: str, message: Message, **kwargs) -> Any: ...

    async def get_task(self, agent: str, task_id: str) -> Any: ...

    async def cancel(self, agent: str, task_id: str) -> Any: ...


class MeshMCPBridge:
    """Map bounded MCP tools/resources to the existing Mesh client API."""

    def __init__(self, client: MeshClientProtocol, *, name: str = "A2AMesh") -> None:
        self.client = client
        self.server = MCPServer(
            name=name,
            description="A2AMesh task and agent bridge",
            version="0.1.0",
        )
        self._submissions: dict[tuple[str, str], str] = {}
        self._submission_lock = asyncio.Lock()
        self._register_tools()

    @staticmethod
    def _validate_target(target_agent_id: str) -> None:
        if not _AGENT_ID_RE.fullmatch(target_agent_id):
            raise ValueError("invalid targetAgentId")

    @staticmethod
    def _validate_message_id(message_id: str) -> None:
        if not _MESSAGE_ID_RE.fullmatch(message_id):
            raise ValueError("messageId must be 8-128 safe characters")

    @staticmethod
    def _task_result(task, deduplication_result: str | None = None) -> TaskHandle:
        return TaskHandle(
            taskId=task.id,
            state=task.status.state,
            resourceUri=f"a2amesh://tasks/{task.id}",
            deduplicationResult=deduplication_result,
        )

    def _register_tools(self) -> None:
        @self.server.tool(structured_output=True)
        async def mesh_list_agents() -> AgentListResult:
            """List currently discoverable Mesh agents and their public skills."""

            cards = await self.client.discover()
            return AgentListResult(
                agents=[
                    AgentSummary(
                        agentId=card.name,
                        description=card.description,
                        skills=[skill.id for skill in card.skills],
                    )
                    for card in cards
                ]
            )

        @self.server.tool(structured_output=True)
        async def mesh_get_agent(agentId: str) -> AgentCardResult:
            """Get one public Mesh Agent Card summary."""

            self._validate_target(agentId)
            card = await self.client.get_card(agentId)
            return AgentCardResult.model_validate(card.model_dump(mode="json"))

        @self.server.tool(structured_output=True)
        async def mesh_submit_task(
            targetAgentId: str,
            messageId: str,
            text: str,
            runtime: str | None = None,
        ) -> TaskHandle:
            """Submit an asynchronous Mesh task with a stable required message ID."""

            self._validate_target(targetAgentId)
            self._validate_message_id(messageId)
            if not text or len(text) > 65536:
                raise ValueError("text must contain 1-65536 characters")
            payload_hash = hashlib.sha256(
                json.dumps(
                    {"text": text, "runtime": runtime},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            key = (targetAgentId, messageId)
            async with self._submission_lock:
                existing = self._submissions.get(key)
                if existing and existing != payload_hash:
                    raise ValueError("messageId already used with different payload")
                duplicate = existing == payload_hash
                self._submissions[key] = payload_hash
            task = await self.client.send_message(
                targetAgentId,
                Message(role="user", parts=[TextPart(text=text)]),
                runtime=runtime,
                task_id=messageId,
            )
            return self._task_result(
                task,
                "DUPLICATE_SAME" if duplicate else "CREATED",
            )

        @self.server.tool(structured_output=True)
        async def mesh_get_task(targetAgentId: str, taskId: str) -> TaskHandle:
            """Read a Task visible through a specific target Agent."""

            self._validate_target(targetAgentId)
            task = await self.client.get_task(targetAgentId, taskId)
            return self._task_result(task)

        @self.server.tool(structured_output=True)
        async def mesh_cancel_task(targetAgentId: str, taskId: str) -> TaskHandle:
            """Explicitly cancel a running Mesh Task."""

            self._validate_target(targetAgentId)
            task = await self.client.cancel(targetAgentId, taskId)
            return self._task_result(task)


def create_mcp_server(client: MeshClientProtocol, *, name: str = "A2AMesh") -> MCPServer:
    """Create a configured MCPServer; caller chooses stdio or Streamable HTTP."""

    return MeshMCPBridge(client, name=name).server
