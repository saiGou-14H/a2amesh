"""Pure, closed Redis key rendering for the A2AMesh state plane."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum, auto
from types import MappingProxyType
from typing import Final


class KeyBuilderError(ValueError):
    """Raised when a Redis key cannot be rendered from approved parts."""


class KeyKind(StrEnum):
    """Closed Redis key templates registered for the current C2 slice."""

    AUTH_REPLAY = auto()
    DEDUPE = auto()
    TASK = auto()
    TASKS_UPDATED = auto()
    TASKS_STATE = auto()
    CONTEXT_TASKS = auto()
    CALLER_TASKS = auto()
    AGENT_TASKS = auto()
    OUTBOX_EVENT = auto()
    OUTBOX_DUE = auto()
    OUTBOX_TASK = auto()
    ADMISSION_GLOBAL = auto()
    ADMISSION_PRINCIPAL = auto()
    ADMISSION_PRINCIPALS = auto()
    ADMISSION_PRINCIPAL_FIFO = auto()
    ADMISSION_TASK = auto()
    DISPATCH = auto()


class KeyPartCodec(StrEnum):
    """Approved encodings for dynamic Redis key components."""

    SAFE_TOKEN = auto()
    AGENT_ID = auto()
    SHA256_BASE64URL = auto()
    TASK_STATE = auto()
    POSITIVE_SEQUENCE = auto()


_SAFE_TOKEN: Final = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_AGENT_ID: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SHA256_BASE64URL: Final = re.compile(r"^[A-Za-z0-9_-]{43}$")
_TASK_STATE: Final = re.compile(r"^TASK_STATE_[A-Z][A-Z0-9_]{0,51}$")
_POSITIVE_SEQUENCE: Final = re.compile(r"^[1-9][0-9]{0,15}$")
_COMPONENT_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_JSON_SAFE_INTEGER_MAX: Final = 9_007_199_254_740_991


@dataclass(frozen=True, slots=True)
class KeyPart:
    """A validated Redis key component tagged with its exact codec."""

    text: str
    codec: KeyPartCodec

    def __post_init__(self) -> None:
        if type(self.text) is not str or type(self.codec) is not KeyPartCodec:
            raise KeyBuilderError("key part must use an approved codec")
        _validate_part_text(self.text, self.codec)

    @classmethod
    def safe_token(cls, value: object) -> KeyPart:
        """Build a strict ASCII token component without implicit encoding."""

        return cls(_exact_string(value, "SAFE_TOKEN"), KeyPartCodec.SAFE_TOKEN)

    @classmethod
    def agent_id(cls, value: object) -> KeyPart:
        """Build an Agent ID using the authoritative production grammar."""

        return cls(_exact_string(value, "AGENT_ID"), KeyPartCodec.AGENT_ID)

    @classmethod
    def sha256_base64url(cls, value: object) -> KeyPart:
        """Build a canonical unpadded base64url-encoded SHA-256 component."""

        return cls(
            _exact_string(value, "SHA256_BASE64URL"),
            KeyPartCodec.SHA256_BASE64URL,
        )

    @classmethod
    def task_state(cls, value: object) -> KeyPart:
        """Build an official-name TaskState index component."""

        return cls(_exact_string(value, "TASK_STATE"), KeyPartCodec.TASK_STATE)

    @classmethod
    def positive_sequence(cls, value: object) -> KeyPart:
        """Build a positive JSON-safe integer encoded as canonical decimal ASCII."""

        if type(value) is not int or not 1 <= value <= _JSON_SAFE_INTEGER_MAX:
            raise KeyBuilderError("POSITIVE_SEQUENCE must be a positive JSON-safe integer")
        return cls(str(value), KeyPartCodec.POSITIVE_SEQUENCE)


@dataclass(frozen=True, slots=True)
class _Component:
    name: str
    codec: KeyPartCodec


_Segment = str | _Component


def _component(name: str, codec: KeyPartCodec) -> _Component:
    return _Component(name, codec)


_SAFE = KeyPartCodec.SAFE_TOKEN
_AGENT = KeyPartCodec.AGENT_ID
_HASH = KeyPartCodec.SHA256_BASE64URL
_STATE = KeyPartCodec.TASK_STATE
_SEQUENCE = KeyPartCodec.POSITIVE_SEQUENCE

_TEMPLATES = MappingProxyType(
    {
        KeyKind.AUTH_REPLAY: (
            "auth",
            "replay",
            _component("signer_hash", _HASH),
            _component("request_id_hash", _HASH),
        ),
        KeyKind.DEDUPE: (
            "dedupe",
            _component("caller_hash", _HASH),
            _component("target_agent_id", _AGENT),
            _component("message_id", _SAFE),
        ),
        KeyKind.TASK: ("task", _component("task_id", _SAFE)),
        KeyKind.TASKS_UPDATED: ("tasks", "updated"),
        KeyKind.TASKS_STATE: ("tasks", "state", _component("state", _STATE)),
        KeyKind.CONTEXT_TASKS: (
            "context",
            _component("context_id", _SAFE),
            "tasks",
        ),
        KeyKind.CALLER_TASKS: (
            "caller",
            _component("principal_hash", _HASH),
            "tasks",
        ),
        KeyKind.AGENT_TASKS: (
            "agent",
            _component("agent_id", _AGENT),
            "tasks",
        ),
        KeyKind.OUTBOX_EVENT: (
            "outbox",
            "event",
            _component("task_id", _SAFE),
            _component("event_seq", _SEQUENCE),
        ),
        KeyKind.OUTBOX_DUE: ("outbox", "due"),
        KeyKind.OUTBOX_TASK: (
            "outbox",
            "task",
            _component("task_id", _SAFE),
        ),
        KeyKind.ADMISSION_GLOBAL: ("admission", "global"),
        KeyKind.ADMISSION_PRINCIPAL: (
            "admission",
            "principal",
            _component("principal_hash", _HASH),
        ),
        KeyKind.ADMISSION_PRINCIPALS: ("admission", "principals"),
        KeyKind.ADMISSION_PRINCIPAL_FIFO: (
            "admission",
            "principal",
            _component("principal_hash", _HASH),
            "fifo",
        ),
        KeyKind.ADMISSION_TASK: (
            "admission",
            "task",
            _component("task_id", _SAFE),
        ),
        KeyKind.DISPATCH: ("dispatch", _component("task_id", _SAFE)),
    }
)


@dataclass(frozen=True, slots=True)
class RedisKeyBuilder:
    """Render schema-v1 Redis keys under one trusted mesh hash tag."""

    mesh_id: str
    schema_version: str = "v1"
    normalized_mesh_id: str = field(init=False)
    _prefix: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or self.schema_version != "v1":
            raise KeyBuilderError("schema_version must be exactly 'v1'")
        normalized = _normalize_mesh_id(self.mesh_id)
        object.__setattr__(self, "normalized_mesh_id", normalized)
        object.__setattr__(
            self,
            "_prefix",
            f"a2am:v1:{{{normalized}}}:".encode(),
        )

    def render(self, kind: KeyKind, /, **parts: KeyPart) -> bytes:
        """Render one closed template from its exact approved component set."""

        if type(kind) is not KeyKind:
            raise KeyBuilderError("key kind is not registered")
        template = _TEMPLATES.get(kind)
        if template is None:
            raise KeyBuilderError("key kind is not registered")
        expected = {
            segment.name for segment in template if isinstance(segment, _Component)
        }
        if set(parts) != expected:
            missing = expected - set(parts)
            extra = set(parts) - expected
            raise KeyBuilderError(
                f"{kind.name} components do not match: "
                f"missing={_component_names(missing)}; extra={_component_names(extra)}"
            )

        rendered: list[bytes] = []
        for segment in template:
            if isinstance(segment, str):
                rendered.append(segment.encode("ascii"))
                continue
            part = parts[segment.name]
            if type(part) is not KeyPart or part.codec is not segment.codec:
                raise KeyBuilderError(
                    f"{kind.name} component {segment.name} requires {segment.codec.name}"
                )
            rendered.append(part.text.encode("ascii"))
        return self._prefix + b":".join(rendered)


def _exact_string(value: object, codec_name: str) -> str:
    if type(value) is not str:
        raise KeyBuilderError(f"{codec_name} value must be an exact string")
    return value


def _component_names(names: set[str]) -> str:
    if not names:
        return "<none>"
    return ",".join(
        name if _COMPONENT_NAME.fullmatch(name) is not None else "<invalid>"
        for name in sorted(names)
    )


def _validate_part_text(text: str, codec: KeyPartCodec) -> None:
    if codec is KeyPartCodec.SAFE_TOKEN:
        valid = _SAFE_TOKEN.fullmatch(text) is not None
    elif codec is KeyPartCodec.AGENT_ID:
        valid = _AGENT_ID.fullmatch(text) is not None
    elif codec is KeyPartCodec.SHA256_BASE64URL:
        valid = _is_canonical_sha256_base64url(text)
    elif codec is KeyPartCodec.TASK_STATE:
        valid = _TASK_STATE.fullmatch(text) is not None
    elif codec is KeyPartCodec.POSITIVE_SEQUENCE:
        valid = _is_canonical_positive_sequence(text)
    else:  # pragma: no cover - exact enum gate above makes this unreachable
        valid = False
    if not valid:
        raise KeyBuilderError(f"key part is not canonical {codec.name}")


def _is_canonical_sha256_base64url(text: str) -> bool:
    if _SHA256_BASE64URL.fullmatch(text) is None:
        return False
    try:
        decoded = base64.urlsafe_b64decode(text + "=")
    except (ValueError, binascii.Error):
        return False
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    return len(decoded) == 32 and canonical == text


def _is_canonical_positive_sequence(text: str) -> bool:
    if _POSITIVE_SEQUENCE.fullmatch(text) is None:
        return False
    return int(text) <= _JSON_SAFE_INTEGER_MAX


def _normalize_mesh_id(value: object) -> str:
    if type(value) is not str:
        raise KeyBuilderError("mesh_id must be an exact string")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or len(normalized) > 128:
        raise KeyBuilderError("mesh_id must contain 1 to 128 normalized characters")
    if any(
        unicodedata.category(char) in {"Cc", "Cf"}
        or char.isspace()
        or char in "{}"
        for char in normalized
    ):
        raise KeyBuilderError("mesh_id contains a forbidden character")
    try:
        normalized.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise KeyBuilderError("mesh_id must be valid UTF-8 text") from None
    return normalized
