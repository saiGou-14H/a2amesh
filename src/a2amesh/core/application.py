"""Binding-independent application-core contracts for all eleven A2A operations."""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol, cast

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
        if not isinstance(self.config_generation, int) or not (
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


def _resolve_operation_spec(operation: Operation) -> OperationSpec:
    if not isinstance(operation, Operation):
        raise InvalidParamsError(message=f"unknown canonical operation: {operation!r}")
    return OPERATION_SPECS[operation]


def _validate_request(
    operation: Operation, spec: OperationSpec, request: ProtobufMessage
) -> None:
    if not isinstance(request, spec.request_type):
        raise InvalidParamsError(
            message=(
                f"{operation.value} requires {spec.request_type.__name__}, "
                f"got {type(request).__name__}"
            )
        )
    if getattr(request, "tenant", ""):
        raise InvalidParamsError(message="non-empty tenant is not supported by A2AMesh V1")


def _validate_context(context: object) -> CanonicalRequestContext:
    if not isinstance(context, CanonicalRequestContext):
        raise InvalidParamsError(
            message="application dispatch requires a CanonicalRequestContext"
        )
    return context


def _handler_for(
    application: object, spec: OperationSpec
) -> Callable[..., object]:
    handler = getattr(application, spec.handler_name, None)
    if not callable(handler):
        raise InvalidAgentResponseError(
            message=f"canonical application does not implement {spec.handler_name}"
        )
    return cast(Callable[..., object], handler)


def _validate_response(
    operation: Operation, spec: OperationSpec, result: object
) -> ProtobufMessage:
    if not isinstance(result, spec.response_type):
        raise InvalidAgentResponseError(
            message=(
                f"{operation.value} must return {spec.response_type.__name__}, "
                f"got {type(result).__name__}"
            )
        )
    return cast(ProtobufMessage, result)


def validate_application_contract(application: object) -> None:
    """Fail closed when a semantic application omits any official operation handler."""
    missing = [
        operation.value
        for operation, spec in OPERATION_SPECS.items()
        if not callable(getattr(application, spec.handler_name, None))
    ]
    if missing:
        raise InvalidAgentResponseError(
            message=f"canonical application is missing handlers: {', '.join(missing)}"
        )


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
    context = _validate_context(context)
    _validate_request(operation, spec, request)
    handler = _handler_for(application, spec)
    result = handler(request, context)
    if not inspect.isawaitable(result):
        raise InvalidAgentResponseError(
            message=f"{operation.value} handler must return an awaitable"
        )
    return _validate_response(operation, spec, await result)


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
    context = _validate_context(context)
    _validate_request(operation, spec, request)
    handler = _handler_for(application, spec)
    stream = handler(request, context)
    if inspect.isawaitable(stream):
        if inspect.iscoroutine(stream):
            stream.close()
        raise InvalidAgentResponseError(
            message=f"{operation.value} handler must return an async iterator"
        )
    try:
        iterator = aiter(cast(AsyncIterable[object], stream))
    except TypeError as exc:
        raise InvalidAgentResponseError(
            message=f"{operation.value} handler must return an async iterator"
        ) from exc
    async for item in iterator:
        yield _validate_response(operation, spec, item)
