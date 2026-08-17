"""Regression contracts for total, non-leaking NATS error mapping."""

from __future__ import annotations

import pytest
from a2a.utils.errors import A2AError

from a2amesh.bindings.nats_v1.transport import (
    _safe_a2a_error_fields,
    _safe_binding_error_fields,
)


class UnknownA2AError(A2AError):
    pass


class ExplodingStringA2AError(A2AError):
    def __str__(self) -> str:
        raise RuntimeError("stringification failure")


@pytest.mark.parametrize("error", [UnknownA2AError(message="secret-unknown")])
def test_unknown_a2a_error_maps_to_fixed_internal_fields(error: A2AError) -> None:
    error_type, message = _safe_a2a_error_fields(error)

    assert (error_type, message) == ("InternalError", "canonical application error")
    assert _safe_binding_error_fields(error_type, message) == (
        "InternalError",
        "canonical application dispatch failed",
    )


def test_exploding_a2a_error_stringification_is_total_and_non_leaking() -> None:
    error_type, message = _safe_a2a_error_fields(ExplodingStringA2AError(message="secret"))

    assert (error_type, message) == ("InternalError", "canonical application error")
    assert "stringification failure" not in message


def test_unknown_binding_error_type_discards_the_supplied_message() -> None:
    assert _safe_binding_error_fields("UnknownError", "secret-short") == (
        "InternalError",
        "canonical application error",
    )


def test_non_string_binding_error_type_cannot_break_error_mapping() -> None:
    assert _safe_binding_error_fields(["unhashable"], "secret") == (
        "InternalError",
        "canonical application error",
    )
