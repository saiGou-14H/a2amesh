"""The immutable eleven-operation A2A v1 core registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from google.protobuf.empty_pb2 import Empty
from google.protobuf.message import Message as ProtobufMessage

from a2amesh import protocol


class Operation(StrEnum):
    SEND_MESSAGE = "SendMessage"
    SEND_STREAMING_MESSAGE = "SendStreamingMessage"
    GET_TASK = "GetTask"
    LIST_TASKS = "ListTasks"
    CANCEL_TASK = "CancelTask"
    SUBSCRIBE_TO_TASK = "SubscribeToTask"
    CREATE_TASK_PUSH_NOTIFICATION_CONFIG = "CreateTaskPushNotificationConfig"
    GET_TASK_PUSH_NOTIFICATION_CONFIG = "GetTaskPushNotificationConfig"
    LIST_TASK_PUSH_NOTIFICATION_CONFIGS = "ListTaskPushNotificationConfigs"
    DELETE_TASK_PUSH_NOTIFICATION_CONFIG = "DeleteTaskPushNotificationConfig"
    GET_EXTENDED_AGENT_CARD = "GetExtendedAgentCard"


class DeliveryProfile(StrEnum):
    CORE = "CORE"
    INTEROP = "INTEROP"
    EXTENDED = "EXTENDED"


class CapabilityRequirement(StrEnum):
    NONE = "none"
    PUSH_NOTIFICATIONS = "push-notifications"
    EXTENDED_AGENT_CARD = "extended-agent-card"


class OperationAvailability(StrEnum):
    ENABLED = "enabled"
    PUSH_NOTIFICATION_NOT_SUPPORTED = "PushNotificationNotSupportedError"
    UNSUPPORTED_OPERATION = "UnsupportedOperationError"


@dataclass(frozen=True, slots=True)
class CoreCapabilities:
    push_notifications: bool = False
    extended_agent_card: bool = False


@dataclass(frozen=True, slots=True)
class OperationSpec:
    api_id: str
    request_type: type[ProtobufMessage]
    response_type: type[ProtobufMessage]
    handler_name: str
    streaming: bool = False
    capability: CapabilityRequirement = CapabilityRequirement.NONE


_OPERATION_SPECS = {
    Operation.SEND_MESSAGE: OperationSpec(
        "API-A2A-001",
        protocol.SendMessageRequest,
        protocol.SendMessageResponse,
        "send_message",
    ),
    Operation.SEND_STREAMING_MESSAGE: OperationSpec(
        "API-A2A-002",
        protocol.SendMessageRequest,
        protocol.StreamResponse,
        "send_streaming_message",
        streaming=True,
    ),
    Operation.GET_TASK: OperationSpec(
        "API-A2A-003", protocol.GetTaskRequest, protocol.Task, "get_task"
    ),
    Operation.LIST_TASKS: OperationSpec(
        "API-A2A-004",
        protocol.ListTasksRequest,
        protocol.ListTasksResponse,
        "list_tasks",
    ),
    Operation.CANCEL_TASK: OperationSpec(
        "API-A2A-005", protocol.CancelTaskRequest, protocol.Task, "cancel_task"
    ),
    Operation.SUBSCRIBE_TO_TASK: OperationSpec(
        "API-A2A-006",
        protocol.SubscribeToTaskRequest,
        protocol.StreamResponse,
        "subscribe_to_task",
        streaming=True,
    ),
    Operation.CREATE_TASK_PUSH_NOTIFICATION_CONFIG: OperationSpec(
        "API-A2A-007",
        protocol.TaskPushNotificationConfig,
        protocol.TaskPushNotificationConfig,
        "create_task_push_notification_config",
        capability=CapabilityRequirement.PUSH_NOTIFICATIONS,
    ),
    Operation.GET_TASK_PUSH_NOTIFICATION_CONFIG: OperationSpec(
        "API-A2A-008",
        protocol.GetTaskPushNotificationConfigRequest,
        protocol.TaskPushNotificationConfig,
        "get_task_push_notification_config",
        capability=CapabilityRequirement.PUSH_NOTIFICATIONS,
    ),
    Operation.LIST_TASK_PUSH_NOTIFICATION_CONFIGS: OperationSpec(
        "API-A2A-009",
        protocol.ListTaskPushNotificationConfigsRequest,
        protocol.ListTaskPushNotificationConfigsResponse,
        "list_task_push_notification_configs",
        capability=CapabilityRequirement.PUSH_NOTIFICATIONS,
    ),
    Operation.DELETE_TASK_PUSH_NOTIFICATION_CONFIG: OperationSpec(
        "API-A2A-010",
        protocol.DeleteTaskPushNotificationConfigRequest,
        Empty,
        "delete_task_push_notification_config",
        capability=CapabilityRequirement.PUSH_NOTIFICATIONS,
    ),
    Operation.GET_EXTENDED_AGENT_CARD: OperationSpec(
        "API-A2A-011",
        protocol.GetExtendedAgentCardRequest,
        protocol.AgentCard,
        "get_extended_agent_card",
        capability=CapabilityRequirement.EXTENDED_AGENT_CARD,
    ),
}

OPERATION_SPECS: Final = MappingProxyType(_OPERATION_SPECS)


def operation_availability(
    operation: Operation,
    profile: DeliveryProfile,
    capabilities: CoreCapabilities,
) -> OperationAvailability:
    """Return the deterministic profile/capability result for an operation."""
    requirement = OPERATION_SPECS[operation].capability
    if requirement is CapabilityRequirement.NONE:
        return OperationAvailability.ENABLED
    if requirement is CapabilityRequirement.PUSH_NOTIFICATIONS:
        if profile is DeliveryProfile.CORE or not capabilities.push_notifications:
            return OperationAvailability.PUSH_NOTIFICATION_NOT_SUPPORTED
        return OperationAvailability.ENABLED
    if not capabilities.extended_agent_card:
        return OperationAvailability.UNSUPPORTED_OPERATION
    return OperationAvailability.ENABLED
