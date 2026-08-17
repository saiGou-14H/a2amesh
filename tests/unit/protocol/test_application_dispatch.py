"""C1 transport-independent contracts for trusted Core request dispatch."""

from __future__ import annotations

from dataclasses import fields

import pytest
from a2a.utils.errors import InvalidAgentResponseError, InvalidParamsError, InvalidRequestError

from a2amesh import protocol
from a2amesh.core import CanonicalRequestContext, Operation, dispatch_unary
from a2amesh.identity import Principal
from a2amesh.protocol.application import (
    CanonicalRequestContext as PublicRequestContext,
)
from a2amesh.protocol.application import (
    dispatch_unary as public_dispatch_unary,
)
from a2amesh.protocol.errors import InvalidParamsError as PublicInvalidParamsError


def context() -> CanonicalRequestContext:
    return CanonicalRequestContext(
        request_id="request_001",
        principal=Principal(
            id="a2a:client-001",
            kind="a2a",
            credential_id="credential-001",
            alias_generation=7,
        ),
        target_agent_id="worker-a",
        config_generation=42,
    )


class GetTaskApplication:
    def __init__(self, response: object | None = None) -> None:
        self.calls: list[tuple[protocol.GetTaskRequest, CanonicalRequestContext]] = []
        self.response = response

    async def get_task(
        self,
        request: protocol.GetTaskRequest,
        request_context: CanonicalRequestContext,
    ) -> object:
        self.calls.append((request, request_context))
        return self.response if self.response is not None else protocol.Task(id=request.id)


def test_canonical_context_contains_only_verified_transport_independent_facts() -> None:
    candidate = context()
    assert PublicRequestContext is CanonicalRequestContext
    assert public_dispatch_unary is dispatch_unary
    assert PublicInvalidParamsError is InvalidParamsError
    assert [field.name for field in fields(candidate)] == [
        "request_id",
        "principal",
        "target_agent_id",
        "config_generation",
    ]
    assert candidate.principal_id == "a2a:client-001"
    assert candidate.credential_id == "credential-001"
    assert candidate.principal.alias_generation == 7
    assert not hasattr(candidate, "token")
    assert not hasattr(candidate, "binding_metadata")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"request_id": "not safe!"}, "request_id"),
        ({"target_agent_id": "Invalid.Agent"}, "target_agent_id"),
        ({"config_generation": 0}, "config_generation"),
    ],
)
def test_canonical_context_rejects_invalid_ingress_facts(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "request_id": "request_001",
        "principal": Principal("agent:caller", "agent"),
        "target_agent_id": "worker-a",
        "config_generation": 42,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        CanonicalRequestContext(**values)


def test_canonical_context_rejects_boolean_config_generation() -> None:
    with pytest.raises(ValueError, match="config_generation"):
        CanonicalRequestContext(
            request_id="request_001",
            principal=Principal("agent:caller", "agent"),
            target_agent_id="worker-a",
            config_generation=True,
        )


@pytest.mark.asyncio
async def test_get_task_dispatch_uses_official_request_response_and_trusted_context() -> None:
    application = GetTaskApplication()
    request = protocol.GetTaskRequest(id="task-001")
    request_context = context()

    result = await dispatch_unary(application, Operation.GET_TASK, request, request_context)

    assert isinstance(result, protocol.Task)
    assert result.id == "task-001"
    assert application.calls == [(request, request_context)]


@pytest.mark.asyncio
async def test_dispatch_rejects_wrong_request_type_before_handler() -> None:
    application = GetTaskApplication()
    with pytest.raises(InvalidParamsError, match="GetTaskRequest"):
        await dispatch_unary(
            application,
            Operation.GET_TASK,
            protocol.CancelTaskRequest(id="task-001"),
            context(),
        )
    assert application.calls == []


@pytest.mark.asyncio
async def test_dispatch_rejects_nonempty_tenant_before_handler() -> None:
    application = GetTaskApplication()
    with pytest.raises(InvalidParamsError, match="tenant"):
        await dispatch_unary(
            application,
            Operation.GET_TASK,
            protocol.GetTaskRequest(tenant="forbidden", id="task-001"),
            context(),
        )
    assert application.calls == []


@pytest.mark.asyncio
async def test_dispatch_rejects_invalid_official_response_type() -> None:
    application = GetTaskApplication(response=protocol.SendMessageResponse())
    with pytest.raises(InvalidAgentResponseError, match="Task"):
        await dispatch_unary(
            application,
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )


@pytest.mark.asyncio
async def test_streaming_operation_cannot_use_unary_dispatch() -> None:
    application = GetTaskApplication()
    with pytest.raises(InvalidRequestError, match="streaming"):
        await dispatch_unary(
            application,
            Operation.SEND_STREAMING_MESSAGE,
            protocol.SendMessageRequest(),
            context(),
        )
