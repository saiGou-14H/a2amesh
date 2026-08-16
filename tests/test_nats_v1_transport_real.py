"""Optional real-broker smoke test for two signed A2A v1 caller identities."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

import nacl.signing
import nkeys
import pytest

import nats
from a2amesh import protocol
from a2amesh.bindings.nats_v1 import (
    NatsCallerIdentity,
    V1NatsClient,
    V1NatsServer,
)
from a2amesh.identity import SignerPolicy, nkey_public_key

NATS_URL = os.getenv("A2AMESH_TEST_NATS_URL")


class ReplayGuard:
    def __init__(self) -> None:
        self.claims: set[tuple[str, str, str]] = set()

    async def claim(self, *, principal_id, target_agent_id, request_id, expires_at):
        del expires_at
        key = (principal_id, target_agent_id, request_id)
        if key in self.claims:
            return False
        self.claims.add(key)
        return True


class IdentityResolver:
    def __init__(self, identities: dict[str, NatsCallerIdentity]) -> None:
        self.identities = identities
        self.verified_signers: list[str] = []

    def resolve(self, message, envelope):
        del message
        self.verified_signers.append(envelope.auth_proof.signer)
        return self.identities[envelope.auth_proof.signer]


class SmokeApplication:
    def __init__(self) -> None:
        self.principals: list[str] = []

    async def send_message(self, request, context):
        del request
        self.principals.append(context.principal_id)
        caller = context.principal_id.split(":", 1)[1]
        return protocol.SendMessageResponse(task=protocol.Task(id=f"task-{caller}"))


def make_user_key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    seed = nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    return nkeys.from_seed(seed)


async def run_dual_identity_smoke(nats_url: str) -> dict[str, object]:
    pairs = {"peer-a": make_user_key_pair(), "peer-b": make_user_key_pair()}
    signers = {name: nkey_public_key(pair) for name, pair in pairs.items()}
    identities = {
        signers[name]: NatsCallerIdentity(
            connection_public_key=signers[name],
            caller_agent_id=name,
            caller_instance_id=f"{name}-instance",
            allowed_reply_prefix=f"_INBOX.a2amesh.{name}.",
        )
        for name in pairs
    }
    policies = {
        signers[name]: SignerPolicy(
            principal_ids=frozenset({f"agent:{name}"}),
            methods=frozenset({"nats-nkey"}),
            subjects=frozenset({name}),
        )
        for name in pairs
    }

    server_nc = await nats.connect(
        nats_url,
        inbox_prefix="_INBOX.a2amesh.server",
        name="a2amesh-v1-smoke-server",
    )
    observer_nc = await nats.connect(
        nats_url,
        inbox_prefix="_INBOX.a2amesh.observer",
        name="a2amesh-v1-smoke-observer",
    )
    caller_connections = {
        name: await nats.connect(
            nats_url,
            inbox_prefix=f"_INBOX.a2amesh.{name}",
            name=f"a2amesh-v1-smoke-{name}",
        )
        for name in pairs
    }
    application = SmokeApplication()
    resolver = IdentityResolver(identities)
    server = V1NatsServer(
        server_nc,
        agent_id="worker",
        application=application,
        signer_policies=policies,
        replay_guard=ReplayGuard(),
        identity_resolver=resolver,
        active_config_generation=42,
    )
    v1_subjects: list[str] = []
    legacy_subjects: list[str] = []

    async def record_v1(message) -> None:
        v1_subjects.append(message.subject)

    async def record_legacy(message) -> None:
        legacy_subjects.append(message.subject)

    v1_observer = await observer_nc.subscribe("a2a.v1.rpc.>", cb=record_v1)
    legacy_observer = await observer_nc.subscribe("a2a.rpc.>", cb=record_legacy)
    await observer_nc.flush()
    await server.start()
    await server_nc.flush()

    clients = {
        name: V1NatsClient(
            caller_connections[name],
            key_pair=pairs[name],
            principal_id=f"agent:{name}",
            credential_id=f"{name}-key",
            caller_agent_id=name,
            caller_instance_id=f"{name}-instance",
            issuer="smoke-config",
            subject=name,
            config_generation=42,
        )
        for name in pairs
    }
    try:
        peer_a, peer_b = await asyncio.gather(
            clients["peer-a"].send_message(
                protocol.SendMessageRequest(),
                target_agent_id="worker",
            ),
            clients["peer-b"].send_message(
                protocol.SendMessageRequest(),
                target_agent_id="worker",
            ),
        )
        await asyncio.sleep(0.05)
        result = {
            "schemaVersion": "1.0",
            "executedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "brokerUrl": nats_url,
            "securityScope": (
                "application-envelope NKey signatures; development broker connection "
                "authentication not asserted"
            ),
            "callerPublicKeys": signers,
            "tasks": [peer_a.task.id, peer_b.task.id],
            "canonicalPrincipals": sorted(application.principals),
            "v1Subjects": sorted(v1_subjects),
            "legacySubjects": sorted(legacy_subjects),
            "verifiedSignerCount": len(set(resolver.verified_signers)),
        }
        assert result["tasks"] == ["task-peer-a", "task-peer-b"]
        assert result["canonicalPrincipals"] == ["agent:peer-a", "agent:peer-b"]
        assert result["v1Subjects"] == ["a2a.v1.rpc.worker", "a2a.v1.rpc.worker"]
        assert result["legacySubjects"] == []
        assert result["verifiedSignerCount"] == 2
        return result
    finally:
        await server.close()
        await v1_observer.unsubscribe()
        await legacy_observer.unsubscribe()
        for connection in caller_connections.values():
            await connection.close()
        await observer_nc.close()
        await server_nc.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not NATS_URL,
    reason="A2AMESH_TEST_NATS_URL is required for real NATS v1 smoke",
)
async def test_real_nats_dual_signed_identity_smoke() -> None:
    result = await run_dual_identity_smoke(NATS_URL)
    print("NATS_V1_DUAL_IDENTITY_SMOKE=" + json.dumps(result, sort_keys=True))
