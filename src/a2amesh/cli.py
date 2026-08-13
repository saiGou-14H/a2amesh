"""a2amesh 命令行入口。"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="a2amesh", description="A2AMesh 对称 A2A Agent Mesh")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="生成 agents.yaml 骨架 + .env 模板")
    p_init.add_argument("--name", required=True)
    p_init.add_argument("--nats", default="nats://127.0.0.1:4222")
    p_init.add_argument("--dir", default=".")

    p_boot = sub.add_parser("bootstrap", help="生成 NKey seed 写入 .env")
    p_boot.add_argument("--dir", default=".")
    p_boot.add_argument("--env", default="A2AMESH_NKEY_SEED")

    p_agent = sub.add_parser("agent", help="启动 agent 服务")
    p_agent.add_argument("action", nargs="?", default="start", choices=["start"])
    p_agent.add_argument("--config", default="agents.yaml")

    sub.add_parser("ingress", help="启动网关（P6 实现）")
    p_orch = sub.add_parser("orchestrator", help="启动编排器")
    p_orch.add_argument("--config", default="agents.yaml")

    args = p.parse_args(argv)

    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "bootstrap":
        return cmd_bootstrap(args)
    if args.cmd == "agent":
        load_dotenv()
        asyncio.run(cmd_agent(args))
        return 0
    if args.cmd == "orchestrator":
        load_dotenv()
        asyncio.run(cmd_orchestrator(args))
        return 0
    if args.cmd == "ingress":
        print("ingress 尚未实现（P6）")
        return 0
    return 0


def cmd_init(args) -> int:
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    yaml_path = d / "agents.yaml"
    if yaml_path.exists():
        print(f"{yaml_path} 已存在，跳过")
    else:
        yaml_path.write_text(_AGENTS_YAML.format(name=args.name, nats=args.nats), encoding="utf-8")
        print(f"已生成 {yaml_path}")
    env_path = d / ".env"
    if not env_path.exists():
        env_path.write_text(
            f"A2AMESH_NKEY_SEED=\nA2AMESH_NATS_URL={args.nats}\nA2AMESH_AGENT_NAME={args.name}\n",
            encoding="utf-8",
        )
        print(f"已生成 {env_path}")
    print("下一步：a2amesh bootstrap 生成 NKey → a2amesh agent start")
    return 0


def cmd_bootstrap(args) -> int:
    import nacl.signing
    import nkeys
    from dotenv import set_key

    env_path = Path(args.dir) / ".env"
    if not env_path.exists():
        print("先运行 a2amesh init")
        return 1
    sk = nacl.signing.SigningKey.generate()
    seed_bytes = nkeys.encode_seed(bytes(sk), nkeys.PREFIX_BYTE_USER)
    seed = seed_bytes.decode()  # str 用于 .env / nats-py
    kp = nkeys.from_seed(seed_bytes)  # bytes 用于 from_seed
    set_key(str(env_path), args.env, seed)
    print(f"seed 已写入 {env_path}（{args.env}）")
    print(f"public key（交给 NATS 管理员登记到 nats.conf）: {kp.public_key.decode()}")
    return 0


async def cmd_agent(args) -> None:
    from a2amesh.config import Config
    from a2amesh.runtime.agent import AgentRuntime

    cfg = Config.load(args.config)
    rt = AgentRuntime(cfg)
    await rt.start()
    print(f"agent '{cfg.agent.name}' 已启动，监听 a2a.rpc.{cfg.agent.name}")
    try:
        await asyncio.Event().wait()  # 常驻
    finally:
        await rt.nc.close()


async def cmd_orchestrator(args) -> None:
    from a2amesh.config import Config
    from a2amesh.orchestrator.orchestrator import OrchestratorRuntime

    cfg = Config.load(args.config)
    rt = OrchestratorRuntime(cfg)
    await rt.start()
    print("orchestrator 已启动，监听 a2a.rpc.orchestrator")
    try:
        await asyncio.Event().wait()
    finally:
        await rt.nc.close()


_AGENTS_YAML = """\
nats:
  url: {nats}
  nkey_seed_env: A2AMESH_NKEY_SEED

agent:
  name: {name}
  description: "agent {name}"
  default_runtime: hermes
  workdir: "."
  runtimes: [hermes, codex, claude, opencode]
  tools_dir: ./tools
  public_tools: [read_file, list_dir]
  session_ttl_seconds: 86400
  task_timeout_seconds: 600

mcp: []

observability:
  otlp_endpoint: ""
  log_level: INFO
"""


if __name__ == "__main__":
    sys.exit(main())
