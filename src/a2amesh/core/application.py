"""Binding-independent application-core contracts for all eleven A2A operations."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast, get_origin

from google.protobuf.message import Message as ProtobufMessage

from a2amesh.identity import Principal
from a2amesh.protocol.errors import (
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
)

from .operations import OPERATION_SPECS, Operation, OperationSpec

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991
_MAX_ERROR_MESSAGE_LENGTH = 512
_GENERIC_CONTRACT_ERROR = "invalid application contract"


@dataclass(frozen=True, slots=True)
class CanonicalRequestContext:
    """Verified ingress facts supplied by a binding, never by request payload."""

    request_id: str
    principal: Principal
    target_agent_id: str
    config_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not _SAFE_TOKEN.fullmatch(self.request_id):
            raise ValueError("request_id must be a safe token")
        if not isinstance(self.principal, Principal):
            raise ValueError("principal must be a verified Canonical Principal")
        if not isinstance(self.target_agent_id, str) or not _AGENT_ID.fullmatch(
            self.target_agent_id
        ):
            raise ValueError("target_agent_id is invalid")
        if type(self.config_generation) is not int or not (
            1 <= self.config_generation <= _MAX_JSON_SAFE_INTEGER
        ):
            raise ValueError("config_generation must be a positive JSON-safe integer")

    @property
    def principal_id(self) -> str:
        """Compatibility projection for State calls and existing binding tests."""
        return self.principal.id

    @property
    def credential_id(self) -> str | None:
        """Return the verified credential reference without exposing credential material."""
        return self.principal.credential_id


class CanonicalApplication(Protocol):
    """One semantic implementation used by JSON-RPC, gRPC, NATS and MCP.

    Exact generated request/response classes are frozen in ``OPERATION_SPECS``.
    The pinned SDK's dynamic protobuf classes are runtime objects rather than
    static typing symbols, so the structural interface uses their common base;
    ``dispatch_unary`` enforces the exact pair at runtime.
    """

    async def send_message(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    def send_streaming_message(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> AsyncIterator[ProtobufMessage]: ...

    async def get_task(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    async def list_tasks(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    async def cancel_task(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    def subscribe_to_task(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> AsyncIterator[ProtobufMessage]: ...

    async def create_task_push_notification_config(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    async def get_task_push_notification_config(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    async def list_task_push_notification_configs(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    async def delete_task_push_notification_config(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...

    async def get_extended_agent_card(
        self, request: ProtobufMessage, context: CanonicalRequestContext
    ) -> ProtobufMessage: ...


def _bounded_error_message(message: object, fallback: str = _GENERIC_CONTRACT_ERROR) -> str:
    if (
        type(message) is not str
        or not message
        or len(message) > _MAX_ERROR_MESSAGE_LENGTH
        or not all(character.isprintable() for character in message)
    ):
        return fallback
    return message


def _safe_class_name(candidate: object, fallback: str = "official type") -> str:
    try:
        name = candidate.__name__  # type: ignore[attr-defined]
    except Exception:
        return fallback
    return _bounded_error_message(name, fallback)


def _safe_value_type_name(value: object) -> str:
    return _safe_class_name(type(value), "invalid value")


def _invalid_params(message: object) -> InvalidParamsError:
    return InvalidParamsError(message=_bounded_error_message(message))


def _invalid_response(message: object) -> InvalidAgentResponseError:
    return InvalidAgentResponseError(message=_bounded_error_message(message))


def _resolve_operation_spec(operation: Operation) -> OperationSpec:
    if not isinstance(operation, Operation):
        raise _invalid_params("unknown canonical operation")
    return OPERATION_SPECS[operation]


def _validate_request(
    operation: Operation, spec: OperationSpec, request: ProtobufMessage
) -> None:
    if type(request) is not spec.request_type:
        raise _invalid_params(
            f"{operation.value} requires {_safe_class_name(spec.request_type)}, "
            f"got {_safe_value_type_name(request)}"
        )
    if getattr(request, "tenant", ""):
        raise _invalid_params("non-empty tenant is not supported by A2AMesh V1")


def _validate_context(context: object) -> CanonicalRequestContext:
    if type(context) is not CanonicalRequestContext:
        raise _invalid_params("application dispatch requires a CanonicalRequestContext")
    return context


def _handler_for(
    application: object, spec: OperationSpec
) -> Callable[..., object]:
    handler = getattr(application, spec.handler_name, None)
    if not callable(handler):
        raise _invalid_response(f"canonical application does not implement {spec.handler_name}")
    return cast(Callable[..., object], handler)


def _validate_response(
    operation: Operation, spec: OperationSpec, result: object
) -> ProtobufMessage:
    if type(result) is not spec.response_type:
        raise _invalid_response(
            f"{operation.value} must return {_safe_class_name(spec.response_type)}, "
            f"got {_safe_value_type_name(result)}"
        )
    return cast(ProtobufMessage, result)


def _callable_target(handler: Callable[..., object]) -> object:
    if inspect.isroutine(handler):
        return handler
    try:
        return handler.__call__  # type: ignore[attr-defined]
    except AttributeError:
        return handler


def _return_annotation(handler: Callable[..., object]) -> object:
    try:
        return inspect.signature(handler).return_annotation
    except (TypeError, ValueError):
        return inspect.Signature.empty


def _annotation_has_origin_or_name(
    annotation: object, origins: tuple[object, ...], names: tuple[str, ...]
) -> bool:
    if annotation is inspect.Signature.empty:
        return False
    if isinstance(annotation, str):
        return any(name in annotation for name in names)
    return get_origin(annotation) in origins or annotation in origins


def _supports_unary_handler(handler: Callable[..., object]) -> bool:
    target = _callable_target(handler)
    if inspect.iscoroutinefunction(target):
        return True
    return _annotation_has_origin_or_name(
        _return_annotation(handler),
        (Awaitable,),
        ("Awaitable", "Coroutine"),
    )


def _supports_streaming_handler(handler: Callable[..., object]) -> bool:
    target = _callable_target(handler)
    if inspect.isasyncgenfunction(target):
        return True
    return _annotation_has_origin_or_name(
        _return_annotation(handler),
        (AsyncGenerator, AsyncIterable, AsyncIterator),
        ("AsyncGenerator", "AsyncIterable", "AsyncIterator"),
    )


def validate_application_contract(application: object) -> None:
    """Fail closed when handlers are missing or have an impossible modality."""
    missing: list[str] = []
    invalid_modality: list[str] = []
    for operation, spec in OPERATION_SPECS.items():
        handler = getattr(application, spec.handler_name, None)
        if not callable(handler):
            missing.append(operation.value)
            continue
        handler = cast(Callable[..., object], handler)
        valid = (
            _supports_streaming_handler(handler)
            if spec.streaming
            else _supports_unary_handler(handler)
        )
        if not valid:
            invalid_modality.append(operation.value)
    if missing:
        raise _invalid_response(
            f"canonical application is missing handlers: {', '.join(missing)}"
        )
    if invalid_modality:
        raise _invalid_response(
            f"canonical application has invalid handler modality: {', '.join(invalid_modality)}"
        )


def _close_awaitable(value: object) -> None:
    """Best-effort synchronous cleanup for a rejected awaitable."""

    def close_candidate(candidate: object) -> None:
        try:
            close = candidate.close  # type: ignore[attr-defined]
        except AttributeError:
            return
        if callable(close):
            try:
                close()
            except Exception:
                return

    pending: list[object] = [value]
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        if isinstance(candidate, asyncio.Future):
            candidate.cancel()
        close_candidate(candidate)
        for attribute in ("inner", "_inner", "coro", "_coro", "awaitable", "_awaitable"):
            try:
                nested = getattr(candidate, attribute, None)
            except Exception:
                nested = None
            if nested is not None and inspect.isawaitable(nested):
                pending.append(nested)


async def _close_async_iterator(iterator: object) -> None:
    try:
        close = getattr(iterator, "aclose", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            try:
                await result
            except Exception:
                _close_awaitable(result)
    except Exception:
        return


async def dispatch_unary(
    application: object,
    operation: Operation,
    request: ProtobufMessage,
    context: CanonicalRequestContext,
) -> ProtobufMessage:
    """Validate and dispatch one unary operation without reading binding metadata."""
    spec = _resolve_operation_spec(operation)
    if spec.streaming:
        raise InvalidRequestError(
            message=f"{operation.value} is streaming and requires streaming dispatch"
        )
    _validate_request(operation, spec, request)
    context = _validate_context(context)
    handler = _handler_for(application, spec)
    try:
        result = handler(request, context)
    except TypeError as exc:
        raise _invalid_response(f"{operation.value} handler invocation is malformed") from exc
    if not inspect.isawaitable(result):
        raise _invalid_response(f"{operation.value} handler must return an awaitable")
    try:
        awaited = await result
    except TypeError as exc:
        _close_awaitable(result)
        raise _invalid_response(
            f"{operation.value} handler returned a malformed awaitable"
        ) from exc
    return _validate_response(operation, spec, awaited)


async def dispatch_streaming(
    application: object,
    operation: Operation,
    request: ProtobufMessage,
    context: CanonicalRequestContext,
) -> AsyncIterator[ProtobufMessage]:
    """Validate and dispatch one streaming operation with exact item types."""
    spec = _resolve_operation_spec(operation)
    if not spec.streaming:
        raise InvalidRequestError(
            message=f"{operation.value} is unary and requires unary dispatch"
        )
    _validate_request(operation, spec, request)
    context = _validate_context(context)
    handler = _handler_for(application, spec)
    try:
        stream = handler(request, context)
    except TypeError as exc:
        raise _invalid_response(f"{operation.value} handler invocation is malformed") from exc
    if inspect.isawaitable(stream):
        _close_awaitable(stream)
        raise _invalid_response(f"{operation.value} handler must return an async iterator")
    try:
        iterator = aiter(cast(AsyncIterable[object], stream))
    except TypeError as exc:
        raise _invalid_response(f"{operation.value} handler must return an async iterator") from exc
    try:
        async for item in iterator:
            yield _validate_response(operation, spec, item)
    except TypeError as exc:
        raise _invalid_response(f"{operation.value} iterator produced an invalid item") from exc
    finally:
        await _close_async_iterator(iterator)
