"""Aliases and strict ProtoJSON helpers for the pinned official A2A SDK."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from a2a import types as official
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtobufMessage

A2A_PROTOCOL_VERSION = "1.0"
A2A_SDK_VERSION = "1.1.2"

# Explicit aliases make accidental parallel Pydantic models visible in review.
AgentCard = official.AgentCard
AgentInterface = official.AgentInterface
Artifact = official.Artifact
CancelTaskRequest = official.CancelTaskRequest
DeleteTaskPushNotificationConfigRequest = official.DeleteTaskPushNotificationConfigRequest
GetExtendedAgentCardRequest = official.GetExtendedAgentCardRequest
GetTaskPushNotificationConfigRequest = official.GetTaskPushNotificationConfigRequest
GetTaskRequest = official.GetTaskRequest
ListTaskPushNotificationConfigsRequest = official.ListTaskPushNotificationConfigsRequest
ListTaskPushNotificationConfigsResponse = official.ListTaskPushNotificationConfigsResponse
ListTasksRequest = official.ListTasksRequest
ListTasksResponse = official.ListTasksResponse
Message = official.Message
Part = official.Part
Role = official.Role
SendMessageRequest = official.SendMessageRequest
SendMessageResponse = official.SendMessageResponse
StreamResponse = official.StreamResponse
SubscribeToTaskRequest = official.SubscribeToTaskRequest
Task = official.Task
TaskArtifactUpdateEvent = official.TaskArtifactUpdateEvent
TaskPushNotificationConfig = official.TaskPushNotificationConfig
TaskState = official.TaskState
TaskStatus = official.TaskStatus
TaskStatusUpdateEvent = official.TaskStatusUpdateEvent

MessageT = TypeVar("MessageT", bound=ProtobufMessage)


def to_protojson_dict(message: ProtobufMessage) -> dict[str, Any]:
    """Serialize an official protobuf message using lowerCamelCase ProtoJSON."""
    return cast(
        dict[str, Any],
        MessageToDict(
            message,
            preserving_proto_field_name=False,
            use_integers_for_enums=False,
        ),
    )


def to_protojson_bytes(message: ProtobufMessage) -> bytes:
    """Return deterministic UTF-8 ProtoJSON bytes for fixtures and transport.

    This is stable key ordering, not RFC 8785 canonicalization. Binding digest
    code must apply the design's explicit RFC 8785 rule separately.
    """
    return json.dumps(
        to_protojson_dict(message),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def from_protojson(data: Mapping[str, Any], message_type: type[MessageT]) -> MessageT:
    """Parse strict official ProtoJSON and reject unknown fields."""
    message = message_type()
    ParseDict(dict(data), message, ignore_unknown_fields=False)
    return message
