"""Hardening regressions for the C1-3 Core dispatch trust boundary."""

from __future__ import annotations

import asyncio

import pytest
from a2a.utils.errors import InvalidAgentResponseError, InvalidParamsError
from google.protobuf.empty_pb2 import Empty
from google.protobuf.message import Message as ProtobufMessage

from a2amesh import protocol
from a2amesh.core import (
    OPERATION_SPECS,
    CanonicalRequestContext,
    Operation,
    dispatch_streaming,
    dispatch_unary,
    validate_application_contract,
)
from a2amesh.identity import Principal


def make_context(*, generation: int = 1) -> CanonicalRequestContext:
    return CanonicalRequestContext(
        request_id="hardening-request-001",
        principal=Principal("agent:caller", "agent"),
        target_agent_id="worker-a",
        config_generation=generation,
    )


class ClaimedClass:
    """An object that abuses isinstance's __class__ compatibility hook."""

    def __init__(self, claimed_type: type[object]) -> None:
        self._claimed_type = claimed_type

    @property
    def __class__(self) -> type[object]:
        return self._claimed_type


class OneShotBadIterator:
    def __init__(self, item: object) -> None:
        self.item = item
        self.closed = False
        self._done = False

    def __aiter__(self) -> OneShotBadIterator:
        return self

    async def __anext__(self) -> object:
        if self._done:
            raise StopAsyncIteration
        self._done = True
        return self.item

    async def aclose(self) -> None:
        self.closed = True


class InvalidAnextIterator:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self) -> InvalidAnextIterator:
        return self

    def __anext__(self) -> int:
        return 1

    async def aclose(self) -> None:
        self.closed = True


class BadAwaitable:
    def __init__(self) -> None:
        self.closed = False
        self.inner = asyncio.sleep(3600)

    def __await__(self):
        raise TypeError("underlying-await-typeerror")

    def close(self) -> None:
        self.closed = True


class BadFuture(asyncio.Future[object]):
    def __await__(self):
        raise TypeError("underlying-future-typeerror")


@pytest.mark.asyncio
async def test_request_and_response_contracts_reject_isinstance_spoofs() -> None:
    class RequestApplication:
        def __init__(self) -> None:
            self.called = False

        async def get_task(self, request, context):
            del request, context
            self.called = True
            return protocol.Task(id="should-not-run")

    request_application = RequestApplication()
    with pytest.raises(InvalidParamsError):
        await dispatch_unary(
            request_application,
            Operation.GET_TASK,
            ClaimedClass(protocol.GetTaskRequest),
            make_context(),
        )
    assert request_application.called is False

    class ResponseApplication:
        async def get_task(self, request, context):
            del request, context
            return ClaimedClass(protocol.Task)

    with pytest.raises(InvalidAgentResponseError):
        await dispatch_unary(
            ResponseApplication(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            make_context(),
        )


@pytest.mark.asyncio
async def test_stream_item_contract_rejects_isinstance_spoof_and_closes_iterator() -> None:
    iterator = OneShotBadIterator(ClaimedClass(protocol.StreamResponse))

    class Application:
        def send_streaming_message(self, request, context):
            del request, context
            return iterator

    stream = dispatch_streaming(
        Application(),
        Operation.SEND_STREAMING_MESSAGE,
        protocol.SendMessageRequest(),
        make_context(),
    )
    with pytest.raises(InvalidAgentResponseError):
        await anext(stream)
    assert iterator.closed is True


@pytest.mark.asyncio
async def test_malformed_unary_handler_typeerror_maps_to_official_error() -> None:
    class Application:
        def get_task(self, request, context):
            del request, context
            raise TypeError("underlying-handler-typeerror")

    with pytest.raises(InvalidAgentResponseError):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            make_context(),
        )


@pytest.mark.asyncio
async def test_malformed_awaitable_is_closed_and_maps_to_official_error() -> None:
    awaitable = BadAwaitable()

    class Application:
        def get_task(self, request, context):
            del request, context
            return awaitable

    with pytest.raises(InvalidAgentResponseError):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            make_context(),
        )
    assert awaitable.closed is True
    assert awaitable.inner.cr_frame is None


@pytest.mark.asyncio
async def test_malformed_future_is_cancelled_and_maps_to_official_error() -> None:
    future = BadFuture()

    class Application:
        def get_task(self, request, context):
            del request, context
            return future

    with pytest.raises(InvalidAgentResponseError):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            make_context(),
        )
    assert future.cancelled() is True


