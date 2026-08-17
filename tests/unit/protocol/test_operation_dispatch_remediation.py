"""RED regressions for C1-3 cleanup and static-contract validation."""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import Awaitable
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import pytest
from a2a.utils.errors import InvalidAgentResponseError, InvalidParamsError

from a2amesh import protocol
from a2amesh.bindings.nats_v1.envelope import BindingRequestEnvelope, BindingValidationError
from a2amesh.bindings.nats_v1.response import BindingError, BindingResponseEnvelope
from a2amesh.bindings.nats_v1.transport import (
    BindingRemoteError,
    _safe_a2a_error_fields,
    _safe_binding_error_fields,
)
from a2amesh.core import (
    OPERATION_SPECS,
    CanonicalRequestContext,
    Operation,
    dispatch_streaming,
    dispatch_unary,
    validate_application_contract,
)
from a2amesh.identity import Principal

FIXTURES = Path(__file__).parents[2] / "fixtures" / "a2a_v1"


def context() -> CanonicalRequestContext:
    return CanonicalRequestContext(
        request_id="cleanup-request-001",
        principal=Principal("agent:caller", "agent"),
        target_agent_id="worker-a",
        config_generation=1,
    )


class AsyncCloseAwaitable:
    def __init__(self) -> None:
        self.closed = False

    def __await__(self):
        raise TypeError("malformed await protocol")

    async def close(self) -> None:
        self.closed = True


class MalformedCloseResult:
    def __init__(self) -> None:
        self.closed = False
        self.inner = asyncio.sleep(3600)

    def __await__(self):
        raise RuntimeError("malformed close-result awaitable")

    def close(self) -> None:
        self.closed = True


class NestedCloseAwaitable:
    def __init__(self) -> None:
        self.result: MalformedCloseResult | None = None

    def __await__(self):
        raise TypeError("malformed outer awaitable")

    def close(self) -> MalformedCloseResult:
        self.result = MalformedCloseResult()
        return self.result


class CancelRaisesFuture(asyncio.Future[object]):
    def cancel(self, msg: object = None) -> bool:
        del msg
        raise RuntimeError("cancel exploded")

    def __await__(self):
        raise TypeError("malformed future protocol")


class WrappedOnlyAwaitable:
    def __init__(self) -> None:
        self.closed = False
        self.wrapped = asyncio.sleep(3600)

    def __await__(self):
        raise RuntimeError("await protocol runtime")

    def close(self) -> None:
        self.closed = True


class BadAiter:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closed = False

    def __aiter__(self):
        raise self.error

    async def aclose(self) -> None:
        self.closed = True


class BadNextAwaitable:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closed = False
        self.inner = asyncio.sleep(3600)

    def __await__(self):
        raise self.error

    def close(self) -> None:
        self.closed = True


class BadNextIterator:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.returned: BadNextAwaitable | None = None
        self.closed = False

    def __aiter__(self):
        return self

    def __anext__(self):
        self.returned = BadNextAwaitable(self.error)
        return self.returned

    async def aclose(self) -> None:
        self.closed = True


class MissingAiter:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FalseCancelFuture(asyncio.Future[object]):
    def cancel(self, msg: object = None) -> bool:
        del msg
        return False

    def __await__(self):
        raise TypeError("malformed pending future")


class BlockingClose:
    def __init__(self) -> None:
        self.started = False

    def __await__(self):
        raise TypeError("malformed blocking close")

    def close(self):
        async def wait_forever() -> None:
            self.started = True
            await asyncio.Event().wait()

        return wait_forever()


class DistinctIterator:
    def __init__(self) -> None:
        self.closed = False
        self.done = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.done:
            raise StopAsyncIteration
        self.done = True
        return protocol.StreamResponse()

    async def aclose(self) -> None:
        self.closed = True


