"""Strict stream-session control payloads for the NATS v1 binding."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from typing import Any, Self

import jsonschema
import rfc8785

from a2amesh.core import Operation

from .envelope import BindingValidationError

STREAM_CONTROL_SCHEMA_VERSION = "1.0"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CLOSE_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_STREAMING_OPERATIONS = {
    Operation.SEND_STREAMING_MESSAGE,
    Operation.SUBSCRIBE_TO_TASK,
}
_JSON_SAFE_MAX = 9_007_199_254_740_991


def _plain_wire_string(data: dict[str, Any], field_name: str) -> str:
    try:
        value = data[field_name]
    except (KeyError, TypeError) as exc:
        raise BindingValidationError(f"{field_name} is required") from exc
    if type(value) is not str:
        raise BindingValidationError(f"{field_name} must be a plain string")
    return value


class StreamControlKind(StrEnum):
    OPEN = "open"
    ACK = "ack"
    CLOSE = "close"

    @property
    def subject(self) -> str:
        return f"a2a.v1.stream.{self.value}"


class StreamControlOperation(StrEnum):
    OPEN = "StreamSessionOpen"
    ACK = "StreamSessionAck"
    CLOSE = "StreamSessionClose"

    @property
    def kind(self) -> StreamControlKind:
        return {
            StreamControlOperation.OPEN: StreamControlKind.OPEN,
            StreamControlOperation.ACK: StreamControlKind.ACK,
            StreamControlOperation.CLOSE: StreamControlKind.CLOSE,
        }[self]

    @property
    def subject(self) -> str:
        return self.kind.subject


class StreamSessionState(StrEnum):
    OPENING = "OPENING"
    ACTIVE = "ACTIVE"
    DRAINING_FINAL = "DRAINING_FINAL"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    EXPIRING = "EXPIRING"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class StreamOpenDigestContextV1:
    """Trusted non-wire fields included in the State open-request digest."""

    caller_scope: str
    response_core_principal_hash: str
    consumer_config_digest: str

    def __post_init__(self) -> None:
        if type(self.caller_scope) is not str or not _SAFE_TOKEN.fullmatch(
            self.caller_scope
        ):
            raise BindingValidationError("callerScope is not a safe token")
        for name, value in (
            ("responseCorePrincipalHash", self.response_core_principal_hash),
            ("consumerConfigDigest", self.consumer_config_digest),
        ):
            if type(value) is not str or not _DIGEST.fullmatch(value):
                raise BindingValidationError(f"{name} must be lowercase SHA-256 hex")


@dataclass(frozen=True, slots=True)
class StreamOpenRequestV1:
    stream_open_id: str
    operation: Operation
    task_id: str
    caller_instance_id: str
    request_digest: str
    expires_at: datetime
    config_generation: int

    def __post_init__(self) -> None:
        _validate_safe_token("streamOpenId", self.stream_open_id)
        _validate_safe_token("taskId", self.task_id)
        _validate_safe_token("callerInstanceId", self.caller_instance_id)
        if not isinstance(self.operation, Operation) or self.operation not in _STREAMING_OPERATIONS:
            raise BindingValidationError("stream open operation must be streaming")
        _validate_digest("requestDigest", self.request_digest)
        _validate_timestamp("expiresAt", self.expires_at)
        _validate_positive_integer("configGeneration", self.config_generation)
        _validate("open", self.to_dict())

    @classmethod
    def create(
        cls,
        *,
        stream_open_id: str,
        operation: Operation,
        task_id: str,
        caller_instance_id: str,
        expires_at: datetime,
        config_generation: int,
        digest_context: StreamOpenDigestContextV1,
    ) -> Self:
        _validate_timestamp("expiresAt", expires_at)
        digest = compute_stream_open_request_digest(
            stream_open_id=stream_open_id,
            operation=operation,
            task_id=task_id,
            caller_instance_id=caller_instance_id,
            expires_at=expires_at,
            config_generation=config_generation,
            digest_context=digest_context,
        )
        return cls(
            stream_open_id=stream_open_id,
            operation=operation,
            task_id=task_id,
            caller_instance_id=caller_instance_id,
            request_digest=digest,
            expires_at=expires_at,
            config_generation=config_generation,
        )

    def verify_request_digest(self, digest_context: StreamOpenDigestContextV1) -> None:
        expected = compute_stream_open_request_digest(
            stream_open_id=self.stream_open_id,
            operation=self.operation,
            task_id=self.task_id,
            caller_instance_id=self.caller_instance_id,
            expires_at=self.expires_at,
            config_generation=self.config_generation,
            digest_context=digest_context,
        )
        if not hmac.compare_digest(self.request_digest, expected):
            raise BindingValidationError("stream open requestDigest mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": STREAM_CONTROL_SCHEMA_VERSION,
            "streamOpenId": self.stream_open_id,
            "operation": self.operation.value,
            "taskId": self.task_id,
            "callerInstanceId": self.caller_instance_id,
            "requestDigest": self.request_digest,
            "expiresAt": _format_timestamp_ms(self.expires_at),
            "configGeneration": self.config_generation,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        _plain_wire_string(data, "schemaVersion")
        _plain_wire_string(data, "operation")
        _validate("open", data)
        expires_at = _parse_timestamp(data["expiresAt"])
        if _format_timestamp_ms(expires_at) != data["expiresAt"]:
            raise BindingValidationError("expiresAt must be canonical UTC milliseconds")
        try:
            operation = Operation(data["operation"])
        except ValueError as exc:
            raise BindingValidationError("unknown stream open operation") from exc
        return cls(
            stream_open_id=data["streamOpenId"],
            operation=operation,
            task_id=data["taskId"],
            caller_instance_id=data["callerInstanceId"],
            request_digest=data["requestDigest"],
            expires_at=expires_at,
            config_generation=data["configGeneration"],
        )

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Self:
        return cls.from_dict(_decode_object(data, "stream open request"))


@dataclass(frozen=True, slots=True)
class StreamAckRequestV1:
    stream_session_id: str
    stream_open_id: str
    sequence: int
    event_seq: int
    payload_digest: str

    def __post_init__(self) -> None:
        _validate_safe_token("streamSessionId", self.stream_session_id)
        _validate_safe_token("streamOpenId", self.stream_open_id)
        _validate_positive_integer("sequence", self.sequence)
        _validate_nonnegative_integer("eventSeq", self.event_seq)
        _validate_digest("payloadDigest", self.payload_digest)
        _validate("ack", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": STREAM_CONTROL_SCHEMA_VERSION,
            "streamSessionId": self.stream_session_id,
            "streamOpenId": self.stream_open_id,
            "sequence": self.sequence,
            "eventSeq": self.event_seq,
            "payloadDigest": self.payload_digest,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        _plain_wire_string(data, "schemaVersion")
        _validate("ack", data)
        return cls(
            stream_session_id=data["streamSessionId"],
            stream_open_id=data["streamOpenId"],
            sequence=data["sequence"],
            event_seq=data["eventSeq"],
            payload_digest=data["payloadDigest"],
        )

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Self:
        return cls.from_dict(_decode_object(data, "stream ack request"))


@dataclass(frozen=True, slots=True)
class StreamCloseRequestV1:
    stream_session_id: str
    stream_open_id: str
    reason: str

    def __post_init__(self) -> None:
        _validate_safe_token("streamSessionId", self.stream_session_id)
        _validate_safe_token("streamOpenId", self.stream_open_id)
        if type(self.reason) is not str or not _CLOSE_REASON.fullmatch(self.reason):
            raise BindingValidationError("stream close reason must be a bounded code")
        _validate("close", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": STREAM_CONTROL_SCHEMA_VERSION,
            "streamSessionId": self.stream_session_id,
            "streamOpenId": self.stream_open_id,
            "reason": self.reason,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        _plain_wire_string(data, "schemaVersion")
        _validate("close", data)
        return cls(
            stream_session_id=data["streamSessionId"],
            stream_open_id=data["streamOpenId"],
            reason=data["reason"],
        )

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Self:
        return cls.from_dict(_decode_object(data, "stream close request"))


@dataclass(frozen=True, slots=True)
class StreamControlResultV1:
    accepted: bool
    current_state: StreamSessionState

    def __post_init__(self) -> None:
        if type(self.accepted) is not bool:
            raise BindingValidationError("accepted must be boolean")
        if type(self.current_state) is not StreamSessionState:
            raise BindingValidationError("currentState is invalid")
        _validate("result", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "currentState": self.current_state.value,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        _plain_wire_string(data, "currentState")
        _validate("result", data)
        try:
            state = StreamSessionState(data["currentState"])
        except ValueError as exc:
            raise BindingValidationError("unknown stream session currentState") from exc
        return cls(accepted=data["accepted"], current_state=state)

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Self:
        return cls.from_dict(_decode_object(data, "stream control result"))


def compute_stream_open_request_digest(
    *,
    stream_open_id: str,
    operation: Operation,
    task_id: str,
    caller_instance_id: str,
    expires_at: datetime,
    config_generation: int,
    digest_context: StreamOpenDigestContextV1,
) -> str:
    if type(digest_context) is not StreamOpenDigestContextV1:
        raise BindingValidationError("trusted stream open digest context is required")
    _validate_safe_token("streamOpenId", stream_open_id)
    _validate_safe_token("taskId", task_id)
    _validate_safe_token("callerInstanceId", caller_instance_id)
    if type(operation) is not Operation or operation not in _STREAMING_OPERATIONS:
        raise BindingValidationError("stream open operation must be streaming")
    _validate_timestamp("expiresAt", expires_at)
    _validate_positive_integer("configGeneration", config_generation)
    digest_input = {
        "schemaVersion": STREAM_CONTROL_SCHEMA_VERSION,
        "streamOpenId": stream_open_id,
        "operation": operation.value,
        "taskId": task_id,
        "callerScope": digest_context.caller_scope,
        "callerInstanceId": caller_instance_id,
        "responseCorePrincipalHash": digest_context.response_core_principal_hash,
        "configGeneration": config_generation,
        "expiresAt": _format_timestamp_ms(expires_at),
        "consumerConfigDigest": digest_context.consumer_config_digest,
    }
    return hashlib.sha256(_canonical_bytes(digest_input)).hexdigest()


_SCHEMA_FILES = {
    "open": "nats_stream_open_request_v1.json",
    "ack": "nats_stream_ack_request_v1.json",
    "close": "nats_stream_close_request_v1.json",
    "result": "nats_stream_control_result_v1.json",
}
_VALIDATORS = {
    name: jsonschema.Draft202012Validator(
        json.loads(
            files("a2amesh.schemas").joinpath(file_name).read_text(encoding="utf-8")
        ),
        format_checker=jsonschema.FormatChecker(),
    )
    for name, file_name in _SCHEMA_FILES.items()
}


def _validate(schema_name: str, data: dict[str, Any]) -> None:
    errors = sorted(
        _VALIDATORS[schema_name].iter_errors(data), key=lambda error: list(error.path)
    )
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise BindingValidationError(
            f"stream {schema_name} schema violation at {path}: {error.message}"
        )


def _validate_safe_token(name: str, value: object) -> None:
    if type(value) is not str or not _SAFE_TOKEN.fullmatch(value):
        raise BindingValidationError(f"{name} is not a safe token")


def _validate_digest(name: str, value: object) -> None:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise BindingValidationError(f"{name} must be lowercase SHA-256 hex")


def _validate_nonnegative_integer(name: str, value: object) -> None:
    if type(value) is not int or not 0 <= value <= _JSON_SAFE_MAX:
        raise BindingValidationError(f"{name} is outside JSON safe integer range")


def _validate_positive_integer(name: str, value: object) -> None:
    if type(value) is not int or not 1 <= value <= _JSON_SAFE_MAX:
        raise BindingValidationError(f"{name} must be a positive JSON safe integer")


def _validate_timestamp(name: str, value: object) -> None:
    if type(value) is not datetime or value.tzinfo is None:
        raise BindingValidationError(f"{name} must be timezone-aware")


def _format_timestamp_ms(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    if type(value) is not str:
        raise BindingValidationError("stream control timestamp must be a plain string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise BindingValidationError("invalid stream control timestamp") from exc
    if parsed.tzinfo is None:
        raise BindingValidationError("stream control timestamp must include an offset")
    return parsed


def _canonical_bytes(data: dict[str, Any]) -> bytes:
    try:
        return rfc8785.dumps(data)
    except (rfc8785.CanonicalizationError, TypeError, UnicodeError) as exc:
        raise BindingValidationError("stream control object cannot be canonicalized") from exc


def _decode_object(data: bytes, name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(data, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BindingValidationError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise BindingValidationError(f"{name} must be a JSON object")
    return decoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BindingValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
