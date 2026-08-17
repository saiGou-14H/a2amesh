"""Lifecycle and trust-boundary tests for the A2A v1 Peer Binding runtime."""

from __future__ import annotations

from pathlib import Path

import nacl.signing
import nkeys
import pytest

from a2amesh.identity import Principal, SignerPolicy, nkey_public_key
from a2amesh.runtime import V1PeerRuntime, V1PeerRuntimeState


class Subscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.subscription = Subscription()

    async def subscribe(self, subject: str, *, queue=None, cb=None):
        del cb
        self.calls.append((subject, queue))
        return self.subscription


class ProtectedProxy:
    uses_protected_local_ipc = True


class UnprotectedApplication:
    uses_protected_local_ipc = False


class ReplayGuard:
    async def claim(self, **kwargs):
        del kwargs
        return True


class Resolver:
    def resolve(self, message, envelope):
        raise AssertionError(f"not called during lifecycle test: {message!r} {envelope!r}")


def user_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    return nkeys.from_seed(
        nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    )


def runtime(connection: RecordingConnection) -> V1PeerRuntime:
    signer = nkey_public_key(user_key_pair())
    return V1PeerRuntime(
        connection,
        agent_id="worker",
        core_application=ProtectedProxy(),  # type: ignore[arg-type]
        signer_policies={
            signer: SignerPolicy(
                principal_ids=frozenset({"agent:caller"}),
                methods=frozenset({"nats-nkey"}),
                subjects=frozenset({"caller"}),
                principal_bindings={
                    "agent:caller": Principal("agent:caller", "agent", "caller-key", 0)
                },
            )
        },
        replay_guard=ReplayGuard(),
        identity_resolver=Resolver(),
        active_config_generation=42,
    )


@pytest.mark.asyncio
async def test_peer_runtime_subscribes_only_v1_literal_subject_and_closes_cleanly() -> None:
    connection = RecordingConnection()
    peer = runtime(connection)

    assert peer.state is V1PeerRuntimeState.NEW
    assert peer.subject == "a2a.v1.rpc.worker"
    await peer.start()
    assert peer.state is V1PeerRuntimeState.RUNNING
    assert connection.calls == [("a2a.v1.rpc.worker", "a2a-v1-worker-worker")]
    assert all(not subject.startswith("a2a.rpc.") for subject, _ in connection.calls)

    await peer.close()
    await peer.close()
    assert peer.state is V1PeerRuntimeState.CLOSED
    assert connection.subscription.unsubscribed is True
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        await peer.start()


@pytest.mark.asyncio
async def test_peer_runtime_rejects_duplicate_start() -> None:
    peer = runtime(RecordingConnection())
    await peer.start()
    with pytest.raises(RuntimeError, match="already running"):
        await peer.start()
    await peer.close()


def test_peer_runtime_rejects_direct_unprotected_application_core() -> None:
    connection = RecordingConnection()
    signer = nkey_public_key(user_key_pair())
    with pytest.raises(ValueError, match="Protected Local IPC"):
        V1PeerRuntime(
            connection,
            agent_id="worker",
            core_application=UnprotectedApplication(),  # type: ignore[arg-type]
            signer_policies={
                signer: SignerPolicy(
                    principal_ids=frozenset({"agent:caller"}),
                    methods=frozenset({"nats-nkey"}),
                    subjects=frozenset({"caller"}),
                    principal_bindings={
                        "agent:caller": Principal("agent:caller", "agent", "caller-key", 0)
                    },
                )
            },
            replay_guard=ReplayGuard(),
            identity_resolver=Resolver(),
            active_config_generation=42,
        )


def test_v1_peer_runtime_source_has_no_legacy_or_parallel_model_dependency() -> None:
    source = (
        Path(__file__).parents[1] / "src" / "a2amesh" / "runtime" / "v1_peer.py"
    ).read_text()
    assert "LegacyMesh" not in source
    assert "a2anats.compatibility" not in source
    assert "contracts.models" not in source
    assert "a2a.rpc." not in source
    assert "a2a.v1.rpc" in source