class DistinctOwner:
    def __init__(self) -> None:
        self.iterator = DistinctIterator()
        self.closed = False

    def __aiter__(self):
        return self.iterator

    async def aclose(self) -> None:
        self.closed = True


class UnaryCallable:
    async def __call__(self, request, request_context):
        del request, request_context
        return protocol.Task()


class StreamingCallable:
    async def __call__(self, request, request_context):
        del request, request_context
        yield protocol.StreamResponse()


@pytest.mark.asyncio
async def test_async_close_result_is_awaited_during_cleanup() -> None:
    value = AsyncCloseAwaitable()

    class Application:
        def get_task(self, request, request_context):
            del request, request_context
            return value

    with pytest.raises(InvalidAgentResponseError):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )
    assert value.closed is True


@pytest.mark.asyncio
async def test_custom_close_result_and_its_wrapped_coroutine_are_closed() -> None:
    value = NestedCloseAwaitable()

    class Application:
        def get_task(self, request, request_context):
            del request, request_context
            return value

    with pytest.raises(InvalidAgentResponseError):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )
    assert value.result is not None
    assert value.result.closed is True
    assert value.result.inner.cr_frame is None


@pytest.mark.asyncio
async def test_cleanup_cancel_failure_cannot_escape_as_runtime_error() -> None:
    value = CancelRaisesFuture()

    class Application:
        def get_task(self, request, request_context):
            del request, request_context
            return value

    with pytest.raises(InvalidAgentResponseError):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )


@pytest.mark.asyncio
async def test_runtime_malformed_awaitable_preserves_error_and_closes_wrapped_coroutine() -> None:
    value = WrappedOnlyAwaitable()

    class Application:
        def get_task(self, request, request_context):
            del request, request_context
            return value

    with pytest.raises(RuntimeError, match="await protocol runtime"):
        await dispatch_unary(
            Application(),
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-001"),
            context(),
        )
    assert value.closed is True
    assert value.wrapped.cr_frame is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TypeError("bad aiter"), RuntimeError("aiter runtime")])
async def test_aiter_failure_closes_original_stream_and_preserves_runtime(
    error: BaseException,
) -> None:
    value = BadAiter(error)

    class Application:
        def send_streaming_message(self, request, request_context):
            del request, request_context
            return value

    stream = dispatch_streaming(
        Application(),
        Operation.SEND_STREAMING_MESSAGE,
        protocol.SendMessageRequest(),
        context(),
    )
    if isinstance(error, TypeError):
        with pytest.raises(InvalidAgentResponseError):
            await anext(stream)
    else:
        with pytest.raises(RuntimeError, match="aiter runtime"):
            await anext(stream)
    assert value.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [TypeError("bad next await"), RuntimeError("next await runtime")],
)
async def test_malformed_anext_awaitable_is_closed_with_its_wrapped_coroutine(
    error: BaseException,
) -> None:
    value = BadNextIterator(error)

    class Application:
        def send_streaming_message(self, request, request_context):
            del request, request_context
            return value

    stream = dispatch_streaming(
        Application(),
        Operation.SEND_STREAMING_MESSAGE,
        protocol.SendMessageRequest(),
        context(),
    )
    if isinstance(error, TypeError):
        with pytest.raises(InvalidAgentResponseError):
            await anext(stream)
    else:
        with pytest.raises(RuntimeError, match="next await runtime"):
            await anext(stream)
    assert value.closed is True
    assert value.returned is not None
    assert value.returned.closed is True
    assert value.returned.inner.cr_frame is None


@pytest.mark.asyncio
async def test_missing_aiter_is_mapped_and_owner_is_closed() -> None:
    value = MissingAiter()

    class Application:
        def send_streaming_message(self, request, request_context):
            del request, request_context
            return value

    with pytest.raises(InvalidAgentResponseError):
        await anext(
            dispatch_streaming(
                Application(),
                Operation.SEND_STREAMING_MESSAGE,
                protocol.SendMessageRequest(),
                context(),
            )
        )
    assert value.closed is True


