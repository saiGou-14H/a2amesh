"""Runtime wiring for an A2A v1 Peer Binding process.

The Peer Binding is not the Application Core.  Its injected application endpoint
must be a Protected Local IPC proxy as required by NATS integration profile 16.9.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

from a2amesh.bindings.nats_v1 import (
    NatsCallerIdentityResolver,
    V1NatsServer,
)
from a2amesh.bindings.nats_v1.auth import RequestReplayGuard
from a2amesh.bindings.nats_v1.transport import NatsServerConnection
from a2amesh.core.application import CanonicalApplication
from a2amesh.identity import SignerPolicy


class V1PeerRuntimeState(StrEnum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    CLOSED = "CLOSED"


class ProtectedCoreApplicationProxy(CanonicalApplication, Protocol):
    """CanonicalApplication proxy whose calls cross protected local IPC profile v1."""

    @property
    def uses_protected_local_ipc(self) -> bool: ...


class V1PeerRuntime:
    """Start only the signed ``a2a.v1.rpc.<agent>`` Peer Binding listener."""

    def __init__(
        self,
        nc: NatsServerConnection,
        *,
        agent_id: str,
        core_application: ProtectedCoreApplicationProxy,
        signer_policies: Mapping[str, SignerPolicy],
        replay_guard: RequestReplayGuard,
        identity_resolver: NatsCallerIdentityResolver,
        active_config_generation: int,
    ) -> None:
        if getattr(core_application, "uses_protected_local_ipc", False) is not True:
            raise ValueError(
                "Peer Binding requires a Protected Local IPC CanonicalApplication proxy"
            )
        self._server = V1NatsServer(
            nc,
            agent_id=agent_id,
            application=core_application,
            signer_policies=signer_policies,
            replay_guard=replay_guard,
            identity_resolver=identity_resolver,
            active_config_generation=active_config_generation,
        )
        self._state = V1PeerRuntimeState.NEW

    @property
    def state(self) -> V1PeerRuntimeState:
        return self._state

    @property
    def subject(self) -> str:
        return self._server.subject

    async def start(self) -> None:
        if self._state is V1PeerRuntimeState.CLOSED:
            raise RuntimeError("closed v1 Peer Runtime cannot be restarted")
        if self._state is V1PeerRuntimeState.RUNNING:
            raise RuntimeError("v1 Peer Runtime is already running")
        await self._server.start()
        self._state = V1PeerRuntimeState.RUNNING

    async def close(self) -> None:
        if self._state is V1PeerRuntimeState.CLOSED:
            return
        await self._server.close()
        self._state = V1PeerRuntimeState.CLOSED
