"""Contract tests for the binding-independent eleven-operation core."""

from __future__ import annotations

from google.protobuf.message import Message as ProtobufMessage

from a2amesh.core import (
    OPERATION_SPECS,
    CapabilityRequirement,
    CoreCapabilities,
    DeliveryProfile,
    Operation,
    OperationAvailability,
    operation_availability,
)

TASK_OPERATIONS = {
    Operation.SEND_MESSAGE,
    Operation.SEND_STREAMING_MESSAGE,
    Operation.GET_TASK,
    Operation.LIST_TASKS,
    Operation.CANCEL_TASK,
    Operation.SUBSCRIBE_TO_TASK,
}
PUSH_OPERATIONS = {
    Operation.CREATE_TASK_PUSH_NOTIFICATION_CONFIG,
    Operation.GET_TASK_PUSH_NOTIFICATION_CONFIG,
    Operation.LIST_TASK_PUSH_NOTIFICATION_CONFIGS,
    Operation.DELETE_TASK_PUSH_NOTIFICATION_CONFIG,
}


def test_registry_contains_exact_official_eleven_operations() -> None:
    assert set(OPERATION_SPECS) == set(Operation)
    assert len(OPERATION_SPECS) == 11
    assert {spec.api_id for spec in OPERATION_SPECS.values()} == {
        f"API-A2A-{number:03d}" for number in range(1, 12)
    }
    assert not ({"message/send", "message/stream", "tasks/get"} & {op.value for op in Operation})


def test_every_operation_uses_official_protobuf_types() -> None:
    for spec in OPERATION_SPECS.values():
        assert issubclass(spec.request_type, ProtobufMessage)
        assert issubclass(spec.response_type, ProtobufMessage)


def test_streaming_shape_is_fixed_to_two_operations() -> None:
    streaming = {operation for operation, spec in OPERATION_SPECS.items() if spec.streaming}
    assert streaming == {
        Operation.SEND_STREAMING_MESSAGE,
        Operation.SUBSCRIBE_TO_TASK,
    }


def test_capability_requirements_match_design() -> None:
    assert {
        operation
        for operation, spec in OPERATION_SPECS.items()
        if spec.capability is CapabilityRequirement.NONE
    } == TASK_OPERATIONS
    assert {
        operation
        for operation, spec in OPERATION_SPECS.items()
        if spec.capability is CapabilityRequirement.PUSH_NOTIFICATIONS
    } == PUSH_OPERATIONS
    assert (
        OPERATION_SPECS[Operation.GET_EXTENDED_AGENT_CARD].capability
        is CapabilityRequirement.EXTENDED_AGENT_CARD
    )


def test_core_task_operations_are_enabled_in_every_profile() -> None:
    for profile in DeliveryProfile:
        for operation in TASK_OPERATIONS:
            assert (
                operation_availability(operation, profile, CoreCapabilities())
                is OperationAvailability.ENABLED
            )


def test_push_requires_interop_profile_and_capability() -> None:
    enabled = CoreCapabilities(push_notifications=True)
    disabled = CoreCapabilities(push_notifications=False)
    for operation in PUSH_OPERATIONS:
        assert (
            operation_availability(operation, DeliveryProfile.CORE, enabled)
            is OperationAvailability.PUSH_NOTIFICATION_NOT_SUPPORTED
        )
        for profile in (DeliveryProfile.INTEROP, DeliveryProfile.EXTENDED):
            assert (
                operation_availability(operation, profile, disabled)
                is OperationAvailability.PUSH_NOTIFICATION_NOT_SUPPORTED
            )
            assert (
                operation_availability(operation, profile, enabled)
                is OperationAvailability.ENABLED
            )


def test_extended_card_is_explicit_capability_in_every_profile() -> None:
    for profile in DeliveryProfile:
        assert (
            operation_availability(
                Operation.GET_EXTENDED_AGENT_CARD,
                profile,
                CoreCapabilities(extended_agent_card=False),
            )
            is OperationAvailability.UNSUPPORTED_OPERATION
        )
        assert (
            operation_availability(
                Operation.GET_EXTENDED_AGENT_CARD,
                profile,
                CoreCapabilities(extended_agent_card=True),
            )
            is OperationAvailability.ENABLED
        )
