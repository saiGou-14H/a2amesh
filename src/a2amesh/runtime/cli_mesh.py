"""mesh 命令（调度其他 agent，P0 骨架）。"""
import argparse


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mesh", description="A2AMesh 调度命令")
    p.add_argument("action", nargs="?", default="list",
                   choices=["list", "call", "broadcast", "tool"])
    args = p.parse_args(argv)
    print(f"mesh · action={args.action}（实现中）")
    return 0
