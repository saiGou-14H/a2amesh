"""NKey authentication and subject ACL integration tests.

Run against a broker started with nats/gen_test_keys.py output:
A2AMESH_TEST_SECURE_NATS_URL=nats://127.0.0.1:4222 \
A2AMESH_TEST_WIN1_SEED=SU... pytest tests/test_security.py -q
"""

from __future__ import annotations

import asyncio
import os

import nacl.signing
import nkeys
import pytest

import nats
from a2amesh.memory.store import MemoryStore

SECURE_URL = os.getenv("A2AMESH_TEST_SECURE_NATS_URL")
WIN1_SEED = os.getenv("A2AMESH_TEST_WIN1_SEED")
pytestmark = pytest.mark.skipif(
    not SECURE_URL or not WIN1_SEED,
    reason="secure NATS integration environment not configured",
)


@pytest.mark.asyncio
async def test_correct_auth():
    nc = await nats.connect(
        SECURE_URL,
        nkeys_seed_str=WIN1_SEED,
        connect_timeout=3,
        max_reconnect_attempts=1,
    )
    await nc.close()


@pytest.mark.asyncio
async def test_no_auth():
    with pytest.raises(Exception):
        await nats.connect(
            SECURE_URL,
            connect_timeout=1,
            allow_reconnect=False,
            max_reconnect_attempts=0,
        )


@pytest.mark.asyncio
async def test_wrong_auth():
    signing_key = nacl.signing.SigningKey.generate()
    wrong = nkeys.encode_seed(bytes(signing_key), nkeys.PREFIX_BYTE_USER).decode()
    with pytest.raises(Exception):
        await nats.connect(
            SECURE_URL,
            nkeys_seed_str=wrong,
            connect_timeout=1,
            allow_reconnect=False,
            max_reconnect_attempts=0,
        )


@pytest.mark.asyncio
async def test_acl_cross_subscribe():
    errors: list[str] = []

    async def error_callback(error):
        errors.append(str(error))

    nc = await nats.connect(
        SECURE_URL,
        nkeys_seed_str=WIN1_SEED,
        error_cb=error_callback,
        max_reconnect_attempts=1,
    )
    try:
        await nc.subscribe("a2a.rpc.win2")
        await nc.flush()
        for _ in range(20):
            if errors:
                break
            await asyncio.sleep(0.05)
        assert any(
            "permissions" in error.lower() or "violation" in error.lower()
            for error in errors
        ), errors
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_acl_allows_own_kv_and_shared_memory():
    nc = await nats.connect(
        SECURE_URL,
        nkeys_seed_str=WIN1_SEED,
        inbox_prefix="_INBOX.win1",
        max_reconnect_attempts=1,
    )
    try:
        memory = await MemoryStore.create(
            nc.jetstream(),
            "win1",
            session_ttl_seconds=60,
        )
        await memory.session_append("acl-session", {"ok": True})
        assert await memory.session_get("acl-session") == [{"ok": True}]
        await memory.mem_set("own", "value")
        assert await memory.mem_get("own") == "value"
        await memory.shared_set("shared", "value")
        assert await memory.shared_get("shared") == "value"
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_acl_denies_other_agent_kv_subject():
    errors: list[str] = []

    async def error_callback(error):
        errors.append(str(error))

    nc = await nats.connect(
        SECURE_URL,
        nkeys_seed_str=WIN1_SEED,
        inbox_prefix="_INBOX.win1",
        error_cb=error_callback,
        max_reconnect_attempts=1,
    )
    try:
        await nc.publish("$KV.A2AMESH_MEM_win2.forbidden", b"secret")
        await nc.flush()
        for _ in range(20):
            if errors:
                break
            await asyncio.sleep(0.05)
        assert any(
            "permissions" in error.lower() or "violation" in error.lower()
            for error in errors
        ), errors
    finally:
        await nc.close()
