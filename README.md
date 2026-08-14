# A2AMesh

对称 Agent Mesh：多台异构机器的 AI Agent（Hermes / Codex / OpenCode / Claude Code）经公网 NATS 互联，任意 agent 可调度任意 agent，NAT 友好。

> **兼容性状态：** 当前代码是 A2A-inspired 的私有 NATS RPC 原型，尚未通过官方黑盒验证。V1.3 采用累积交付剖面：先完成 A2A JSON-RPC/SSE `CORE`，再按门禁增加 gRPC/Push `INTEROP` 与 MCP/Observer `EXTENDED`。

最新可实施设计入口见 [A2AMesh V1.3 设计文档索引](docs/specs/README.md)，包含 8 份当前专项文档及 1 份开发实施计划；V1.0～V1.2 位于 [历史归档](docs/archive/README.md)，不参与当前实现合同。

## 最新架构

- **南北向：** `CORE` 经每 Agent 通配子域名提供 JSON-RPC/SSE；gRPC 和 MCP 仅在对应剖面门禁通过后启用；
- **东西向：** Linux/Windows Peer 直接经 NATS v1 Binding 对称调用，不经过 Gateway；
- **状态面：** State Service 独占私有 Redis，管理权威 Task 快照、幂等、lease、outbox、副作用账本、capability grant 和准入状态；
- **事件面：** State mutation 原子写快照/outbox，Event Relay 经 PubAck 发布到 JetStream；Projector 只维护派生视图；
- **长任务：** TaskSupervisor 独立 heartbeat/cancel，外部副作用以 `PREPARED/APPLYING/APPLIED/UNKNOWN/COMPENSATED/FAILED` 对账；
- **INTEROP Push：** Push 配置写入 State Service，由独立 Push Dispatcher 消费 JetStream 并投递；
- **EXTENDED MCP：** Peer 消费配置的 stdio/Streamable HTTP Server；公网 Linux 只暴露白名单 Mesh tools/resources；
- **身份与授权：** 各入口共享 Identity Resolver 逻辑，把 NKey、A2A Bearer、MCP OAuth 统一为 Canonical Principal，再按目标 Agent/operation/skill/tool/workspace capability fail closed；
- **幂等：** MCP `mesh_submit_task` 强制稳定 messageId，与 A2A 共用 Redis claim/dedupe；
- **EXTENDED OAuth：** 外部 Authorization Server + RFC 9728/RFC 8414 + client_credentials/JWKS，故障 fail closed；
- **边界：** Gateway 不是 Mesh Leader 或固定调度主节点，V1 不建设 tenant/RBAC；服务重启 RTO 15 分钟，整机恢复 RTO 4 小时。

当前 Mermaid 拓扑和完整 ADR 见 [业务与总体架构设计 V1.3](docs/specs/A2AMesh_业务与总体架构设计_V1.3.md)。

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
