"""生成测试用 NKey + 带 ACL 的 nats.test.conf（P3 安全测试）。"""
import json
from pathlib import Path

import nacl.signing
import nkeys

AGENTS = ["win1", "win2", "linux"]
pub_keys = {}
seed_lines = []

for name in AGENTS:
    sk = nacl.signing.SigningKey.generate()
    seed_bytes = nkeys.encode_seed(bytes(sk), nkeys.PREFIX_BYTE_USER)
    seed = seed_bytes.decode()
    kp = nkeys.from_seed(seed_bytes)
    pub_keys[name] = kp.public_key.decode()
    seed_lines.append(f"{name.upper()}_SEED={seed}")


def publish_permissions(name: str) -> list[str]:
    buckets = [
        f"A2AMESH_SESS_{name}",
        f"A2AMESH_MEM_{name}",
        "A2AMESH_MEM_SHARED",
    ]
    permissions = [
        "a2a.rpc.*",
        "a2a.cards.*",
        "$SRV.>",
        f"a2a.dlq.{name}",
    ]
    for bucket in buckets:
        permissions.append(f"$KV.{bucket}.>")
        permissions.extend(
            f"$JS.API.{operation}.KV_{bucket}"
            for operation in (
                "STREAM.INFO",
                "STREAM.CREATE",
                "DIRECT.GET",
                "STREAM.MSG.GET",
                "STREAM.MSG.DELETE",
            )
        )
    return permissions


def user_block(name: str) -> str:
    publish = ", ".join(json.dumps(item) for item in publish_permissions(name))
    return f'''    {{ nkey: "{pub_keys[name]}", permissions: {{
        subscribe: ["a2a.rpc.{name}", "a2a.cards.{name}", "$SRV.>", "_INBOX.{name}.>"]
        publish:   [{publish}]
        allow_responses: {{ max: -1, expires: "10m" }} }} }}'''


conf = f"""port: 4222
server_name: a2amesh
http: "127.0.0.1:8222"
jetstream {{ store_dir: "/tmp/nats/jetstream" }}

authorization {{
  users = [
{user_block('win1')},
{user_block('win2')},
{user_block('linux')}
  ]
}}
"""

output_dir = Path(__file__).resolve().parent
config_path = output_dir / "nats.test.conf"
seed_path = output_dir / ".test-seeds.env"
config_path.write_text(conf, encoding="utf-8")
seed_path.touch(mode=0o600, exist_ok=True)
seed_path.write_text("\n".join(seed_lines) + "\n", encoding="utf-8")
seed_path.chmod(0o600)
print("已生成 nats/nats.test.conf + nats/.test-seeds.env")
for name in AGENTS:
    print(f"{name} public: {pub_keys[name]}")
