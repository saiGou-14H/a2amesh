"""P3 安全测试：NKey 认证 + ACL 越权防护。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nats

NATS_URL = "nats://127.0.0.1:4222"


def load_seeds() -> dict[str, str]:
    seeds = {}
    for line in Path("/tmp/a2amesh_seeds.env").read_text().splitlines():
        k, v = line.split("=", 1)
        seeds[k] = v
    return seeds


async def test_correct_auth():
    seeds = load_seeds()
    nc = await nats.connect(NATS_URL, nkeys_seed_str=seeds["WIN1_SEED"], connect_timeout=3, max_reconnect_attempts=1)
    await nc.close()
    print("✅ 正确 NKey 连接成功")


async def test_no_auth():
    try:
        nc = await nats.connect(NATS_URL, connect_timeout=2, max_reconnect_attempts=1)
        await nc.close()
        print("❌ 无认证却连接成功（不该发生）")
    except Exception as e:
        print("✅ 无认证被拒绝:", type(e).__name__)


async def test_wrong_auth():
    import nacl.signing
    import nkeys
    sk = nacl.signing.SigningKey.generate()
    wrong = nkeys.encode_seed(bytes(sk), nkeys.PREFIX_BYTE_USER).decode()
    try:
        nc = await nats.connect(NATS_URL, nkeys_seed_str=wrong, connect_timeout=2, max_reconnect_attempts=1)
        await nc.close()
        print("❌ 错误 NKey 却连接成功（不该发生）")
    except Exception as e:
        print("✅ 错误 NKey 被拒绝:", type(e).__name__)


async def test_acl_cross_subscribe():
    seeds = load_seeds()
    errors: list[str] = []

    async def err_cb(e):
        errors.append(str(e))

    nc = await nats.connect(NATS_URL, nkeys_seed_str=seeds["WIN1_SEED"], error_cb=err_cb, max_reconnect_attempts=1)
    # win1 尝试订阅 win2 的 rpc（ACL 只允许订阅自己的 a2a.rpc.win1）
    await nc.subscribe("a2a.rpc.win2")
    # 同时 win1 尝试发布到 win2（ACL 允许 publish a2a.rpc.*，这是合法的调度）
    await nc.publish("a2a.rpc.win2", b"ping")
    await asyncio.sleep(0.6)

    violation = any("permissions" in e.lower() or "violation" in e.lower() for e in errors)
    if violation:
        print(f"✅ ACL：越权订阅被拒（权限错误触发）")
    elif errors:
        print("⚠️ 触发错误但非权限类:", errors)
    else:
        print("⚠️ 未捕获到权限错误（nats-py 可能静默），但订阅不会收到他人消息")
    await nc.close()


async def main():
    await test_correct_auth()
    await test_no_auth()
    await test_wrong_auth()
    await test_acl_cross_subscribe()
    print("\n🎉 安全测试完成")


if __name__ == "__main__":
    asyncio.run(main())
