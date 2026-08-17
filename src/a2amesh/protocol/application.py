"""Public transport-independent Application Core facade.

The implementation currently lives in ``a2amesh.core.application`` because the
operation registry is owned by the ``core`` package.  This module is the stable
C1 protocol import path; it deliberately contains aliases, not a second Core.
"""

from a2amesh.core.application import (
    CanonicalApplication,
    CanonicalRequestContext,
    dispatch_streaming,
    dispatch_unary,
    validate_application_contract,
)

__all__ = [
    "CanonicalApplication",
    "CanonicalRequestContext",
    "dispatch_unary",
    "dispatch_streaming",
    "validate_application_contract",
]
