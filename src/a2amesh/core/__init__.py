"""Canonical A2A application-core contracts shared by every binding."""

from .application import (
    CanonicalApplication,
    CanonicalRequestContext,
    dispatch_streaming,
    dispatch_unary,
    validate_application_contract,
)
from .operations import (
    OPERATION_SPECS,
    CapabilityRequirement,
    CoreCapabilities,
    DeliveryProfile,
    Operation,
    OperationAvailability,
    OperationSpec,
    operation_availability,
)

__all__ = [
    "OPERATION_SPECS",
    "CanonicalApplication",
    "CanonicalRequestContext",
    "dispatch_unary",
    "dispatch_streaming",
    "validate_application_contract",
    "CapabilityRequirement",
    "CoreCapabilities",
    "DeliveryProfile",
    "Operation",
    "OperationAvailability",
    "OperationSpec",
    "operation_availability",
]