@pytest.mark.asyncio
async def test_bad_async_iterator_next_typeerror_maps_and_closes_iterator() -> None:
    iterator = InvalidAnextIterator()

    class Application:
        def send_streaming_message(self, request, context):
            del request, context
            return iterator

    stream = dispatch_streaming(
        Application(),
        Operation.SEND_STREAMING_MESSAGE,
        protocol.SendMessageRequest(),
        make_context(),
    )
    with pytest.raises(InvalidAgentResponseError):
        await anext(stream)
    assert iterator.closed is True


@pytest.mark.asyncio
async def test_request_and_tenant_validation_precede_context_validation() -> None:
    class Application:
        async def get_task(self, request, context):
            del request, context
            return protocol.Task()

    with pytest.raises(InvalidParamsError, match="GetTaskRequest"):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.CancelTaskRequest(id="task-001"),
            object(),  # type: ignore[arg-type]
        )

    with pytest.raises(InvalidParamsError, match="tenant"):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001", tenant="forbidden"),
            object(),  # type: ignore[arg-type]
        )


def test_context_rejects_boolean_generation() -> None:
    with pytest.raises(ValueError, match="config_generation"):
        make_context(generation=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_core_contract_error_messages_are_bounded() -> None:
    huge_type = type("X" * 10_000, (), {})

    class Application:
        async def get_task(self, request, context):
            del request, context
            return protocol.Task()

    with pytest.raises(InvalidParamsError) as captured:
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            huge_type(),
            make_context(),
        )
    assert len(captured.value.message) <= 512


def test_validate_application_contract_rejects_sync_only_handlers() -> None:
    class CallableOnlyApplication:
        pass

    application = CallableOnlyApplication()
    for spec in OPERATION_SPECS.values():
        setattr(application, spec.handler_name, lambda request, context: None)

    with pytest.raises(InvalidAgentResponseError, match="handler"):
        validate_application_contract(application)


EXPECTED_MATRIX: dict[
    Operation, tuple[str, type[ProtobufMessage], type[ProtobufMessage], str, bool]
] = {
    Operation.SEND_MESSAGE: (
        "API-A2A-001",
        protocol.SendMessageRequest,
        protocol.SendMessageResponse,
        "send_message",
        False,
    ),
    Operation.SEND_STREAMING_MESSAGE: (
        "API-A2A-002",
        protocol.SendMessageRequest,
        protocol.StreamResponse,
        "send_streaming_message",
        True,
    ),
    Operation.GET_TASK: (
        "API-A2A-003",
        protocol.GetTaskRequest,
        protocol.Task,
        "get_task",
        False,
    ),
    Operation.LIST_TASKS: (
        "API-A2A-004",
        protocol.ListTasksRequest,
        protocol.ListTasksResponse,
        "list_tasks",
        False,
    ),
    Operation.CANCEL_TASK: (
        "API-A2A-005",
        protocol.CancelTaskRequest,
        protocol.Task,
        "cancel_task",
        False,
    ),
    Operation.SUBSCRIBE_TO_TASK: (
        "API-A2A-006",
        protocol.SubscribeToTaskRequest,
        protocol.StreamResponse,
        "subscribe_to_task",
        True,
    ),
    Operation.CREATE_TASK_PUSH_NOTIFICATION_CONFIG: (
        "API-A2A-007",
        protocol.TaskPushNotificationConfig,
        protocol.TaskPushNotificationConfig,
        "create_task_push_notification_config",
        False,
    ),
    Operation.GET_TASK_PUSH_NOTIFICATION_CONFIG: (
        "API-A2A-008",
        protocol.GetTaskPushNotificationConfigRequest,
        protocol.TaskPushNotificationConfig,
        "get_task_push_notification_config",
        False,
    ),
    Operation.LIST_TASK_PUSH_NOTIFICATION_CONFIGS: (
        "API-A2A-009",
        protocol.ListTaskPushNotificationConfigsRequest,
        protocol.ListTaskPushNotificationConfigsResponse,
        "list_task_push_notification_configs",
        False,
    ),
    Operation.DELETE_TASK_PUSH_NOTIFICATION_CONFIG: (
        "API-A2A-010",
        protocol.DeleteTaskPushNotificationConfigRequest,
        Empty,
        "delete_task_push_notification_config",
        False,
    ),
    Operation.GET_EXTENDED_AGENT_CARD: (
        "API-A2A-011",
        protocol.GetExtendedAgentCardRequest,
        protocol.AgentCard,
        "get_extended_agent_card",
        False,
    ),
}


def test_registry_matches_independent_official_operation_matrix() -> None:
    assert dict(
        (
            operation,
            (
                spec.api_id,
                spec.request_type,
                spec.response_type,
                spec.handler_name,
                spec.streaming,
            ),
        )
        for operation, spec in OPERATION_SPECS.items()
    ) == EXPECTED_MATRIX