@pytest.mark.asyncio
async def test_distinct_stream_owner_and_iterator_are_both_closed() -> None:
    value = DistinctOwner()

    class Application:
        def send_streaming_message(self, request, request_context):
            del request, request_context
            return value

    stream = dispatch_streaming(
        Application(),
        Operation.SEND_STREAMING_MESSAGE,
        protocol.SendMessageRequest(),
        context(),
    )
    assert [item async for item in stream] == [protocol.StreamResponse()]
    assert value.iterator.closed is True
    assert value.closed is True


@pytest.mark.asyncio
async def test_cancel_false_does_not_leave_rejected_future_pending() -> None:
    value = FalseCancelFuture()

    class Application:
        def get_task(self, request, request_context):
            del request, request_context
            return value

    try:
        with pytest.raises(InvalidAgentResponseError):
            await dispatch_unary(
                Application(), Operation.GET_TASK, protocol.GetTaskRequest(id="task"), context()
            )
        assert value.done() is True
    finally:
        if not value.done():
            asyncio.Future.cancel(value)


@pytest.mark.asyncio
async def test_blocking_cleanup_is_bounded_and_external_timeout_is_preserved() -> None:
    value = BlockingClose()

    class Application:
        def get_task(self, request, request_context):
            del request, request_context
            return value

    with pytest.raises(InvalidAgentResponseError):
        await asyncio.wait_for(
            dispatch_unary(
                Application(), Operation.GET_TASK, protocol.GetTaskRequest(id="task"), context()
            ),
            timeout=0.25,
        )
    assert value.started is True

    value = BlockingClose()
    task = asyncio.create_task(
        dispatch_unary(
            Application(), Operation.GET_TASK, protocol.GetTaskRequest(id="task"), context()
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_validator_handles_partial_callable_and_rejects_impossible_modalities() -> None:
    application = _valid_application()
    application.get_task = functools.partial(UnaryCallable())
    application.send_streaming_message = functools.partial(StreamingCallable())
    validate_application_contract(application)

    async def async_generator_handler(request, request_context):
        del request, request_context
        yield protocol.Task()

    async def coroutine_handler(
        request, request_context
    ) -> Annotated[Awaitable[protocol.StreamResponse], "wrong"]:
        del request, request_context
        return protocol.StreamResponse()

    application = _valid_application()
    application.get_task = async_generator_handler
    with pytest.raises(InvalidAgentResponseError, match="modality"):
        validate_application_contract(application)

    application = _valid_application()
    application.send_streaming_message = coroutine_handler
    with pytest.raises(InvalidAgentResponseError, match="modality"):
        validate_application_contract(application)


def _valid_application() -> object:
    application = type("Application", (), {})()

    async def unary(request, request_context):
        del request, request_context
        return protocol.Task()

    async def streaming(request, request_context):
        del request, request_context
        yield protocol.StreamResponse()

    for spec in OPERATION_SPECS.values():
        setattr(application, spec.handler_name, streaming if spec.streaming else unary)
    return application


def test_validator_rejects_false_string_annotation_and_wrong_arity() -> None:
    application = _valid_application()

    def false_name(request, request_context):
        del request, request_context
        return protocol.Task()

    false_name.__annotations__["return"] = "NotAnAwaitable"

    def wrong_arity(request) -> Awaitable[protocol.Task]:
        del request
        return asyncio.sleep(0)

    application.get_task = false_name
    with pytest.raises(InvalidAgentResponseError, match="modality"):
        validate_application_contract(application)

    application = _valid_application()
    application.get_task = wrong_arity
    with pytest.raises(InvalidAgentResponseError, match="modality"):
        validate_application_contract(application)


def test_validator_accepts_annotated_and_partial_async_handlers() -> None:
    application = _valid_application()

    async def annotated(request, request_context) -> Annotated[Awaitable[protocol.Task], "ok"]:
        del request, request_context
        return protocol.Task()

    async def plain_async(request, request_context):
        del request, request_context
        return protocol.Task()

    application.get_task = annotated
    application.list_tasks = functools.partial(plain_async)
    validate_application_contract(application)


@pytest.mark.asyncio
async def test_cleanup_regression_tests_use_real_dispatch_path() -> None:
    with pytest.raises(InvalidParamsError):
        await dispatch_unary(
            object(),
            Operation.GET_TASK,
            protocol.CancelTaskRequest(id="wrong"),
            context(),
        )


def test_active_request_boundary_rejects_bool_float_generation_and_spoof_payload() -> None:
    data = json.loads((FIXTURES / "nats_send_message_request.json").read_text())
    envelope = BindingRequestEnvelope.from_dict(data)
    with pytest.raises(BindingValidationError, match="configGeneration"):
        replace(envelope, config_generation=True)
    with pytest.raises(BindingValidationError, match="configGeneration"):
        replace(envelope, config_generation=1.0)

    class Claimed:
        @property
        def __class__(self):
            return protocol.SendMessageRequest

    with pytest.raises(BindingValidationError, match="payload"):
        replace(envelope, payload=Claimed())


def test_active_response_boundary_rejects_bool_float_and_spoof_payload() -> None:
    for generation in (True, 1.0):
        with pytest.raises(BindingValidationError, match="configGeneration"):
            BindingResponseEnvelope(
                operation=Operation.GET_TASK,
                request_id="response-001",
                config_generation=generation,
                payload=protocol.Task(),
            )

    class Claimed:
        @property
        def __class__(self):
            return protocol.Task

    with pytest.raises(BindingValidationError, match="response payload"):
        BindingResponseEnvelope(
            operation=Operation.GET_TASK,
            request_id="response-001",
            config_generation=1,
            payload=Claimed(),
        )


def test_binding_error_retryable_is_exact_bool() -> None:
    with pytest.raises(BindingValidationError, match="retryable"):
        BindingError("InternalError", "fixed", 1)  # type: ignore[arg-type]


def test_binding_error_message_rejects_control_characters() -> None:
    with pytest.raises(BindingValidationError, match="message"):
        BindingError("InternalError", "safe-prefix\nFORGED-LOG\x00", False)

    valid = BindingResponseEnvelope(
        operation=Operation.GET_TASK,
        request_id="response-001",
        config_generation=1,
        error=BindingError("InternalError", "safe", False),
    ).to_dict()
    valid["error"]["message"] = "safe-prefix\nFORGED-LOG\x00"
    with pytest.raises(BindingValidationError, match="schema"):
        BindingResponseEnvelope.from_json_bytes(
            json.dumps(valid).encode(), Operation.GET_TASK
        )


def test_remote_error_sanitizes_a_forged_legacy_error_object() -> None:
    forged = object.__new__(BindingError)
    object.__setattr__(forged, "type", "InternalError")
    object.__setattr__(forged, "message", "safe-prefix\nFORGED-LOG\x00")
    object.__setattr__(forged, "retryable", False)
    rendered = str(BindingRemoteError(forged))
    assert "FORGED-LOG" not in rendered
    assert "\\n" not in rendered
    assert "\\x00" not in rendered


def test_transport_error_mappers_never_stringify_or_hash_hostile_values() -> None:
    class DerivedUnknown(InvalidParamsError):
        def __str__(self) -> str:
            raise RuntimeError("must not stringify")

    class HostileHash(str):
        def __hash__(self) -> int:
            raise RuntimeError("must not hash")

    assert _safe_a2a_error_fields(DerivedUnknown(message="secret")) == (
        "InternalError",
        "canonical application error",
    )
    assert _safe_binding_error_fields(HostileHash("InternalError"), object()) == (
        "InternalError",
        "canonical application error",
    )
