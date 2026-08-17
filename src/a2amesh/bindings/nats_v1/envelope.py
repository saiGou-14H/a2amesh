"""Strict request envelope for the custom A2AMesh NATS v1 binding."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import jsonschema
from google.protobuf.json_format import ParseError
from google.protobuf.message import Message as ProtobufMessage

from a2amesh import protocol
from a2amesh.core import OPERATION_SPECS, Operation

BINDING_URI = "https://a2amesh.dev/bindings/nats/v1"
BINDING_SCHEMA_VERSION = "1.1"
A2A_PROTOCOL_VERSION = "1.0"

_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STREAMING_OPERATIONS = {
    Operation.SEND_STREAMING_MESSAGE,
    Operation.SUBSCRIBE_TO_TASK,
}
_REQUEST_SCHEMA = json.loads(
    files("a2amesh.schemas")
    .joinpath("nats_binding_request_v1.json")
    .read_text(encoding="utf-8")
)
_REQUEST_VALIDATOR = jsonschema.Draft202012Validator(
    _REQUEST_SCHEMA,
    format_checker=jsonschema.FormatChecker(),
)


class BindingValidationError(ValueError):
    """The NATS binding envelope or its official A2A payload is invalid."""


@dataclass(frozen=True, slots=True)
class AuthContext:
    principal_id: str
    credential_id: str
    method: str
    issuer: str
    subject: str
    issued_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "principalId": self.principal_id,
            "credentialId": self.credential_id,
            "method": self.method,
            "issuer": self.issuer,
            "subject": self.subject,
            "issuedAt": _format_timestamp(self.issued_at),
            "expiresAt": _format_timestamp(self.expires_at),
        }


@dataclass(frozen=True, slots=True)
class AuthProof:
    signer: str
    algorithm: str
    signature: str

    def to_dict(self) -> dict[str, str]:
        return {
            "signer": self.signer,
            "algorithm": self.algorithm,
            "signature": self.signature,
        }


@dataclass(frozen=True, slots=True)
class BindingRequestEnvelope:
    operation: Operation
    request_id: str
    caller_instance_id: str
    stream_open_id: str | None
    config_generation: int
    caller_agent_id: str
    auth_context: AuthContext
    auth_proof: AuthProof
    target_agent_id: str
    sent_at: datetime
    deadline_at: datetime
    reply_subject: str
    payload: ProtobufMessage

    def __post_init__(self) -> None:
        if not _SAFE_TOKEN.fullmatch(self.request_id):
            raise BindingValidationError("requestId is not a safe token")
        if not _SAFE_TOKEN.fullmatch(self.caller_instance_id):
            raise BindingValidationError("callerInstanceId is not a safe token")
        if not _AGENT_ID.fullmatch(self.caller_agent_id):
            raise BindingValidationError("callerAgentId is invalid")
        if not _AGENT_ID.fullmatch(self.target_agent_id):
            raise BindingValidationError("targetAgentId is invalid")
        if type(self.config_generation) is not int or self.config_generation < 1:
            raise BindingValidationError("configGeneration must be a positive integer")
        if self.operation in _STREAMING_OPERATIONS:
            if self.stream_open_id is None or not _SAFE_TOKEN.fullmatch(self.stream_open_id):
                raise BindingValidationError("streamOpenId is required for streaming operations")
        elif self.stream_open_id is not None:
            raise BindingValidationError("streamOpenId must be null for unary operations")
        if self.sent_at.tzinfo is None or self.deadline_at.tzinfo is None:
            raise BindingValidationError("sentAt and deadlineAt must be timezone-aware")
        if self.deadline_at <= self.sent_at:
            raise BindingValidationError("deadlineAt must be later than sentAt")
        if (
            self.auth_context.issued_at.tzinfo is None
            or self.auth_context.expires_at.tzinfo is None
        ):
            raise BindingValidationError("AuthContext timestamps must be timezone-aware")
        if self.auth_context.expires_at <= self.auth_context.issued_at:
            raise BindingValidationError("AuthContext expiresAt must be later than issuedAt")
        if not (
            self.auth_context.issued_at <= self.sent_at < self.auth_context.expires_at
        ):
            raise BindingValidationError("AuthContext is not valid at sentAt")
        expected_type = OPERATION_SPECS[self.operation].request_type
        if not isinstance(self.payload, expected_type):
            raise BindingValidationError(
                f"payload type for {self.operation.value} must be {expected_type.__name__}"
            )
        tenant_field = self.payload.DESCRIPTOR.fields_by_name.get("tenant")
        if tenant_field is not None and getattr(self.payload, tenant_field.name):
            raise BindingValidationError("tenant must be empty in A2AMesh V1")

    def to_dict(self) -> dict[str, Any]:
        envelope = {
            "bindingUri": BINDING_URI,
            "bindingSchemaVersion": BINDING_SCHEMA_VERSION,
            "a2aProtocolVersion": A2A_PROTOCOL_VERSION,
            "operation": self.operation.value,
            "requestId": self.request_id,
            "callerInstanceId": self.caller_instance_id,
            "streamOpenId": self.stream_open_id,
            "configGeneration": self.config_generation,
            "callerAgentId": self.caller_agent_id,
            "authContext": self.auth_context.to_dict(),
            "authProof": self.auth_proof.to_dict(),
            "targetAgentId": self.target_agent_id,
            "sentAt": _format_timestamp(self.sent_at),
            "deadlineAt": _format_timestamp(self.deadline_at),
            "replySubject": self.reply_subject,
            "payload": protocol.to_protojson_dict(self.payload),
        }
        _validate_schema(envelope)
        return envelope

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def signing_payload_dict(self) -> dict[str, Any]:
        """Return the envelope shape covered by RFC 8785 before signing."""
        envelope = self.to_dict()
        del envelope["authProof"]["signature"]
        return envelope

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BindingRequestEnvelope:
        _validate_schema(data)
        try:
            operation = Operation(data["operation"])
            payload = protocol.from_protojson(
                data["payload"], OPERATION_SPECS[operation].request_type
            )
        except (KeyError, ValueError, ParseError) as exc:
            raise BindingValidationError(f"invalid official A2A payload: {exc}") from exc
        auth_context = data["authContext"]
        auth_proof = data["authProof"]
        return cls(
            operation=operation,
            request_id=data["requestId"],
            caller_instance_id=data["callerInstanceId"],
            stream_open_id=data["streamOpenId"],
            config_generation=data["configGeneration"],
            caller_agent_id=data["callerAgentId"],
            auth_context=AuthContext(
                principal_id=auth_context["principalId"],
                credential_id=auth_context["credentialId"],
                method=auth_context["method"],
                issuer=auth_context["issuer"],
                subject=auth_context["subject"],
                issued_at=_parse_timestamp(auth_context["issuedAt"]),
                expires_at=_parse_timestamp(auth_context["expiresAt"]),
            ),
            auth_proof=AuthProof(
                signer=auth_proof["signer"],
                algorithm=auth_proof["algorithm"],
                signature=auth_proof["signature"],
            ),
            target_agent_id=data["targetAgentId"],
            sent_at=_parse_timestamp(data["sentAt"]),
            deadline_at=_parse_timestamp(data["deadlineAt"]),
            reply_subject=data["replySubject"],
            payload=payload,
        )

    @classmethod
    def from_json_bytes(cls, data: bytes) -> BindingRequestEnvelope:
        try:
            decoded = json.loads(data, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BindingValidationError("request is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise BindingValidationError("request envelope must be a JSON object")
        return cls.from_dict(decoded)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BindingValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_schema(data: dict[str, Any]) -> None:
    errors = sorted(_REQUEST_VALIDATOR.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise BindingValidationError(f"request schema violation at {path}: {error.message}")


def _format_timestamp(value: datetime) -> str:
    value = value.astimezone(UTC)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BindingValidationError(f"invalid RFC 3339 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise BindingValidationError("timestamp must include an offset")
    return parsed
