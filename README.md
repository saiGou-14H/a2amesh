# A2AMesh

对称 A2A Agent Mesh：多台异构机器的 AI Agent（Hermes / Codex / OpenCode / Claude Code）经公网 NATS 注册中心互联，任意 agent 调度任意 agent，全程 A2A 语义，NAT 友好。

完整设计见 [docs/DESIGN.md](docs/DESIGN.md)。

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
