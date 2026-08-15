"""Canonical A2A application-core contracts shared by every binding."""

from .application import CanonicalApplication, CanonicalRequestContext
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
    "CapabilityRequirement",
    "CoreCapabilities",
    "DeliveryProfile",
    "Operation",
    "OperationAvailability",
    "OperationSpec",
    "operation_availability",
]
