"""Signed NATS v1 envelope for internal stream-session control commands."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any, TypeAlias, cast

import jsonschema
import nkeys

from a2amesh.identity import SignerPolicy, nkey_public_key, sign_nkey

from .auth import (
    AUTH_ALGORITHM,
    BindingAuthVerifier,
    RequestReplayGuard,
    VerifiedBindingIdentity,
    canonical_signing_bytes,
)
from .envelope import (
    A2A_PROTOCOL_VERSION,
    BINDING_SCHEMA_VERSION,
    BINDING_URI,
    AuthContext,
    AuthProof,
    BindingValidationError,
)
from .stream_control import (
    StreamAckRequestV1,
    StreamCloseRequestV1,
    StreamControlOperation,
    StreamOpenDigestContextV1,
    StreamOpenRequestV1,
)

_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_JSON_SAFE_MAX = 9_007_199_254_740_991
_SCHEMA = json.loads(
    files("a2amesh.schemas")
    .joinpath("nats_stream_control_envelope_v1.json")
    .read_text(encoding="utf-8")
)
_VALIDATOR = jsonschema.Draft202012Validator(
    _SCHEMA,
    format_checker=jsonschema.FormatChecker(),
)

StreamControlPayload: TypeAlias = (
    StreamOpenRequestV1 | StreamAckRequestV1 | StreamCloseRequestV1
)
_PAYLOAD_TYPES: dict[StreamControlOperation, type[StreamControlPayload]] = {
    StreamControlOperation.OPEN: StreamOpenRequestV1,
    StreamControlOperation.ACK: StreamAckRequestV1,
    StreamControlOperation.CLOSE: StreamCloseRequestV1,
}


@dataclass(frozen=True, slots=True)
class StreamControlEnvelopeV1:
    operation: StreamControlOperation
    request_id: str
    caller_instance_id: str
    stream_open_id: str
    config_generation: int
    caller_agent_id: str
    auth_context: AuthContext
    auth_proof: AuthProof
    target_agent_id: str
    sent_at: datetime
    deadline_at: datetime
    reply_subject: str
    payload: StreamControlPayload

    def __post_init__(self) -> None:
        if type(self.operation) is not StreamControlOperation:
            raise BindingValidationError("invalid stream control operation")
        for name, value in (
            ("requestId", self.request_id),
            ("callerInstanceId", self.caller_instance_id),
            ("streamOpenId", self.stream_open_id),
        ):
            if type(value) is not str or not _SAFE_TOKEN.fullmatch(value):
                raise BindingValidationError(f"{name} is not a safe token")
        for name, value in (
            ("callerAgentId", self.caller_agent_id),
            ("targetAgentId", self.target_agent_id),
        ):
            if type(value) is not str or not _AGENT_ID.fullmatch(value):
                raise BindingValidationError(f"{name} is invalid")
        if (
            type(self.config_generation) is not int
            or not 1 <= self.config_generation <= _JSON_SAFE_MAX
        ):
            raise BindingValidationError("configGeneration must be a positive safe integer")
        expected_payload_type = _PAYLOAD_TYPES[self.operation]
        if type(self.payload) is not expected_payload_type:
            raise BindingValidationError(
                f"payload type for {self.operation.value} must be "
                f"{expected_payload_type.__name__}"
            )
        if self.payload.stream_open_id != self.stream_open_id:
            raise BindingValidationError("outer and payload streamOpenId mismatch")
        if self.operation is StreamControlOperation.OPEN:
            open_payload = cast(StreamOpenRequestV1, self.payload)
            if open_payload.caller_instance_id != self.caller_instance_id:
                raise BindingValidationError("outer and payload callerInstanceId mismatch")
            if open_payload.config_generation != self.config_generation:
                raise BindingValidationError("outer and payload configGeneration mismatch")
        if type(self.auth_context) is not AuthContext:
            raise BindingValidationError("authContext must be AuthContext")
        if type(self.auth_proof) is not AuthProof:
            raise BindingValidationError("authProof must be AuthProof")
        if type(self.reply_subject) is not str:
            raise BindingValidationError("replySubject must be a plain string")
        if type(self.sent_at) is not datetime or self.sent_at.tzinfo is None:
            raise BindingValidationError("sentAt must be timezone-aware")
        if type(self.deadline_at) is not datetime or self.deadline_at.tzinfo is None:
            raise BindingValidationError("deadlineAt must be timezone-aware")
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
        _validate_schema(self.to_dict())

    @property
    def expected_subject(self) -> str:
        return self.operation.subject

    def to_dict(self) -> dict[str, Any]:
        return {
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
            "payload": self.payload.to_dict(),
        }

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def signing_payload_dict(self) -> dict[str, object]:
        data = self.to_dict()
        del data["authProof"]["signature"]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StreamControlEnvelopeV1:
        _validate_schema(data)
        try:
            operation = StreamControlOperation(data["operation"])
        except ValueError as exc:
            raise BindingValidationError("unknown stream control operation") from exc
        payload_type = _PAYLOAD_TYPES[operation]
        payload = payload_type.from_dict(data["payload"])
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
    def from_json_bytes(cls, data: bytes) -> StreamControlEnvelopeV1:
        try:
            decoded = json.loads(data, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BindingValidationError(
                "stream control envelope is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise BindingValidationError("stream control envelope must be a JSON object")
        return cls.from_dict(decoded)


def sign_stream_control_envelope(
    envelope: StreamControlEnvelopeV1,
    key_pair: nkeys.KeyPair,
) -> StreamControlEnvelopeV1:
    signer = nkey_public_key(key_pair)
    unsigned = replace(
        envelope,
        auth_proof=AuthProof(
            signer=signer,
            algorithm=AUTH_ALGORITHM,
            signature="unsigned",
        ),
    )
    return replace(
        unsigned,
        auth_proof=AuthProof(
            signer=signer,
            algorithm=AUTH_ALGORITHM,
            signature=sign_nkey(canonical_signing_bytes(unsigned), key_pair),
        ),
    )


_STREAM_CONTROL_VERIFIER_SEALED_FIELDS = frozenset({"_common", "_sealed", "__class__"})


class StreamControlAuthVerifier:
    """Authenticate a stream control envelope and bind it to its literal subject."""

    __slots__ = ("_common", "_sealed")

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False) and name in _STREAM_CONTROL_VERIFIER_SEALED_FIELDS:
            raise AttributeError("StreamControlAuthVerifier configuration is immutable")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_sealed", False) and name in _STREAM_CONTROL_VERIFIER_SEALED_FIELDS:
            raise AttributeError("StreamControlAuthVerifier configuration is immutable")
        object.__delattr__(self, name)

    def __init__(
        self,
        signer_policies: Mapping[str, SignerPolicy],
        replay_guard: RequestReplayGuard,
        *,
        clock_skew_seconds: int = 30,
        max_auth_lifetime_seconds: int = 900,
    ) -> None:
        self._common = BindingAuthVerifier(
            signer_policies,
            replay_guard,
            clock_skew_seconds=clock_skew_seconds,
            max_auth_lifetime_seconds=max_auth_lifetime_seconds,
        )
        self._sealed = True

    async def verify(
        self,
        envelope: StreamControlEnvelopeV1,
        *,
        expected_operation: StreamControlOperation,
        received_subject: str,
        connection_public_key: str,
        expected_target_agent_id: str,
        expected_caller_agent_id: str,
        expected_caller_instance_id: str,
        allowed_reply_prefix: str,
        expected_config_generation: int,
        open_digest_context: StreamOpenDigestContextV1 | None = None,
        now: datetime | None = None,
    ) -> VerifiedBindingIdentity:
        if not isinstance(envelope, StreamControlEnvelopeV1):
            raise BindingValidationError("stream control envelope type is invalid")
        if envelope.operation is not expected_operation:
            raise BindingValidationError("stream control operation does not match handler")
        if envelope.operation is StreamControlOperation.OPEN:
            if open_digest_context is None:
                raise BindingValidationError(
                    "trusted digest context is required for stream open"
                )
            cast(StreamOpenRequestV1, envelope.payload).verify_request_digest(
                open_digest_context
            )
        elif open_digest_context is not None:
            raise BindingValidationError(
                "stream open digest context is invalid for ack/close"
            )
        return await self._common.verify_subject(
            envelope,
            received_subject=received_subject,
            expected_subject=expected_operation.subject,
            connection_public_key=connection_public_key,
            expected_target_agent_id=expected_target_agent_id,
            expected_caller_agent_id=expected_caller_agent_id,
            expected_caller_instance_id=expected_caller_instance_id,
            allowed_reply_prefix=allowed_reply_prefix,
            expected_config_generation=expected_config_generation,
            now=now,
        )


def _validate_schema(data: dict[str, Any]) -> None:
    errors = sorted(_VALIDATOR.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        raise BindingValidationError(
            f"stream control envelope schema violation at {path}: {error.message}"
        )


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise BindingValidationError("invalid stream control envelope timestamp") from exc
    if parsed.tzinfo is None:
        raise BindingValidationError("stream control envelope timestamp must include an offset")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BindingValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
