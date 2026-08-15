"""Canonical NATS stream-session open response and frame contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from typing import Any, cast

import jsonschema
import rfc8785
from google.protobuf.json_format import ParseError, SerializeToJsonError
from google.protobuf.message import Message as ProtobufMessage

from a2amesh import protocol
from a2amesh.core import Operation

from .envelope import BindingValidationError

STREAM_SCHEMA_VERSION = "1.0"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DELIVERY_SUBJECT = re.compile(
    r"^_DELIVER\.a2amesh\.stream\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STREAMING_OPERATIONS = {
    Operation.SEND_STREAMING_MESSAGE,
    Operation.SUBSCRIBE_TO_TASK,
}
_TERMINAL_STATES = {
    protocol.TaskState.TASK_STATE_COMPLETED,
    protocol.TaskState.TASK_STATE_FAILED,
    protocol.TaskState.TASK_STATE_CANCELED,
    protocol.TaskState.TASK_STATE_REJECTED,
}
_FRAME_FIELDS = frozenset(
    {
        "schemaVersion",
        "streamSessionId",
        "streamOpenId",
        "sequence",
        "eventSeq",
        "final",
        "canonicalStreamResponse",
        "payloadDigest",
    }
)
_OPENED_SCHEMA = json.loads(
    files("a2amesh.schemas")
    .joinpath("nats_stream_session_opened_v1.json")
    .read_text(encoding="utf-8")
)
_OPENED_VALIDATOR = jsonschema.Draft202012Validator(
    _OPENED_SCHEMA,
    format_checker=jsonschema.FormatChecker(),
)


@dataclass(frozen=True, slots=True)
class StreamSessionFrameV1:
    stream_session_id: str
    stream_open_id: str
    sequence: int
    event_seq: int
    final: bool
    canonical_stream_response: ProtobufMessage
    payload_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.stream_session_id, str) or not _SAFE_TOKEN.fullmatch(
            self.stream_session_id
        ):
            raise BindingValidationError("streamSessionId is not a safe token")
        if not isinstance(self.stream_open_id, str) or not _SAFE_TOKEN.fullmatch(
            self.stream_open_id
        ):
            raise BindingValidationError("streamOpenId is not a safe token")
        if (
            type(self.sequence) is not int
            or not 0 <= self.sequence <= 9_007_199_254_740_991
        ):
            raise BindingValidationError("sequence is outside JSON safe integer range")
        if (
            type(self.event_seq) is not int
            or not 0 <= self.event_seq <= 9_007_199_254_740_991
        ):
            raise BindingValidationError("eventSeq is outside JSON safe integer range")
        if type(self.final) is not bool:
            raise BindingValidationError("final must be boolean")
        if not isinstance(self.canonical_stream_response, protocol.StreamResponse):
            raise BindingValidationError("canonicalStreamResponse must be official StreamResponse")
        if not isinstance(self.payload_digest, str) or not _DIGEST.fullmatch(
            self.payload_digest
        ):
            raise BindingValidationError("payloadDigest must be lowercase SHA-256 hex")
        if self.payload_digest != self.compute_payload_digest():
            raise BindingValidationError("payloadDigest does not match canonical frame")

    @classmethod
    def create(
        cls,
        *,
        stream_session_id: str,
        stream_open_id: str,
        sequence: int,
        event_seq: int,
        final: bool,
        canonical_stream_response: ProtobufMessage,
    ) -> StreamSessionFrameV1:
        core = _frame_core_dict(
            stream_session_id=stream_session_id,
            stream_open_id=stream_open_id,
            sequence=sequence,
            event_seq=event_seq,
            final=final,
            canonical_stream_response=canonical_stream_response,
        )
        return cls(
            stream_session_id=stream_session_id,
            stream_open_id=stream_open_id,
            sequence=sequence,
            event_seq=event_seq,
            final=final,
            canonical_stream_response=canonical_stream_response,
            payload_digest=_sha256_rfc8785(core),
        )

    def core_dict(self) -> dict[str, Any]:
        return _frame_core_dict(
            stream_session_id=self.stream_session_id,
            stream_open_id=self.stream_open_id,
            sequence=self.sequence,
            event_seq=self.event_seq,
            final=self.final,
            canonical_stream_response=self.canonical_stream_response,
        )

    def compute_payload_digest(self) -> str:
        return _sha256_rfc8785(self.core_dict())

    def to_dict(self) -> dict[str, Any]:
        data = self.core_dict()
        data["payloadDigest"] = self.payload_digest
        return data

    def canonical_bytes(self) -> bytes:
        return _rfc8785_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamSessionFrameV1:
        actual_fields = frozenset(data)
        if actual_fields != _FRAME_FIELDS:
            missing = sorted(_FRAME_FIELDS - actual_fields)
            extra = sorted(actual_fields - _FRAME_FIELDS)
            raise BindingValidationError(
                f"stream frame fields mismatch; missing={missing}, extra={extra}"
            )
        if data["schemaVersion"] != STREAM_SCHEMA_VERSION:
            raise BindingValidationError("unsupported stream frame schemaVersion")
        try:
            response = protocol.from_protojson(
                data["canonicalStreamResponse"], protocol.StreamResponse
            )
        except (ParseError, TypeError, ValueError) as exc:
            raise BindingValidationError("invalid official StreamResponse payload") from exc
        return cls(
            stream_session_id=data["streamSessionId"],
            stream_open_id=data["streamOpenId"],
            sequence=data["sequence"],
            event_seq=data["eventSeq"],
            final=data["final"],
            canonical_stream_response=response,
            payload_digest=data["payloadDigest"],
        )


class StreamFrameDisposition(StrEnum):
    NEW = "new"
    REDELIVERY = "redelivery"


@dataclass(frozen=True, slots=True)
class StreamFrameCursorV1:
    """Immutable caller-side/session-side sequence gate for committed live frames."""

    stream_session_id: str
    stream_open_id: str
    task_id: str
    snapshot_event_seq: int
    last_sequence: int = 0
    last_event_seq: int | None = None
    last_payload_digest: str | None = None
    final: bool = False

    def __post_init__(self) -> None:
        for field_name, value in (
            ("streamSessionId", self.stream_session_id),
            ("streamOpenId", self.stream_open_id),
            ("taskId", self.task_id),
        ):
            if not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value):
                raise BindingValidationError(f"{field_name} is not a safe token")
        for field_name, value in (
            ("snapshotEventSeq", self.snapshot_event_seq),
            ("lastSequence", self.last_sequence),
        ):
            if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
                raise BindingValidationError(
                    f"{field_name} is outside JSON safe integer range"
                )
        if self.last_event_seq is None:
            object.__setattr__(self, "last_event_seq", self.snapshot_event_seq)
        elif (
            type(self.last_event_seq) is not int
            or not 0 <= self.last_event_seq <= 9_007_199_254_740_991
        ):
            raise BindingValidationError("lastEventSeq is outside JSON safe integer range")
        if type(self.final) is not bool:
            raise BindingValidationError("cursor final must be boolean")
        if self.last_sequence == 0:
            if self.last_event_seq != self.snapshot_event_seq:
                raise BindingValidationError(
                    "empty cursor lastEventSeq must equal snapshotEventSeq"
                )
            if self.last_payload_digest is not None or self.final:
                raise BindingValidationError(
                    "empty cursor cannot have a payload digest or final state"
                )
        else:
            if self.last_event_seq is None or self.last_event_seq <= self.snapshot_event_seq:
                raise BindingValidationError(
                    "advanced cursor lastEventSeq must exceed snapshotEventSeq"
                )
            if not isinstance(self.last_payload_digest, str) or not _DIGEST.fullmatch(
                self.last_payload_digest
            ):
                raise BindingValidationError(
                    "advanced cursor requires a lowercase SHA-256 payload digest"
                )

    def accept(
        self, frame: StreamSessionFrameV1
    ) -> tuple[StreamFrameCursorV1, StreamFrameDisposition]:
        if not isinstance(frame, StreamSessionFrameV1):
            raise BindingValidationError("live frame must be StreamSessionFrameV1")
        if frame.stream_session_id != self.stream_session_id:
            raise BindingValidationError("live frame streamSessionId mismatch")
        if frame.stream_open_id != self.stream_open_id:
            raise BindingValidationError("live frame streamOpenId mismatch")
        event_task_id, terminal = _live_event_identity(frame)
        if event_task_id != self.task_id:
            raise BindingValidationError("live frame Task ID mismatch")
        if frame.final != terminal:
            raise BindingValidationError(
                "live frame final does not match committed Task terminal state"
            )

        if frame.sequence == self.last_sequence and self.last_sequence > 0:
            if (
                frame.event_seq == self.last_event_seq
                and frame.payload_digest == self.last_payload_digest
                and frame.final == self.final
            ):
                return self, StreamFrameDisposition.REDELIVERY
            raise BindingValidationError("conflicting live frame redelivery")
        if self.final:
            raise BindingValidationError("no new sequence is allowed after final frame")
        if frame.sequence != self.last_sequence + 1:
            raise BindingValidationError("live frame sequence must increase by exactly one")
        if frame.event_seq <= self.snapshot_event_seq:
            raise BindingValidationError("live frame eventSeq must exceed snapshotEventSeq")
        if self.last_event_seq is not None and frame.event_seq <= self.last_event_seq:
            raise BindingValidationError("live frame eventSeq must strictly increase")

        return (
            StreamFrameCursorV1(
                stream_session_id=self.stream_session_id,
                stream_open_id=self.stream_open_id,
                task_id=self.task_id,
                snapshot_event_seq=self.snapshot_event_seq,
                last_sequence=frame.sequence,
                last_event_seq=frame.event_seq,
                last_payload_digest=frame.payload_digest,
                final=frame.final,
            ),
            StreamFrameDisposition.NEW,
        )


@dataclass(frozen=True, slots=True)
class StreamSessionOpenedV1:
    stream_session_id: str
    stream_open_id: str
    task_id: str
    operation: Operation
    caller_delivery_subject: str
    snapshot_event_seq: int
    expires_at: datetime
    initial_frame: StreamSessionFrameV1

    def __post_init__(self) -> None:
        if not isinstance(self.stream_session_id, str) or not _SAFE_TOKEN.fullmatch(
            self.stream_session_id
        ):
            raise BindingValidationError("streamSessionId is not a safe token")
        if not isinstance(self.stream_open_id, str) or not _SAFE_TOKEN.fullmatch(
            self.stream_open_id
        ):
            raise BindingValidationError("streamOpenId is not a safe token")
        if not isinstance(self.task_id, str) or not _SAFE_TOKEN.fullmatch(self.task_id):
            raise BindingValidationError("taskId is not a safe token")
        if not isinstance(self.operation, Operation) or self.operation not in _STREAMING_OPERATIONS:
            raise BindingValidationError("stream session operation must be streaming")
        if (
            not isinstance(self.caller_delivery_subject, str)
            or not _DELIVERY_SUBJECT.fullmatch(self.caller_delivery_subject)
        ):
            raise BindingValidationError("callerDeliverySubject is invalid")
        if self.caller_delivery_subject.rsplit(".", 1)[-1] != self.stream_open_id:
            raise BindingValidationError("callerDeliverySubject is not bound to streamOpenId")
        if (
            type(self.snapshot_event_seq) is not int
            or not 0 <= self.snapshot_event_seq <= 9_007_199_254_740_991
        ):
            raise BindingValidationError("snapshotEventSeq is outside JSON safe integer range")
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise BindingValidationError("expiresAt must be timezone-aware")
        if not isinstance(self.initial_frame, StreamSessionFrameV1):
            raise BindingValidationError("initialFrame must be StreamSessionFrameV1")
        frame = self.initial_frame
        if frame.stream_session_id != self.stream_session_id:
            raise BindingValidationError("initialFrame streamSessionId mismatch")
        if frame.stream_open_id != self.stream_open_id:
            raise BindingValidationError("initialFrame streamOpenId mismatch")
        if frame.sequence != 0:
            raise BindingValidationError("initialFrame sequence must be zero")
        if frame.event_seq != self.snapshot_event_seq:
            raise BindingValidationError("initialFrame eventSeq must equal snapshotEventSeq")
        response = cast(Any, frame.canonical_stream_response)
        if response.WhichOneof("payload") != "task":
            raise BindingValidationError("initialFrame must contain an official Task snapshot")
        if response.task.id != self.task_id:
            raise BindingValidationError("initialFrame Task ID mismatch")
        terminal = response.task.status.state in _TERMINAL_STATES
        if frame.final != terminal:
            raise BindingValidationError("initialFrame final does not match Task terminal state")

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schemaVersion": STREAM_SCHEMA_VERSION,
            "streamSessionId": self.stream_session_id,
            "streamOpenId": self.stream_open_id,
            "taskId": self.task_id,
            "operation": self.operation.value,
            "callerDeliverySubject": self.caller_delivery_subject,
            "snapshotEventSeq": self.snapshot_event_seq,
            "expiresAt": _format_timestamp_ms(self.expires_at),
            "initialFrame": self.initial_frame.to_dict(),
        }
        _validate_opened_schema(data)
        return data

    def canonical_bytes(self) -> bytes:
        return _rfc8785_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamSessionOpenedV1:
        _validate_opened_schema(data)
        expires_at = _parse_timestamp(data["expiresAt"])
        if _format_timestamp_ms(expires_at) != data["expiresAt"]:
            raise BindingValidationError("expiresAt must be canonical UTC milliseconds")
        try:
            operation = Operation(data["operation"])
        except ValueError as exc:
            raise BindingValidationError("unknown stream operation") from exc
        return cls(
            stream_session_id=data["streamSessionId"],
            stream_open_id=data["streamOpenId"],
            task_id=data["taskId"],
            operation=operation,
            caller_delivery_subject=data["callerDeliverySubject"],
            snapshot_event_seq=data["snapshotEventSeq"],
            expires_at=expires_at,
            initial_frame=StreamSessionFrameV1.from_dict(data["initialFrame"]),
        )

    @classmethod
    def from_json_bytes(cls, data: bytes) -> StreamSessionOpenedV1:
        try:
            decoded = json.loads(data, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BindingValidationError("stream open response is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise BindingValidationError("stream open response must be a JSON object")
        return cls.from_dict(decoded)


def _live_event_identity(frame: StreamSessionFrameV1) -> tuple[str, bool]:
    response = cast(Any, frame.canonical_stream_response)
    payload_kind = response.WhichOneof("payload")
    if payload_kind == "status_update":
        event = response.status_update
        return event.task_id, event.status.state in _TERMINAL_STATES
    if payload_kind == "artifact_update":
        return response.artifact_update.task_id, False
    raise BindingValidationError(
        "live frame must contain committed statusUpdate or artifactUpdate"
    )


def _frame_core_dict(
    *,
    stream_session_id: str,
    stream_open_id: str,
    sequence: int,
    event_seq: int,
    final: bool,
    canonical_stream_response: ProtobufMessage,
) -> dict[str, Any]:
    try:
        response = protocol.to_protojson_dict(canonical_stream_response)
    except SerializeToJsonError as exc:
        raise BindingValidationError("StreamResponse is not valid ProtoJSON") from exc
    return {
        "schemaVersion": STREAM_SCHEMA_VERSION,
        "streamSessionId": stream_session_id,
        "streamOpenId": stream_open_id,
        "sequence": sequence,
        "eventSeq": event_seq,
        "final": final,
        "canonicalStreamResponse": response,
    }


def _sha256_rfc8785(data: dict[str, Any]) -> str:
    return hashlib.sha256(_rfc8785_bytes(data)).hexdigest()


def _rfc8785_bytes(data: dict[str, Any]) -> bytes:
    try:
        return rfc8785.dumps(data)
    except (rfc8785.CanonicalizationError, TypeError, UnicodeError) as exc:
        raise BindingValidationError("stream object cannot be RFC 8785 canonicalized") from exc


def _validate_opened_schema(data: dict[str, Any]) -> None:
    errors = sorted(_OPENED_VALIDATOR.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise BindingValidationError(f"stream open schema violation at {path}: {error.message}")


def _format_timestamp_ms(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BindingValidationError("invalid stream expiresAt timestamp") from exc
    if parsed.tzinfo is None:
        raise BindingValidationError("stream expiresAt must include an offset")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BindingValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
