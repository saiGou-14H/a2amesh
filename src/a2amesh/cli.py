"""a2amesh 命令行入口（P0 骨架，完整实现见后续阶段）。"""
import argparse


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="a2amesh", description="A2AMesh 对称 A2A Agent Mesh")
    p.add_argument("action", nargs="?", default="help",
                   choices=["init", "bootstrap", "agent", "ingress", "orchestrator", "help"])
    args = p.parse_args(argv)
    print(f"a2amesh 0.1.0 · action={args.action}（实现中）")
    return 0
