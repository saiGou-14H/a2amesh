# A2AMesh

对称 Agent Mesh：多台异构机器的 AI Agent（Hermes / Codex / OpenCode / Claude Code）经公网 NATS 互联，任意 agent 可调度任意 agent，NAT 友好。

> **兼容性状态：** 当前代码是 A2A-inspired 的私有 NATS RPC 原型，尚未通过官方 A2A SDK 黑盒验证。目标是完整实现 A2A v1.0 标准 Agent Card、核心操作和 JSON-RPC/SSE。

最新可实施设计入口见 [A2AMesh V1 设计文档索引](docs/specs/README.md)，包含 8 份版本化专项文档及 1 份开发实施计划。旧综合设计已由本套文档替代，历史版本通过 Git 追溯。

## 快速开始

```bash
pip install -e .
a2amesh init --name win1 --nats wss://<公网IP>:4222
a2amesh bootstrap
a2amesh agent start
mesh list
mesh call win2 "执行 dir 并报告"
```

## 目录

- `src/a2amesh/` —— 核心库
- `nats/` —— NATS 配置
- `docs/` —— 设计文档
- `skills/` —— mesh skill（让 Hermes 当协调者）
