"""Minimal real NATS transport for the signed A2A v1 unary binding.

This module intentionally contains no legacy subject or compatibility fallback.
Streaming operations use the dedicated stream-session transport contracts.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import uuid4

import nkeys

from a2amesh.core import OPERATION_SPECS, Operation, dispatch_unary
from a2amesh.core.application import CanonicalApplication, CanonicalRequestContext
from a2amesh.identity import SignerPolicy, nkey_public_key
from a2amesh.protocol import errors as protocol_errors
from a2amesh.protocol.errors import A2AError

from .auth import (
    BindingAuthVerifier,
    RequestReplayGuard,
    sign_request_envelope,
)
from .envelope import (
    AuthContext,
    AuthProof,
    BindingRequestEnvelope,
    BindingValidationError,
)
from .response import BindingError, BindingResponseEnvelope

_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_REPLY_PREFIX = re.compile(r"^_INBOX\.a2amesh\.[A-Za-z0-9_-]+\.$")
RPC_SUBJECT_PREFIX = "a2a.v1.rpc."
_A2A_ERROR_CLASSES = tuple(
    getattr(protocol_errors, name)
    for name in protocol_errors.__all__
    if name != "A2AError"
)


class BindingTransportError(RuntimeError):
    """Transport-level failure after the envelope has been parsed."""


class BindingRemoteError(BindingTransportError):
    """A valid v1 response carried a structured application/binding error."""

    def __init__(self, error: BindingError):
        super().__init__(f"{error.type}: {error.message}")
        self.error = error


@dataclass(frozen=True, slots=True)
class NatsCallerIdentity:
    """Connection-authenticated caller facts supplied by the NATS integration."""

    connection_public_key: str
    caller_agent_id: str
    caller_instance_id: str
    allowed_reply_prefix: str

    def __post_init__(self) -> None:
        if not self.connection_public_key.startswith("U"):
            raise ValueError("NATS caller connection key must be a user NKey")
        if not _AGENT_ID.fullmatch(self.caller_agent_id):
            raise ValueError("caller agent ID is invalid")
        if not re.fullmatch(r"^[A-Za-z0-9_-]{1,128}$", self.caller_instance_id):
            raise ValueError("caller instance ID is invalid")
        if not _REPLY_PREFIX.fullmatch(self.allowed_reply_prefix):
            raise ValueError("caller reply prefix is invalid")


class NatsCallerIdentityResolver(Protocol):
    def resolve(
        self,
        message: object,
        envelope: BindingRequestEnvelope,
    ) -> NatsCallerIdentity | Awaitable[NatsCallerIdentity]: ...


class NatsMessage(Protocol):
    data: bytes
    subject: str
    reply: str | None

    async def respond(self, payload: bytes) -> None: ...


class NatsSubscription(Protocol):
    async def unsubscribe(self) -> None: ...


class NatsServerConnection(Protocol):
    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        cb: Callable[[NatsMessage], Awaitable[None]] | None = None,
    ) -> NatsSubscription: ...


class ReplySubscription(Protocol):
    async def next_msg(self, timeout: float) -> NatsMessage: ...

    async def unsubscribe(self) -> None: ...


class NatsRequestConnection(Protocol):
    def new_inbox(self) -> str: ...

    async def subscribe(self, subject: str) -> ReplySubscription: ...

    async def publish(self, subject: str, payload: bytes, *, reply: str | None = None) -> None: ...

    async def flush(self) -> None: ...


_CANONICAL_A2A_MESSAGE = "canonical application error"
_BINDING_ERROR_MESSAGES = {
    "InvalidBindingRequest": "binding request failed",
    "BindingTransportError": "binding transport error",
    "InternalError": "canonical application dispatch failed",
}
_A2A_ERROR_NAMES = frozenset(candidate.__name__ for candidate in _A2A_ERROR_CLASSES)


def _safe_a2a_error_fields(error: A2AError) -> tuple[str, str]:
    """Map only known official errors to fixed, non-sensitive fields."""
    error_type = next(
        (candidate.__name__ for candidate in _A2A_ERROR_CLASSES if isinstance(error, candidate)),
        None,
    )
    if error_type is None:
        return "InternalError", _CANONICAL_A2A_MESSAGE
    return error_type, _CANONICAL_A2A_MESSAGE


def _safe_binding_error_fields(
    error_type: object, _error_message: object
) -> tuple[str, str]:
    if not isinstance(error_type, str):
        return "InternalError", _CANONICAL_A2A_MESSAGE
    if error_type in _BINDING_ERROR_MESSAGES:
        return error_type, _BINDING_ERROR_MESSAGES[error_type]
    if error_type in _A2A_ERROR_NAMES:
        return error_type, _CANONICAL_A2A_MESSAGE
    return "InternalError", _CANONICAL_A2A_MESSAGE


class V1NatsClient:
    """Signed unary A2A v1 client; no legacy fallback exists in this class."""

    def __init__(
        self,
        nc: NatsRequestConnection,
        *,
        key_pair: nkeys.KeyPair,
        principal_id: str,
        credential_id: str,
        caller_agent_id: str,
        caller_instance_id: str,
        issuer: str,
        subject: str,
        config_generation: int,
        auth_method: str = "nats-nkey",
        auth_ttl_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _AGENT_ID.fullmatch(caller_agent_id):
            raise ValueError("caller agent ID is invalid")
        if not re.fullmatch(r"^[A-Za-z0-9_-]{1,128}$", caller_instance_id):
            raise ValueError("caller instance ID is invalid")
        if (
            type(config_generation) is not int
            or not 1 <= config_generation <= 9_007_199_254_740_991
        ):
            raise ValueError("config generation is invalid")
        if not 1 <= auth_ttl_seconds <= 900:
            raise ValueError("AuthContext TTL must be between 1 and 900 seconds")
        if not nkey_public_key(key_pair).startswith("U"):
            raise ValueError("binding client key pair must be a user NKey")
        self.nc = nc
        self.key_pair = key_pair
        self.principal_id = principal_id
        self.credential_id = credential_id
        self.caller_agent_id = caller_agent_id
        self.caller_instance_id = caller_instance_id
        self.issuer = issuer
        self.subject = subject
        self.config_generation = config_generation
        self.auth_method = auth_method
        self.auth_ttl_seconds = auth_ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    async def request(
        self,
        operation: Operation,
        payload,
        *,
        target_agent_id: str,
        timeout: float = 30.0,
        request_id: str | None = None,
    ):
        if operation not in OPERATION_SPECS:
            raise ValueError("operation is not in the official A2A registry")
        if OPERATION_SPECS[operation].streaming:
            raise BindingTransportError(
                "streaming operations require StreamSession transport, not unary request"
            )
        if timeout < 1:
            raise ValueError("timeout must be at least one second")
        if not _AGENT_ID.fullmatch(target_agent_id):
            raise ValueError("target agent ID is invalid")

        sent_at = self._clock().astimezone(UTC)
        deadline_at = sent_at + timedelta(seconds=timeout)
        auth_expires_at = min(
            sent_at + timedelta(seconds=self.auth_ttl_seconds), deadline_at
        )
        reply_subject = self.nc.new_inbox()
        expected_prefix = f"_INBOX.a2amesh.{self.caller_agent_id}."
        if not reply_subject.startswith(expected_prefix):
            raise BindingTransportError(
                "NATS connection inbox is outside the authenticated caller prefix"
            )
        envelope = BindingRequestEnvelope(
            operation=operation,
            request_id=request_id or uuid4().hex,
            caller_instance_id=self.caller_instance_id,
            stream_open_id=None,
            config_generation=self.config_generation,
            caller_agent_id=self.caller_agent_id,
            auth_context=AuthContext(
                principal_id=self.principal_id,
                credential_id=self.credential_id,
                method=self.auth_method,
                issuer=self.issuer,
                subject=self.subject,
                issued_at=sent_at,
                expires_at=auth_expires_at,
            ),
            auth_proof=AuthProof(
                signer=nkey_public_key(self.key_pair),
                algorithm="nkey-ed25519",
                signature="unsigned",
            ),
            target_agent_id=target_agent_id,
            sent_at=sent_at,
            deadline_at=deadline_at,
            reply_subject=reply_subject,
            payload=payload,
        )
        signed = sign_request_envelope(envelope, self.key_pair)
        subscription = cast(ReplySubscription, await self.nc.subscribe(reply_subject))
        try:
            await self.nc.publish(
                f"{RPC_SUBJECT_PREFIX}{target_agent_id}",
                signed.to_json_bytes(),
                reply=reply_subject,
            )
            await self.nc.flush()
            message = await subscription.next_msg(timeout=timeout)
        except TimeoutError as exc:
            raise BindingTransportError("A2A v1 request timed out") from exc
        finally:
            await subscription.unsubscribe()

        if message.subject != reply_subject:
            raise BindingTransportError("response arrived on an unexpected NATS subject")
        response = BindingResponseEnvelope.from_json_bytes(message.data, operation)
        if response.request_id != signed.request_id:
            raise BindingTransportError("response requestId does not match request")
        if response.config_generation != self.config_generation:
            raise BindingTransportError("response configGeneration does not match request")
        if response.error is not None:
            raise BindingRemoteError(response.error)
        return response.payload

    async def send_message(self, request, *, target_agent_id: str, timeout: float = 30.0):
        return await self.request(
            Operation.SEND_MESSAGE,
            request,
            target_agent_id=target_agent_id,
            timeout=timeout,
        )

    async def get_task(self, request, *, target_agent_id: str, timeout: float = 30.0):
        return await self.request(
            Operation.GET_TASK,
            request,
            target_agent_id=target_agent_id,
            timeout=timeout,
        )


class V1NatsServer:
    """NATS v1 server dispatching verified official requests to CanonicalApplication."""

    def __init__(
        self,
        nc: NatsServerConnection,
        *,
        agent_id: str,
        application: CanonicalApplication,
        signer_policies: Mapping[str, SignerPolicy],
        replay_guard: RequestReplayGuard,
        identity_resolver: NatsCallerIdentityResolver,
        active_config_generation: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _AGENT_ID.fullmatch(agent_id):
            raise ValueError("agent ID is invalid")
        if (
            type(active_config_generation) is not int
            or not 1 <= active_config_generation <= 9_007_199_254_740_991
        ):
            raise ValueError("active config generation must be a positive safe integer")
        if application is None:
            raise ValueError("canonical application is required")
        if identity_resolver is None:
            raise ValueError("NATS caller identity resolver is required")
        self.nc = nc
        self.agent_id = agent_id
        self.application = application
        self.identity_resolver = identity_resolver
        self.active_config_generation = active_config_generation
        self.auth = BindingAuthVerifier(signer_policies, replay_guard)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._subscription: NatsSubscription | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def subject(self) -> str:
        return f"{RPC_SUBJECT_PREFIX}{self.agent_id}"

    async def start(self) -> None:
        if self._subscription is not None:
            raise RuntimeError("v1 NATS server is already started")
        self._subscription = await self.nc.subscribe(
            self.subject,
            queue=f"a2a-v1-worker-{self.agent_id}",
            cb=self._schedule,
        )

    async def _schedule(self, message: NatsMessage) -> None:
        task = asyncio.create_task(self._handle(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, message: NatsMessage) -> None:
        envelope: BindingRequestEnvelope | None = None
        try:
            envelope = BindingRequestEnvelope.from_json_bytes(message.data)
            if message.reply != envelope.reply_subject:
                raise BindingTransportError("NATS reply subject does not match signed replySubject")
            caller = self.identity_resolver.resolve(message, envelope)
            if inspect.isawaitable(caller):
                caller = await caller
            if not isinstance(caller, NatsCallerIdentity):
                raise BindingTransportError(
                    "NATS caller identity resolver returned an invalid identity"
                )
            verified = await self.auth.verify(
                envelope,
                received_subject=message.subject,
                connection_public_key=caller.connection_public_key,
                expected_target_agent_id=self.agent_id,
                expected_caller_agent_id=caller.caller_agent_id,
                expected_caller_instance_id=caller.caller_instance_id,
                allowed_reply_prefix=caller.allowed_reply_prefix,
                active_config_generation=self.active_config_generation,
                now=self._clock(),
            )
            spec = OPERATION_SPECS[envelope.operation]
            if spec.streaming:
                raise BindingTransportError(
                    "streaming operation must use the StreamSession transport"
                )
            context = CanonicalRequestContext(
                request_id=verified.request_id,
                principal=verified.principal,
                target_agent_id=self.agent_id,
                config_generation=envelope.config_generation,
            )
            result = await dispatch_unary(
                self.application,
                envelope.operation,
                envelope.payload,
                context,
            )
            response = BindingResponseEnvelope(
                operation=envelope.operation,
                request_id=envelope.request_id,
                config_generation=self.active_config_generation,
                payload=result,
            )
            await message.respond(response.to_json_bytes())
        except BindingValidationError:
            await self._respond_error(
                message,
                envelope,
                "InvalidBindingRequest",
                "binding request failed",
                retryable=False,
            )
        except BindingTransportError:
            await self._respond_error(
                message,
                envelope,
                "BindingTransportError",
                "binding transport error",
                retryable=False,
            )
        except A2AError as exc:
            error_type, error_message = _safe_a2a_error_fields(exc)
            await self._respond_error(
                message,
                envelope,
                error_type,
                error_message,
                retryable=False,
            )
        except Exception:
            await self._respond_error(
                message,
                envelope,
                "InternalError",
                "canonical application dispatch failed",
                retryable=True,
            )

    async def _respond_error(
        self,
        message: NatsMessage,
        envelope: BindingRequestEnvelope | None,
        error_type: str,
        error_message: str,
        *,
        retryable: bool,
    ) -> None:
        if envelope is None or message.reply != envelope.reply_subject:
            return
        error_type, error_message = _safe_binding_error_fields(error_type, error_message)
        try:
            response = BindingResponseEnvelope(
                operation=envelope.operation,
                request_id=envelope.request_id,
                config_generation=self.active_config_generation,
                error=BindingError(error_type, error_message, retryable),
            )
            await message.respond(response.to_json_bytes())
        except Exception:
            try:
                response = BindingResponseEnvelope(
                    operation=envelope.operation,
                    request_id=envelope.request_id,
                    config_generation=self.active_config_generation,
                    error=BindingError(
                        "InternalError",
                        "canonical application dispatch failed",
                        True,
                    ),
                )
                await message.respond(response.to_json_bytes())
            except Exception:
                return

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
