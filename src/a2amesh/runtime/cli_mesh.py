"""mesh 命令：调度 / 发现 / 广播 / 工具调用。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from dotenv import load_dotenv


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mesh", description="A2AMesh 调度命令")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出在线 agent")
    p_list.add_argument("--config", default="agents.yaml")

    p_call = sub.add_parser("call", help="调度某 agent")
    p_call.add_argument("agent")
    p_call.add_argument("prompt")
    p_call.add_argument("--runtime", default=None)
    p_call.add_argument("--config", default="agents.yaml")

    p_broadcast = sub.add_parser("broadcast", help="广播到所有 agent")
    p_broadcast.add_argument("prompt")
    p_broadcast.add_argument("--config", default="agents.yaml")

    p_tool = sub.add_parser("tool", help="调用某 agent 的工具")
    p_tool.add_argument("agent")
    p_tool.add_argument("tool")
    p_tool.add_argument("args", help="JSON 参数")
    p_tool.add_argument("--config", default="agents.yaml")

    args = p.parse_args(argv)
    load_dotenv()
    asyncio.run(dispatch(args))
    return 0


async def dispatch(args):
    from a2amesh.a2anats.compatibility import LegacyMeshClientAdapter
    from a2amesh.config import Config
    from a2amesh.connect import connect
    from a2amesh.contracts.models import Message, TextPart

    cfg = Config.load(args.config)
    nc = await connect(cfg)
    client = LegacyMeshClientAdapter(
        nc,
        enabled=cfg.compatibility.legacy_private_rpc_enabled,
    )
    try:
        if args.cmd == "list":
            agents = await client.discover()
            if not agents:
                print("（无在线 agent）")
            for a in agents:
                runtimes = [r["name"] for r in a.capabilities.get("runtimes", [])]
                tools = [t["name"] for t in a.capabilities.get("tools", [])]
                print(f"  {a.name:12s} runtimes={runtimes} tools={tools}  {a.description}")
        elif args.cmd == "call":
            task = await client.send_message(
                args.agent,
                Message(role="user", parts=[TextPart(text=args.prompt)]),
                runtime=args.runtime,
            )
            print_task(task)
        elif args.cmd == "broadcast":
            tasks = await client.broadcast(args.prompt)
            for t in tasks:
                print(f"  [{t.id}] {t.status.state}")
        elif args.cmd == "tool":
            result = await client.call_tool(args.agent, args.tool, json.loads(args.args))
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await nc.close()


def print_task(task):
    from a2amesh.contracts.models import TextPart
    print(f"task {task.id}: {task.status.state}")
    for a in task.artifacts:
        for part in a.parts:
            if isinstance(part, TextPart):
                print(part.text)


if __name__ == "__main__":
    sys.exit(main())
