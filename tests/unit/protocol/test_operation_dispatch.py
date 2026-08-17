"""C1-3 contracts for all-operation transport-independent dispatch."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a2a.utils.errors import (
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
)

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


def context() -> CanonicalRequestContext:
    return CanonicalRequestContext(
        request_id="request_stream_001",
        principal=Principal("agent:caller", "agent"),
        target_agent_id="worker-a",
        config_generation=1,
    )


class StreamingApplication:
    def send_streaming_message(
        self,
        request: protocol.SendMessageRequest,
        request_context: CanonicalRequestContext,
    ) -> AsyncIterator[protocol.StreamResponse]:
        async def events() -> AsyncIterator[protocol.StreamResponse]:
            yield protocol.StreamResponse()
            yield protocol.StreamResponse()

        return events()


@pytest.mark.asyncio
async def test_streaming_dispatch_validates_and_yields_official_responses() -> None:
    request = protocol.SendMessageRequest()
    stream = dispatch_streaming(
        StreamingApplication(),
        Operation.SEND_STREAMING_MESSAGE,
        request,
        context(),
    )

    responses = [item async for item in stream]

    assert responses == [protocol.StreamResponse(), protocol.StreamResponse()]


@pytest.mark.asyncio
async def test_streaming_dispatch_rejects_unary_operation() -> None:
    with pytest.raises(InvalidRequestError, match="unary"):
        stream = dispatch_streaming(
            StreamingApplication(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )
        await anext(stream)


class MatrixApplication:
    """Install one exact handler for every operation in the frozen registry."""

    def __init__(self) -> None:
        self.calls: list[Operation] = []
        for operation, spec in OPERATION_SPECS.items():
            factory = self._stream_handler if spec.streaming else self._unary_handler
            setattr(self, spec.handler_name, factory(operation, spec.response_type))

    def _unary_handler(self, operation: Operation, response_type):
        async def handler(request, request_context):
            del request, request_context
            self.calls.append(operation)
            return response_type()

        return handler

    def _stream_handler(self, operation: Operation, response_type):
        async def handler(request, request_context):
            del request, request_context
            self.calls.append(operation)
            yield response_type()

        return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", tuple(OPERATION_SPECS))
async def test_all_eleven_operations_dispatch_to_exact_official_contract(
    operation: Operation,
) -> None:
    application = MatrixApplication()
    validate_application_contract(application)
    spec = OPERATION_SPECS[operation]
    request = spec.request_type()

    if spec.streaming:
        responses = [
            item
            async for item in dispatch_streaming(
                application, operation, request, context()
            )
        ]
        assert len(responses) == 1
        assert isinstance(responses[0], spec.response_type)
    else:
        response = await dispatch_unary(application, operation, request, context())
        assert isinstance(response, spec.response_type)

    assert application.calls == [operation]


def test_validate_application_contract_fails_closed_for_any_missing_handler() -> None:
    application = MatrixApplication()
    delattr(application, "get_task")

    with pytest.raises(InvalidAgentResponseError, match="GetTask"):
        validate_application_contract(application)


@pytest.mark.asyncio
async def test_dispatch_fails_closed_when_unary_handler_is_not_awaitable() -> None:
    class SynchronousApplication:
        def get_task(self, request, request_context):
            del request, request_context
            return protocol.Task(id="wrong-modality")

    with pytest.raises(InvalidAgentResponseError, match="awaitable"):
        await dispatch_unary(
            SynchronousApplication(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )


@pytest.mark.asyncio
async def test_dispatch_fails_closed_when_stream_item_has_wrong_official_type() -> None:
    class WrongStreamApplication:
        def send_streaming_message(self, request, request_context):
            del request, request_context

            async def events():
                yield protocol.Task(id="wrong-stream-item")

            return events()

    with pytest.raises(InvalidAgentResponseError, match="StreamResponse"):
        responses = dispatch_streaming(
            WrongStreamApplication(),
            Operation.SEND_STREAMING_MESSAGE,
            protocol.SendMessageRequest(),
            context(),
        )
        await anext(responses)


@pytest.mark.asyncio
async def test_dispatch_fails_closed_when_stream_handler_returns_invalid_async_iterator() -> None:
    class InvalidIterator:
        def __aiter__(self):
            return object()

    class InvalidStreamApplication:
        def send_streaming_message(self, request, request_context):
            del request, request_context
            return InvalidIterator()

    with pytest.raises(InvalidAgentResponseError, match="async iterator"):
        responses = dispatch_streaming(
            InvalidStreamApplication(),
            Operation.SEND_STREAMING_MESSAGE,
            protocol.SendMessageRequest(),
            context(),
        )
        await anext(responses)


@pytest.mark.asyncio
async def test_dispatch_rejects_unknown_operation_before_handler_lookup() -> None:
    with pytest.raises(InvalidParamsError, match="unknown canonical operation"):
        await dispatch_unary(
            MatrixApplication(),
            "GetTask",  # type: ignore[arg-type]
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )


@pytest.mark.asyncio
async def test_dispatch_rejects_noncanonical_context_before_handler() -> None:
    application = MatrixApplication()

    with pytest.raises(InvalidParamsError, match="CanonicalRequestContext"):
        await dispatch_unary(
            application,
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            object(),  # type: ignore[arg-type]
        )
    assert application.calls == []
