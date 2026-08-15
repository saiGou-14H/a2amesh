"""A2AMesh custom NATS v1 binding."""

from .auth import (
    AUTH_ALGORITHM,
    BindingAuthVerifier,
    RequestReplayGuard,
    VerifiedBindingIdentity,
    canonical_signing_bytes,
    sign_request_envelope,
)
from .envelope import (
    A2A_PROTOCOL_VERSION,
    BINDING_SCHEMA_VERSION,
    BINDING_URI,
    AuthContext,
    AuthProof,
    BindingRequestEnvelope,
    BindingValidationError,
)
from .response import BindingError, BindingResponseEnvelope
from .stream import (
    StreamFrameCursorV1,
    StreamFrameDisposition,
    StreamSessionFrameV1,
    StreamSessionOpenedV1,
)
from .stream_control import (
    StreamAckRequestV1,
    StreamCloseRequestV1,
    StreamControlKind,
    StreamControlResultV1,
    StreamOpenDigestContextV1,
    StreamOpenRequestV1,
    StreamSessionState,
    compute_stream_open_request_digest,
)

__all__ = [
    "A2A_PROTOCOL_VERSION",
    "AUTH_ALGORITHM",
    "BINDING_SCHEMA_VERSION",
    "BINDING_URI",
    "AuthContext",
    "AuthProof",
    "BindingAuthVerifier",
    "BindingError",
    "BindingRequestEnvelope",
    "BindingResponseEnvelope",
    "BindingValidationError",
    "RequestReplayGuard",
    "StreamAckRequestV1",
    "StreamCloseRequestV1",
    "StreamControlKind",
    "StreamControlResultV1",
    "StreamFrameCursorV1",
    "StreamFrameDisposition",
    "StreamOpenDigestContextV1",
    "StreamOpenRequestV1",
    "StreamSessionFrameV1",
    "StreamSessionOpenedV1",
    "StreamSessionState",
    "VerifiedBindingIdentity",
    "canonical_signing_bytes",
    "compute_stream_open_request_digest",
    "sign_request_envelope",
]
