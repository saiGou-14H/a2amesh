"""Strict one-shot response envelope for the custom NATS v1 binding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import jsonschema
from google.protobuf.json_format import ParseError
from google.protobuf.message import Message as ProtobufMessage

from a2amesh import protocol
from a2amesh.core import OPERATION_SPECS, Operation

from .envelope import (
    A2A_PROTOCOL_VERSION,
    BINDING_SCHEMA_VERSION,
    BINDING_URI,
    BindingValidationError,
)

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STREAMING_OPERATIONS = {
    Operation.SEND_STREAMING_MESSAGE,
    Operation.SUBSCRIBE_TO_TASK,
}
_RESPONSE_SCHEMA = json.loads(
    files("a2amesh.schemas")
    .joinpath("nats_binding_response_v1.json")
    .read_text(encoding="utf-8")
)
_RESPONSE_VALIDATOR = jsonschema.Draft202012Validator(_RESPONSE_SCHEMA)


@dataclass(frozen=True, slots=True)
class BindingError:
    type: str
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        if type(self.type) is not str:
            raise BindingValidationError("error.type must be a plain string")
        if type(self.message) is not str:
            raise BindingValidationError("error.message must be a plain string")
        if type(self.retryable) is not bool:
            raise BindingValidationError("error.retryable must be boolean")
        if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_]{0,127}$", self.type):
            raise BindingValidationError("error.type is invalid")
        if not self.message or len(self.message) > 4096:
            raise BindingValidationError("error.message length is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class BindingResponseEnvelope:
    """One NATS response; streaming success uses a dedicated open response."""

    operation: Operation
    request_id: str
    config_generation: int
    payload: ProtobufMessage | None = None
    error: BindingError | None = None

    def __post_init__(self) -> None:
        if type(self.operation) is not Operation:
            raise BindingValidationError("operation must be an official Operation")
        if type(self.request_id) is not str:
            raise BindingValidationError("requestId must be a plain string")
        if self.error is not None and type(self.error) is not BindingError:
            raise BindingValidationError("error must be the NATS binding BindingError")
        if not _SAFE_TOKEN.fullmatch(self.request_id):
            raise BindingValidationError("requestId is not a safe token")
        if type(self.config_generation) is not int or not (
            1 <= self.config_generation <= 9_007_199_254_740_991
        ):
            raise BindingValidationError("configGeneration is outside JSON safe integer range")
        if (self.payload is None) == (self.error is None):
            raise BindingValidationError("response must contain exactly one of payload or error")
        if self.payload is None:
            return
        if self.operation in _STREAMING_OPERATIONS:
            raise BindingValidationError(
                "streaming success must use StreamSessionOpenedV1, not a one-shot payload"
            )
        expected_type = OPERATION_SPECS[self.operation].response_type
        if type(self.payload) is not expected_type:
            raise BindingValidationError(
                f"response payload for {self.operation.value} must be {expected_type.__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "bindingUri": BINDING_URI,
            "bindingSchemaVersion": BINDING_SCHEMA_VERSION,
            "a2aProtocolVersion": A2A_PROTOCOL_VERSION,
            "requestId": self.request_id,
            "configGeneration": self.config_generation,
            "sequence": 1,
            "final": True,
        }
        if self.payload is not None:
            data["payload"] = protocol.to_protojson_dict(self.payload)
        else:
            data["error"] = self.error.to_dict() if self.error is not None else None
        _validate_response_schema(data)
        return data

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], operation: Operation
    ) -> BindingResponseEnvelope:
        _validate_response_schema(data)
        if "error" in data:
            raw_error = data["error"]
            return cls(
                operation=operation,
                request_id=data["requestId"],
                config_generation=data["configGeneration"],
                error=BindingError(
                    type=raw_error["type"],
                    message=raw_error["message"],
                    retryable=raw_error["retryable"],
                ),
            )
        if operation in _STREAMING_OPERATIONS:
            raise BindingValidationError(
                "streaming success must be parsed as StreamSessionOpenedV1"
            )
        try:
            payload = protocol.from_protojson(
                data["payload"], OPERATION_SPECS[operation].response_type
            )
        except (KeyError, ValueError, ParseError) as exc:
            raise BindingValidationError(f"invalid official A2A response payload: {exc}") from exc
        return cls(
            operation=operation,
            request_id=data["requestId"],
            config_generation=data["configGeneration"],
            payload=payload,
        )

    @classmethod
    def from_json_bytes(
        cls, data: bytes, operation: Operation
    ) -> BindingResponseEnvelope:
        try:
            decoded = json.loads(data, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BindingValidationError("response is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise BindingValidationError("response envelope must be a JSON object")
        return cls.from_dict(decoded, operation)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BindingValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_response_schema(data: dict[str, Any]) -> None:
    errors = sorted(_RESPONSE_VALIDATOR.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise BindingValidationError(f"response schema violation at {path}: {error.message}")
