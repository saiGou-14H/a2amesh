"""生成测试用 NKey + 带 ACL 的 nats.test.conf（P3 安全测试）。"""
import nacl.signing
import nkeys
from pathlib import Path

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


def user_block(name: str) -> str:
    return f'''    {{ nkey: "{pub_keys[name]}", permissions: {{
        subscribe: ["a2a.rpc.{name}", "a2a.cards.{name}", "a2a.stream.{name}.>", "$SRV.>", "_INBOX.>"],
        publish:   ["a2a.rpc.*", "a2a.cards.*", "a2a.stream.{name}.>", "$SRV.>", "_INBOX.>"] }} }}'''


conf = f"""port: 4222
server_name: a2amesh
http: 8222
jetstream {{ store_dir: "/tmp/nats/jetstream" }}

authorization {{
  users = [
{user_block('win1')},
{user_block('win2')},
{user_block('linux')}
  ]
}}
"""

Path("/root/a2amesh/nats/nats.test.conf").write_text(conf, encoding="utf-8")
Path("/tmp/a2amesh_seeds.env").write_text("\n".join(seed_lines) + "\n", encoding="utf-8")
print("已生成 nats/nats.test.conf + /tmp/a2amesh_seeds.env")
for name in AGENTS:
    print(f"{name} public: {pub_keys[name]}")
