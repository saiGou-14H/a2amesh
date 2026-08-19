from __future__ import annotations

import subprocess
import sys

import pytest

from a2amesh.state.key_builder import (
    KeyBuilderError,
    KeyKind,
    KeyPart,
    KeyPartCodec,
    RedisKeyBuilder,
)

_HASH = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_task_key_uses_nfc_mesh_tag_and_returns_exact_bytes() -> None:
    composed = RedisKeyBuilder("mésh")
    decomposed = RedisKeyBuilder("me\u0301sh")
    task_id = KeyPart.safe_token("task-123")

    expected = b"a2am:v1:{m\xc3\xa9sh}:task:task-123"
    assert composed.normalized_mesh_id == "mésh"
    assert decomposed.normalized_mesh_id == "mésh"
    assert composed.render(KeyKind.TASK, task_id=task_id) == expected
    assert decomposed.render(KeyKind.TASK, task_id=task_id) == expected
    assert type(expected) is bytes


def test_agent_id_part_uses_authoritative_agent_syntax() -> None:
    part = KeyPart.agent_id("windows-a")

    assert part.text == "windows-a"
    assert part.codec is KeyPartCodec.AGENT_ID


def test_sha256_base64url_part_accepts_canonical_32_byte_digest() -> None:
    part = KeyPart.sha256_base64url(_HASH)

    assert part.text == _HASH
    assert part.codec is KeyPartCodec.SHA256_BASE64URL


def test_task_state_part_preserves_official_enum_name() -> None:
    part = KeyPart.task_state("TASK_STATE_SUBMITTED")

    assert part.text == "TASK_STATE_SUBMITTED"
    assert part.codec is KeyPartCodec.TASK_STATE


def test_positive_sequence_part_uses_json_safe_decimal_ascii() -> None:
    part = KeyPart.positive_sequence(9_007_199_254_740_991)

    assert part.text == "9007199254740991"
    assert part.codec is KeyPartCodec.POSITIVE_SEQUENCE


_BOOTSTRAP_CASES = [
    (
        "AUTH_REPLAY",
        {"signer_hash": ("hash", _HASH), "request_id_hash": ("hash", _HASH)},
        f"auth:replay:{_HASH}:{_HASH}",
    ),
    (
        "DEDUPE",
        {
            "caller_hash": ("hash", _HASH),
            "target_agent_id": ("agent", "windows-a"),
            "message_id": ("safe", "msg_1~retry"),
        },
        f"dedupe:{_HASH}:windows-a:msg_1~retry",
    ),
    ("TASK", {"task_id": ("safe", "task-123")}, "task:task-123"),
    ("TASKS_UPDATED", {}, "tasks:updated"),
    (
        "TASKS_STATE",
        {"state": ("state", "TASK_STATE_SUBMITTED")},
        "tasks:state:TASK_STATE_SUBMITTED",
    ),
    (
        "CONTEXT_TASKS",
        {"context_id": ("safe", "ctx-123")},
        "context:ctx-123:tasks",
    ),
    (
        "CALLER_TASKS",
        {"principal_hash": ("hash", _HASH)},
        f"caller:{_HASH}:tasks",
    ),
    (
        "AGENT_TASKS",
        {"agent_id": ("agent", "windows-a")},
        "agent:windows-a:tasks",
    ),
    (
        "OUTBOX_EVENT",
        {"task_id": ("safe", "task-123"), "event_seq": ("seq", 1)},
        "outbox:event:task-123:1",
    ),
    ("OUTBOX_DUE", {}, "outbox:due"),
    (
        "OUTBOX_TASK",
        {"task_id": ("safe", "task-123")},
        "outbox:task:task-123",
    ),
    ("ADMISSION_GLOBAL", {}, "admission:global"),
    (
        "ADMISSION_PRINCIPAL",
        {"principal_hash": ("hash", _HASH)},
        f"admission:principal:{_HASH}",
    ),
    ("ADMISSION_PRINCIPALS", {}, "admission:principals"),
    (
        "ADMISSION_PRINCIPAL_FIFO",
        {"principal_hash": ("hash", _HASH)},
        f"admission:principal:{_HASH}:fifo",
    ),
    (
        "ADMISSION_TASK",
        {"task_id": ("safe", "task-123")},
        "admission:task:task-123",
    ),
    ("DISPATCH", {"task_id": ("safe", "task-123")}, "dispatch:task-123"),
]


@pytest.mark.parametrize(("kind_name", "raw_parts", "suffix"), _BOOTSTRAP_CASES)
def test_bootstrap_template_registry_renders_exact_golden_key(
    kind_name: str,
    raw_parts: dict[str, tuple[str, object]],
    suffix: str,
) -> None:
    builder = RedisKeyBuilder("mesh:prod")
    parts = {name: _part(codec, value) for name, (codec, value) in raw_parts.items()}

    rendered = builder.render(KeyKind[kind_name], **parts)

    assert rendered == f"a2am:v1:{{mesh:prod}}:{suffix}".encode()
    assert rendered.count(b"{") == 1
    assert rendered.count(b"}") == 1
    assert b"..." not in rendered
    assert b"tenant" not in rendered


@pytest.mark.parametrize(
    "mesh_id",
    [
        None,
        "",
        "a" * 129,
        "mesh prod",
        "mesh\u00a0prod",
        "mesh{prod}",
        "mesh}prod",
        "mesh\x00prod",
        "mesh\x7fprod",
        "mesh\u202eprod",
        "mesh\ud800prod",
    ],
)
def test_mesh_id_rejects_unsafe_or_non_utf8_values(mesh_id: object) -> None:
    with pytest.raises(KeyBuilderError) as raised:
        RedisKeyBuilder(mesh_id)  # type: ignore[arg-type]

    assert repr(mesh_id) not in str(raised.value)


