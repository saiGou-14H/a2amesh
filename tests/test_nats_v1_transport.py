"""Contract tests for the real signed A2A v1 NATS unary transport."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import nacl.signing
import nkeys
import pytest
from a2a.utils.errors import InvalidParamsError

from a2amesh import protocol
from a2amesh.bindings.nats_v1 import (
    BindingRemoteError,
    BindingTransportError,
    NatsCallerIdentity,
    V1NatsClient,
    V1NatsServer,
)
from a2amesh.core import Operation
from a2amesh.identity import Principal, SignerPolicy, nkey_public_key

NOW = datetime(2026, 8, 16, 6, 30, tzinfo=UTC)


class FakeSubscription:
    def __init__(self, broker: FakeBroker, subject: str, callback=None) -> None:
        self.broker = broker
        self.subject = subject
        self.callback = callback
        self.queue: asyncio.Queue[FakeMessage] = asyncio.Queue()
        self.closed = False

    async def next_msg(self, timeout: float) -> FakeMessage:
        return await asyncio.wait_for(self.queue.get(), timeout)

    async def unsubscribe(self) -> None:
        self.closed = True
        self.broker.subscriptions = [
            item for item in self.broker.subscriptions if item is not self
        ]


class FakeMessage:
    def __init__(
        self,
        broker: FakeBroker,
        subject: str,
        payload: bytes,
        reply: str | None,
    ) -> None:
        self.broker = broker
        self.subject = subject
        self.data = payload
        self.reply = reply

    async def respond(self, payload: bytes) -> None:
        if self.reply is None:
            raise AssertionError("message has no reply subject")
        await self.broker.publish(self.reply, payload, reply=None)


class FakeBroker:
    def __init__(self) -> None:
        self.subscriptions: list[FakeSubscription] = []
        self.published: list[tuple[str, str | None]] = []

    async def subscribe(self, subject: str, callback=None) -> FakeSubscription:
        subscription = FakeSubscription(self, subject, callback)
        self.subscriptions.append(subscription)
        return subscription

    async def publish(self, subject: str, payload: bytes, reply: str | None = None) -> None:
        self.published.append((subject, reply))
        matching = [
            item
            for item in self.subscriptions
            if not item.closed and item.subject == subject
        ]
        for subscription in matching:
            message = FakeMessage(self, subject, payload, reply)
            if subscription.callback is None:
                await subscription.queue.put(message)
            else:
                await subscription.callback(message)
        await asyncio.sleep(0)


class FakeConnection:
    def __init__(self, broker: FakeBroker, caller_agent_id: str) -> None:
        self.broker = broker
        self.inbox_prefix = f"_INBOX.a2amesh.{caller_agent_id}"
        self.counter = 0

    def new_inbox(self) -> str:
        self.counter += 1
        return f"{self.inbox_prefix}.reply{self.counter}"

    async def subscribe(self, subject: str, *, queue=None, cb=None):
        del queue
        return await self.broker.subscribe(subject, cb)

    async def publish(self, subject: str, payload: bytes, *, reply=None) -> None:
        await self.broker.publish(subject, payload, reply)

    async def flush(self) -> None:
        await asyncio.sleep(0)


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


class StaticResolver:
    def __init__(self, identity: NatsCallerIdentity) -> None:
        self.identity = identity
        self.calls = 0

    def resolve(self, message, envelope):
        del message, envelope
        self.calls += 1
        return self.identity


class CanonicalApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_message(self, request, context):
        del request
        self.calls.append(("send", context.principal_id, context.request_id))
        return protocol.SendMessageResponse(task=protocol.Task(id="task-v1"))

    async def get_task(self, request, context):
        del request
        self.calls.append(("get", context.principal_id, context.request_id))
        return protocol.Task(id="task-v1")


def key_pair() -> nkeys.KeyPair:
    signing_key = nacl.signing.SigningKey.generate()
    return nkeys.from_seed(
        nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER)
    )


def make_client(
    connection: FakeConnection,
    pair: nkeys.KeyPair,
    caller: str = "caller-a",
) -> V1NatsClient:
    return V1NatsClient(
        connection,
        key_pair=pair,
        principal_id="agent:caller-a",
        credential_id="caller-a-key",
        caller_agent_id=caller,
        caller_instance_id="instance-01",
        issuer="test-config",
        subject="caller-a",
        config_generation=42,
        clock=lambda: NOW,
    )


def make_server(
    broker: FakeBroker,
    pair: nkeys.KeyPair,
    app: CanonicalApp,
    guard: ReplayGuard | None = None,
) -> tuple[V1NatsServer, StaticResolver]:
    signer = nkey_public_key(pair)
    identity = NatsCallerIdentity(
        connection_public_key=signer,
        caller_agent_id="caller-a",
        caller_instance_id="instance-01",
        allowed_reply_prefix="_INBOX.a2amesh.caller-a.",
    )
    resolver = StaticResolver(identity)
    server = V1NatsServer(
        FakeConnection(broker, "server"),
        agent_id="worker",
        application=app,
        signer_policies={
            signer: SignerPolicy(
                principal_ids=frozenset({"agent:caller-a"}),
                methods=frozenset({"nats-nkey"}),
                subjects=frozenset({"caller-a"}),
                principal_bindings={
                    "agent:caller-a": Principal(
                        "agent:caller-a", "agent", "caller-a-key", 0
                    )
                },
            )
        },
        replay_guard=guard or ReplayGuard(),
        identity_resolver=resolver,
        active_config_generation=42,
        clock=lambda: NOW,
    )
    return server, resolver


@pytest.mark.asyncio
async def test_signed_v1_client_server_dispatches_canonical_send_and_get_without_legacy_subject(
) -> None:
    broker = FakeBroker()
    caller_connection = FakeConnection(broker, "caller-a")
    pair = key_pair()
    app = CanonicalApp()
    server, resolver = make_server(broker, pair, app)
    await server.start()
    client = make_client(caller_connection, pair)

    send_result = await client.send_message(
        protocol.SendMessageRequest(),
        target_agent_id="worker",
    )
    get_result = await client.get_task(
        protocol.GetTaskRequest(id="task-v1"),
        target_agent_id="worker",
    )

    assert send_result.task.id == "task-v1"
    assert get_result.id == "task-v1"
    assert [call[0] for call in app.calls] == ["send", "get"]
    rpc_subjects = [
        subject
        for subject, _ in broker.published
        if subject.startswith("a2a.v1.rpc.")
    ]
    assert rpc_subjects == ["a2a.v1.rpc.worker", "a2a.v1.rpc.worker"]
    assert all(not subject.startswith("a2a.rpc.") for subject, _ in broker.published)
    assert resolver.calls == 2
    await server.close()


class WrongResponseCanonicalApp(CanonicalApp):
    async def send_message(self, request, context):
        del request, context
        return protocol.Task(id="wrong-response")


class LongErrorCanonicalApp(CanonicalApp):
    async def send_message(self, request, context):
        del request, context
        raise InvalidParamsError(message="x" * 4097)


@pytest.mark.asyncio
async def test_server_bounds_long_official_error_and_still_replies_structured() -> None:
    broker = FakeBroker()
    pair = key_pair()
    server, _ = make_server(broker, pair, LongErrorCanonicalApp())
    await server.start()
    client = make_client(FakeConnection(broker, "caller-a"), pair)

    with pytest.raises(BindingRemoteError) as captured:
        await client.request(
            Operation.SEND_MESSAGE,
            protocol.SendMessageRequest(),
            target_agent_id="worker",
            timeout=1,
        )

    assert captured.value.error.type == "InvalidParamsError"
    assert len(captured.value.error.message) <= 4096
    await server.close()


@pytest.mark.asyncio
async def test_server_maps_core_response_contract_failure_without_legacy_fallback() -> None:
    broker = FakeBroker()
    pair = key_pair()
    app = WrongResponseCanonicalApp()
    server, _ = make_server(broker, pair, app)
    await server.start()
    client = make_client(FakeConnection(broker, "caller-a"), pair)

    with pytest.raises(BindingRemoteError, match="InvalidAgentResponseError"):
        await client.send_message(
            protocol.SendMessageRequest(),
            target_agent_id="worker",
        )
    await server.close()


@pytest.mark.asyncio
async def test_same_request_id_is_rejected_by_replay_guard_and_is_not_retried_to_legacy() -> None:
    broker = FakeBroker()
    caller_connection = FakeConnection(broker, "caller-a")
    pair = key_pair()
    app = CanonicalApp()
    guard = ReplayGuard()
    server, _ = make_server(broker, pair, app, guard)
    await server.start()
    client = make_client(caller_connection, pair)

    await client.request(
        Operation.SEND_MESSAGE,
        protocol.SendMessageRequest(),
        target_agent_id="worker",
        request_id="stable-request-01",
    )
    with pytest.raises(BindingRemoteError, match="InvalidBindingRequest"):
        await client.request(
            Operation.SEND_MESSAGE,
            protocol.SendMessageRequest(),
            target_agent_id="worker",
            request_id="stable-request-01",
        )
    assert all(not subject.startswith("a2a.rpc.") for subject, _ in broker.published)
    await server.close()


@pytest.mark.asyncio
async def test_streaming_is_rejected_at_v1_client_boundary_instead_of_falling_back() -> None:
    broker = FakeBroker()
    caller_connection = FakeConnection(broker, "caller-a")
    pair = key_pair()
    client = make_client(caller_connection, pair)

    with pytest.raises(BindingTransportError, match="StreamSession transport"):
        await client.request(
            Operation.SEND_STREAMING_MESSAGE,
            protocol.SendMessageRequest(),
            target_agent_id="worker",
        )
    assert broker.published == []


def test_nats_caller_identity_rejects_unsafe_or_non_user_identity() -> None:
    with pytest.raises(ValueError, match="user NKey"):
        NatsCallerIdentity(
            connection_public_key="A" * 56,
            caller_agent_id="caller-a",
            caller_instance_id="instance-01",
            allowed_reply_prefix="_INBOX.a2amesh.caller-a.",
        )
    pair = key_pair()
    with pytest.raises(ValueError, match="agent ID"):
        NatsCallerIdentity(
            connection_public_key=nkey_public_key(pair),
            caller_agent_id="caller.>",
            caller_instance_id="instance-01",
            allowed_reply_prefix="_INBOX.a2amesh.caller-a.",
        )


class BadInboxConnection(FakeConnection):
    def new_inbox(self) -> str:
        return "_INBOX.legacy.caller"


@pytest.mark.asyncio
async def test_client_rejects_bad_inbox_prefix_before_subscribe_or_publish() -> None:
    broker = FakeBroker()
    connection = BadInboxConnection(broker, "caller-a")
    client = make_client(connection, key_pair())

    with pytest.raises(BindingTransportError, match="authenticated caller prefix"):
        await client.request(
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-v1"),
            target_agent_id="worker",
        )
    assert broker.subscriptions == []
    assert broker.published == []


class WrongSubjectSubscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def next_msg(self, timeout: float) -> FakeMessage:
        del timeout
        return FakeMessage(
            FakeBroker(),
            "_INBOX.a2amesh.attacker.reply1",
            b"{}",
            None,
        )

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class WrongSubjectConnection(FakeConnection):
    def __init__(self, broker: FakeBroker, caller_agent_id: str) -> None:
        super().__init__(broker, caller_agent_id)
        self.reply_subscription = WrongSubjectSubscription()

    async def subscribe(self, subject: str, *, queue=None, cb=None):
        del subject, queue, cb
        return self.reply_subscription


@pytest.mark.asyncio
async def test_client_rejects_response_on_subject_other_than_signed_reply_inbox() -> None:
    broker = FakeBroker()
    connection = WrongSubjectConnection(broker, "caller-a")
    client = make_client(connection, key_pair())

    with pytest.raises(BindingTransportError, match="unexpected NATS subject"):
        await client.request(
            Operation.GET_TASK,
            protocol.GetTaskRequest(id="task-v1"),
            target_agent_id="worker",
        )
    assert connection.reply_subscription.unsubscribed is True


class InvalidResolver:
    def resolve(self, message, envelope):
        del message, envelope
        return object()


@pytest.mark.asyncio
async def test_server_rejects_invalid_identity_resolver_output_before_application() -> None:
    broker = FakeBroker()
    pair = key_pair()
    app = CanonicalApp()
    server, _ = make_server(broker, pair, app)
    server.identity_resolver = InvalidResolver()
    await server.start()
    client = make_client(FakeConnection(broker, "caller-a"), pair)

    with pytest.raises(BindingRemoteError, match="BindingTransportError"):
        await client.send_message(
            protocol.SendMessageRequest(),
            target_agent_id="worker",
        )
    assert app.calls == []
    await server.close()


class FailingCanonicalApp(CanonicalApp):
    async def send_message(self, request, context):
        del request, context
        raise RuntimeError("secret internal filesystem path")


@pytest.mark.asyncio
async def test_application_exception_is_sanitized_in_remote_error() -> None:
    broker = FakeBroker()
    pair = key_pair()
    app = FailingCanonicalApp()
    server, _ = make_server(broker, pair, app)
    await server.start()
    client = make_client(FakeConnection(broker, "caller-a"), pair)

    with pytest.raises(BindingRemoteError) as captured:
        await client.send_message(
            protocol.SendMessageRequest(),
            target_agent_id="worker",
        )
    assert captured.value.error.type == "InternalError"
    assert captured.value.error.message == "canonical application dispatch failed"
    assert "filesystem" not in str(captured.value)
    await server.close()


def test_client_rejects_non_user_nkey_pair() -> None:
    signing_key = nacl.signing.SigningKey.generate()
    account_pair = nkeys.from_seed(
        nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_ACCOUNT)
    )
    with pytest.raises(ValueError, match="user NKey"):
        make_client(FakeConnection(FakeBroker(), "caller-a"), account_pair)
