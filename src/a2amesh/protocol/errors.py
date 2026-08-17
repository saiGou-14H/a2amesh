"""Official A2A error classes exposed independently of any transport."""

from a2a.utils.errors import (
    A2AError,
    ContentTypeNotSupportedError,
    ExtendedAgentCardNotConfiguredError,
    ExtensionSupportRequiredError,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)

__all__ = [
    "A2AError",
    "ContentTypeNotSupportedError",
    "ExtendedAgentCardNotConfiguredError",
    "ExtensionSupportRequiredError",
    "InternalError",
    "InvalidAgentResponseError",
    "InvalidParamsError",
    "InvalidRequestError",
    "JSONParseError",
    "MethodNotFoundError",
    "PushNotificationNotSupportedError",
    "TaskNotCancelableError",
    "TaskNotFoundError",
    "UnsupportedOperationError",
    "VersionNotSupportedError",
]