@pytest.mark.parametrize("schema_version", ["v2", "V1", "", 1, None])
def test_builder_rejects_any_schema_version_other_than_exact_v1(
    schema_version: object,
) -> None:
    with pytest.raises(KeyBuilderError, match="schema_version"):
        RedisKeyBuilder("default", schema_version)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    ["", "a" * 129, "raw:principal", "a/b", "a{b}", "a b", "méssage"],
)
def test_safe_token_rejects_unapproved_or_delimiter_bearing_text(value: str) -> None:
    with pytest.raises(KeyBuilderError) as raised:
        KeyPart.safe_token(value)

    if value:
        assert value not in str(raised.value)


@pytest.mark.parametrize(
    "value",
    ["", "Windows-a", "windows.a", "-windows", "a" * 64, 1, None],
)
def test_agent_id_rejects_values_outside_authoritative_grammar(value: object) -> None:
    with pytest.raises(KeyBuilderError):
        KeyPart.agent_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "A" * 42,
        "A" * 44,
        "A" * 42 + "=",
        "A" * 42 + "+",
        "A" * 42 + "B",
        b"A" * 43,
    ],
)
def test_sha256_part_rejects_wrong_length_alphabet_or_padding_bits(
    value: object,
) -> None:
    with pytest.raises(KeyBuilderError):
        KeyPart.sha256_base64url(value)


@pytest.mark.parametrize(
    "value",
    ["SUBMITTED", "task_state_submitted", "TASK_STATE_", "TASK_STATE_" + "A" * 53],
)
def test_task_state_part_rejects_noncanonical_names(value: str) -> None:
    with pytest.raises(KeyBuilderError):
        KeyPart.task_state(value)


@pytest.mark.parametrize(
    "value",
    [0, -1, True, 1.0, "1", 9_007_199_254_740_992],
)
def test_positive_sequence_rejects_nonpositive_nonexact_or_unsafe_values(
    value: object,
) -> None:
    with pytest.raises(KeyBuilderError):
        KeyPart.positive_sequence(value)


def test_direct_key_part_construction_revalidates_canonical_sequence() -> None:
    with pytest.raises(KeyBuilderError):
        KeyPart("01", KeyPartCodec.POSITIVE_SEQUENCE)
    with pytest.raises(KeyBuilderError):
        KeyPart("9007199254740992", KeyPartCodec.POSITIVE_SEQUENCE)


def test_renderer_reports_deterministic_missing_and_extra_component_names() -> None:
    builder = RedisKeyBuilder("default")

    with pytest.raises(KeyBuilderError, match=r"missing=task_id"):
        builder.render(KeyKind.TASK)
    with pytest.raises(KeyBuilderError, match=r"extra=unexpected"):
        builder.render(
            KeyKind.TASKS_UPDATED,
            unexpected=KeyPart.safe_token("attacker-value"),
        )


def test_renderer_rejects_raw_parts_wrong_codec_and_unknown_kind_without_leak() -> None:
    builder = RedisKeyBuilder("default")
    malicious = "payload-secret"

    with pytest.raises(KeyBuilderError) as raw_error:
        builder.render(KeyKind.TASK, task_id=malicious)  # type: ignore[arg-type]
    assert malicious not in str(raw_error.value)

    with pytest.raises(KeyBuilderError, match="SHA256_BASE64URL") as codec_error:
        builder.render(
            KeyKind.CALLER_TASKS,
            principal_hash=KeyPart.safe_token("A" * 43),
        )
    assert "A" * 43 not in str(codec_error.value)

    with pytest.raises(KeyBuilderError, match="not registered"):
        builder.render("task", task_id=KeyPart.safe_token("task-123"))  # type: ignore[arg-type]


def test_bootstrap_registry_is_closed_and_has_no_arbitrary_suffix_kind() -> None:
    assert len(KeyKind) == 17
    assert {kind.name for kind in KeyKind} == {case[0] for case in _BOOTSTRAP_CASES}
    assert all("SUFFIX" not in kind.name and "RAW" not in kind.name for kind in KeyKind)


def test_state_facade_exports_key_builder_lazily_without_loading_redis() -> None:
    script = r'''
import sys
import a2amesh.state as state

expected = {
    "KeyBuilderError",
    "KeyKind",
    "KeyPart",
    "KeyPartCodec",
    "RedisKeyBuilder",
}
assert expected <= set(state.__all__)
assert "a2amesh.state.key_builder" not in sys.modules
assert "a2amesh.state.client" not in sys.modules
assert "a2amesh.state.config" not in sys.modules
assert "redis" not in sys.modules

builder = state.RedisKeyBuilder("default")
part = state.KeyPart.safe_token("task-1")
assert builder.render(state.KeyKind.TASK, task_id=part) == b"a2am:v1:{default}:task:task-1"
assert "a2amesh.state.key_builder" in sys.modules
assert "a2amesh.state.client" not in sys.modules
assert "a2amesh.state.config" not in sys.modules
assert "redis" not in sys.modules
'''
    result = subprocess.run(  # noqa: S603 - sys.executable is the trusted test interpreter
        [sys.executable, "-I", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _part(codec: str, value: object) -> KeyPart:
    factories = {
        "safe": KeyPart.safe_token,
        "agent": KeyPart.agent_id,
        "hash": KeyPart.sha256_base64url,
        "state": KeyPart.task_state,
        "seq": KeyPart.positive_sequence,
    }
    return factories[codec](value)
