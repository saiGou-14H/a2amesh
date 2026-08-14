# A2AMesh 开发实施计划

---

# 1. 文档目的

本文档给出 A2AMesh 从当前私有 NATS RPC 原型演进到可发布 A2A v1 Mesh 的完整实施计划，明确：

- 当前代码事实和目标设计的差距；
- V1 `CORE/INTEROP/EXTENDED` 累积交付剖面和明确不实现的范围；
- C0～C8 阶段的任务、依赖、文件、测试和退出门禁；
- Linux 公网节点与两台 Windows NAT Peer 的部署步骤；
- 兼容性、幂等、长任务、恢复、监控和上线准入；
- 风险、回滚、团队分工和持续维护方式。

本文件是唯一实施入口；业务与技术规则以同目录专项文档为权威来源。

## 1.1 文档维护方式

- 本文件持续更新，不在文件名中增加版本号；
- 阶段完成状态必须有测试/实机证据，不能仅凭代码存在勾选；
- 历史通过 Git 追踪；
- 带版本号的八份专项文档发布后不可原地改写，修订需递增版本；
- 任何 README 兼容声明必须引用本文件的门禁结果；
- V1/V2 表示交付范围，不是本文档版本。

## 1.2 实施依据

| 序号 | 文档 | 主要约束 |
|---:|---|---|
| 1 | 业务与总体架构设计 V1.3 | 交付剖面、状态事件提交、授权、容量、恢复与兼容 |
| 2 | Agent Card 与协议对象规范 V1.3 | 官方对象、Card publisher、Credential、剖面化 Binding 发布 |
| 3 | A2A 协议与 NATS 集成适配设计 V1.3 | Subject、AuthContext、Event Relay、N/N-1 兼容 |
| 4 | Redis 状态平面与数据设计 V1.3 | outbox、effect ledger、grant、admission、Lua、lease、幂等 |
| 5 | 任务生命周期与长任务运行时设计 V1.3 | Task ownership、Supervisor、副作用、取消对账、恢复 |
| 6 | 编排器、Runtime 与工具适配设计 V1.3 | Capability、SideEffectAdapter、公平准入、Runtime/Tool/MCP |
| 7 | 接口请求与响应标准 V1.3 | 身份授权、tenant、429/503、JSON-RPC/gRPC/MCP |
| 8 | 统计、审计与运行监控规则 V1.3 | outbox/effect/admission、日志、告警、RTO/RPO |
| 9 | 本实施计划 | 当前状态、顺序、交付和门禁 |

## 1.3 不作为实施依据

- 旧综合文档中与本套专项冲突的旧方法名和“已兼容”表述；
- 项目自带 client ↔ server 的自洽测试作为标准兼容证明；
- 未锁定版本的 Runtime CLI 博客/示例；
- 原型 UI 中未进入专项文档的字段；
- 被中断的审计任务结论；
- tenant/RBAC/Permission Center 方案（V1 明确不建设）。

---

# 2. 当前状态与实施结论

## 2.1 代码基线（2026-08-14）

当前代码仍是私有 NATS RPC 原型。源码/测试文件数、行数和通过数会随提交变化，不在架构计划中手工维护；每个阶段的实际基线由该阶段 CI 报告、锁文件、官方 fixture 和真机记录留证。本文不把历史测试结果视为当前兼容证据。

## 2.2 能力矩阵

| 能力 | 状态 | 现有位置 | 结论 |
|---|---|---|---|
| 项目 Pydantic AgentCard/Task/Message | 部分实现 | `contracts/models.py` | 需替换为官方对象 |
| 私有 NATS client/server | 已实现并有测试 | `a2anats/` | 作为迁移输入，不是 v1 Binding |
| 私有 send/stream/get/cancel | 部分实现 | `runtime/agent.py` | 语义需迁移到 11 操作 |
| Runtime Executor | 部分实现 | `runtime/executor.py` | 缺独立 heartbeat/lease |
| Hermes/Codex/Claude/OpenCode Adapter | 部分实现 | `runtime/adapters/` | 需固定版本 probe/fixture |
| Tool Registry/MCP | 部分实现 | `tools/` | 需风险策略、messageId 幂等、OAuth AS、workspace 安全 |
| Planner/Dispatcher/Tracker/Aggregator | 基础实现 | `orchestrator/` | 需 Redis 状态和官方 Task |
| KV/Memory | 基础实现 | `memory/store.py` | 不作为 Redis State 替代 |
| 官方 Agent Card well-known | 缺失 | — | C5 实现 |
| 官方 A2A v1 Application Core | 缺失 | — | C1 实现 |
| Redis State Service | 缺失 | — | C2 实现 |
| Redis outbox/Event Relay | 缺失 | — | C2/C3 实现 |
| Side-effect ledger/Adapter | 缺失 | — | C2/C4 实现 |
| Capability grant/admission | 缺失 | — | C2/C4/C5 实现 |
| NATS v1 Binding | 缺失 | — | C3 实现 |
| TaskSupervisor/Progress | 缺失 | — | C4 实现 |
| A2A JSON-RPC/SSE Gateway | 缺失 | — | C5 实现 |
| A2A gRPC Gateway | 缺失 | — | C6 INTEROP 实现，共用 Core/语义套件 |
| MCP Client/Server Bridge | 部分 Client 原型、Server 缺失 | `tools/` | C6 按 MCP 2026-07-28 完成 |
| Identity Resolver 共享逻辑/Credential Registry | 缺失 | — | C2/C3/C5 实现，不要求独立服务进程 |
| 外部 OAuth AS 集成/JWKS | 缺失 | — | C6/C7 实现；A2AMesh 不签发 Token |
| Push Dispatcher/Observer | 缺失 | — | C6 实现 |
| 生产监控/备份/真机门禁 | 缺失 | — | C6～C8 |

## 2.3 总体实施结论

当前版本只能称为：

> private A2A-inspired NATS prototype

兼容声明按交付剖面独立门禁：完成 canonical core、State Service/outbox、NATS Binding、长任务、JSON-RPC/SSE Gateway 和官方黑盒后，只能声明 `CORE / A2A v1 JSON-RPC compatible`。gRPC/Push 通过独立门禁后才能声明 `INTEROP`；MCP/Observer 通过独立门禁后才能声明 `EXTENDED`。NATS 始终只以自定义 Binding URI 声明。

---

# 3. 建设范围

## 3.1 CORE

1. A2A v1.0.1 官方对象、固定 SDK、11 个核心操作和统一 Application Core。
2. 每 Agent Card/JSON-RPC/SSE、ETag、Bearer、Progress/Runtime Extension 与 `A2A-Extensions`。
3. Redis State Service、Task/Context/Card/Principal、幂等、lease、outbox、side-effect ledger、capability grant 和 admission。
4. NATS v1 Binding、私有 inbox、Event Relay、JetStream 有序事件。
5. TaskSupervisor、heartbeat、process tree cancel、UNKNOWN 对账和恢复。
6. 至少一个通过固定版本 probe/fixture/真机门禁的 Runtime Adapter。
7. 最低指标、审计、Trace、健康、告警、备份和 RTO/RPO 演练。
8. Linux + 至少一个 NAT 后 Peer 双向调用。
9. NKey/Bearer 统一 Canonical Principal、可信 AuthContext、不可改指 alias 和最小 capability。
10. 官方 tenant 字段只接受空值，非空在任何 Task/队列/副作用前拒绝。

## 3.2 INTEROP

1. 官方 gRPC Binding 及与 JSON-RPC 共用的 11 操作语义套件。
2. A2A Push Dispatcher、SSRF/签名/重试/DLQ。
3. 额外 Runtime Adapter 与 Linux + 2 Windows 全拓扑真机矩阵。

## 3.3 EXTENDED

1. MCP 2026-07-28 Client（stdio/Streamable HTTP）和 Server Bridge（tools/resources）。
2. 外部 OAuth AS/JWKS、`mesh_submit_task.messageId` 强制幂等和 canonical payload hash。
3. Observer 规则聚合、只读分析和受控干预。

## 3.4 V1 明确不实现

- tenant、RBAC、用户/组织权限；
- 通用后台 UI；
- Redis Cluster/跨区域 HA；
- 任意 shell 公开 Skill；
- 自动重试未知副作用任务；
- 原始 Chain-of-Thought；
- 多 Mesh 联邦。

## 3.5 剖面门禁摘要

| 能力 | CORE | INTEROP | EXTENDED |
|---|---|---|---|
| 标准入口 | JSON-RPC/SSE | + gRPC/Push | + MCP Bridge |
| Runtime | 至少 1 个固定版本 | 额外 Adapter | 不新增强制 Runtime |
| 真机 | Linux + 1 NAT Peer | Linux + 2 Windows | 在 INTEROP 拓扑上增加 MCP/OAuth/Observer |
| 声明 | 只声明已通过 JSON-RPC interface | 才可追加 gRPC/Push | MCP 单独发现，不写入 A2A supportedInterfaces |

---

# 4. 目标工程结构

```text
src/a2amesh/
├── protocol/
│   ├── types.py
│   ├── application.py
│   ├── errors.py
│   ├── agent_card.py
│   ├── state_machine.py
│   └── extensions/
│       ├── runtime_selection.py
│       └── execution_progress.py
├── bindings/
│   ├── jsonrpc_http.py
│   ├── grpc_v1.py
│   └── nats_v1.py
├── state/
│   ├── client.py
│   ├── service.py
│   ├── redis_repository.py
│   ├── event_relay.py
│   ├── projector.py
│   ├── models.py
│   └── scripts/
│       ├── claim_message.lua
│       ├── transition_task.lua
│       ├── lease.lua
│       ├── upsert_card.lua
│       ├── resolve_principal.lua
│       ├── side_effect.lua
│       ├── authorize_capability.lua
│       └── admission.lua
├── identity/
│   ├── principal.py
│   ├── credentials.py
│   ├── aliases.py
│   └── auth_context.py
├── gateway/
│   ├── app.py
│   ├── auth.py
│   ├── oauth_resource.py
│   ├── routes.py
│   ├── sse.py
│   ├── grpc_server.py
│   ├── mcp_server.py
│   └── push_dispatcher.py
├── runtime/
│   ├── agent.py
│   ├── supervisor.py
│   ├── executor.py
│   ├── side_effects.py
│   ├── progress.py
│   ├── workspace.py
│   └── adapters/
├── orchestrator/
├── observer/
│   ├── consumer.py
│   ├── rules.py
│   └── policy.py
├── tools/
│   └── mcp/
│       ├── client.py
│       ├── stdio.py
│       ├── streamable_http.py
│       └── registry.py
├── mcp_bridge/
│   ├── tools.py
│   ├── resources.py
│   ├── auth.py
│   └── mapping.py
├── telemetry/
│   ├── metrics.py
│   ├── tracing.py
│   └── audit.py
└── cli.py

tests/
├── fixtures/a2a_v1/
├── unit/
├── integration/
├── conformance/
├── security/
└── e2e/
```

旧 `a2anats/` 在迁移完成前保留 compatibility adapter，C5 后默认关闭，最终删除或移入 `compat/v03/`。

---

# 5. 技术依赖

建议锁定：

```toml
[project.optional-dependencies]
a2a = [
  "a2a-sdk[grpc,http-server,signing,telemetry]==1.1.2",
  "redis[hiredis]==8.1.0"
]
mcp = [
  "mcp==2.0.0"
]
```

继续使用固定版本 `nats-py`、Pydantic、pytest。HTTP/gRPC server 优先复用官方 SDK `grpc` extra 的类型、stub 和 handler；不要自行实现另一套 JSON-RPC 或 Proto。MCP 规范固定 `2026-07-28`，Python SDK 固定 `2.0.0`；Server 使用 `mcp.server.mcpserver.MCPServer` 或低级 `mcp.server.lowlevel.Server`，不得导入已移除的 `mcp.server.fastmcp`。所有最终版本写入 lockfile。

开发环境使用 `uv`；不使用系统 pip 混装。

---

# 6. 实施原则

1. 每阶段先写失败测试，再实现最小代码。
2. 每个 Binding 共享 Application Core 语义测试。
3. current vs target 文档同步更新。
4. 迁移期间双协议必须显式开关，不能自动猜版本。
5. 所有副作用重试必须先完成端到端幂等。
6. 每个阶段有退出门禁，未通过不得开始兼容宣传。
7. 真机配置和 secret 不提交 Git。
8. 每阶段提交粒度小、可回滚；文档/Schema/测试同提交。
9. callerPrincipal 只由可信入口注入；任何业务 payload 自报身份必须失败。
10. tenant 验证、Principal resolve、幂等 claim 的顺序不可调整。

---

# 7. C0：基线冻结与协议准备

## 7.1 目标

冻结当前行为、引入官方 fixture、建立状态标签和 CI，不改生产协议。

## 7.2 文件

创建：

```text
tests/fixtures/a2a_v1/
tests/conformance/test_official_types.py
tests/conformance/test_compatibility_claims.py
scripts/verify_a2a_fixtures.py
```

修改：

```text
pyproject.toml
uv.lock
README.md
docs/specs/
```

## 7.3 任务

1. 锁定 A2A Spec v1.0.1 和 SDK 1.1.2。
2. 从官方 SDK 生成 Card/Message/Task/Artifact/Event/Error fixture。
3. 测试官方对象 parse/serialize。
4. README 标注当前为 prototype。
5. 建立 `current/target` 能力矩阵。
6. 运行当前测试并保存机器可读 CI 报告；不在文档中手工固化易漂移的通过数。
7. CI 增加 `pytest`、`ruff`、`compileall`、文档链接检查。
8. 增加非空 tenant、三类 Credential、AuthContext 和 MCP messageId fixture。

## 7.4 退出门禁

- 官方 fixture 全部可解析；
- 当前旧 fixture 与 v1 fixture 明确分开；
- CI 安装 wheel 后仍能读取 Schema/fixture；
- README 无虚假兼容声明。

---

# 8. C1：Canonical A2A Application Core

## 8.1 目标

用官方对象和传输无关接口实现 11 操作、错误和状态机。

## 8.2 文件

创建：

```text
src/a2amesh/protocol/types.py
src/a2amesh/protocol/application.py
src/a2amesh/protocol/errors.py
src/a2amesh/protocol/state_machine.py
src/a2amesh/protocol/agent_card.py
src/a2amesh/protocol/extensions/*.py
tests/unit/protocol/
```

## 8.3 任务

1. `types.py` 只 re-export 官方类型。
2. 定义 `A2AApplication` 11 个 async 方法。
3. 实现合法 TaskState 迁移表和终态保护。
4. 实现 Card builder/validator/ETag。
5. 实现 Runtime/Progress 扩展 Schema。
6. 建立错误分类，不依赖 HTTP/NATS。
7. 将旧 contracts 标记 internal/compat，禁止新代码导入其标准对象。
8. 为每个操作写 transport-independent contract test。
9. RequestContext 明确定义 Canonical Principal；11 个操作不得直接读取 Token/Binding metadata。
10. 官方 tenant 非空统一在 Core 前置 validator 拒绝，空值保持 SDK 兼容。

## 8.4 退出门禁

- 11 方法接口存在且有 contract test；
- 官方 SDK 可解析所有输入/输出；
- 非法终态回退、Task/Context 不匹配被拒绝；
- Card heartbeat 不改 ETag；
- 扩展被标准客户端忽略后对象仍合法。

---

# 9. C2：Redis State Service

## 9.1 目标

替换单进程 Task/Card 状态，建立 Principal/Credential/Alias、共享幂等、lease、outbox、side-effect ledger、capability grant、admission、List 和恢复基础。

## 9.2 文件

创建 `state/` 目录、Lua、integration tests、Docker test fixture。

## 9.3 顺序

1. Redis config 和 async client。
2. Key builder（mesh_id、安全编码）。
3. `claim_message.lua` + 并发测试。
4. `transition_task.lua` + 状态/索引测试。
5. lease/fencing + 双实例测试。
6. Card/presence/index。
7. Get/List/cursor。
8. Push config/delivery state。
9. NATS State Service handler，认证身份覆盖 payload。
10. retention cleaner/backup metrics。
11. `resolve_principal.lua`、Credential disable/rotation、不可改指 alias CAS/环测试。
12. Task 固化 callerPrincipal/credentialId/aliasGeneration，历史 owner 不随配置变化。
13. mutation 原子写 Task/索引/outbox；outbox due/dead/retention。
14. effect ledger 状态机、provider reference/hash、UNKNOWN reconciliation。
15. capability grant generation/expiry 和全维 fail-closed 匹配。
16. 全局/Principal admission counter、公平队列、queue deadline 和大小上限。

## 9.4 测试

```text
100 并发同 messageId → 1 Task
同 messageId 不同 payload → conflict
双 owner → 1 lease
旧 token late write → reject
List cursor → 无重/漏
Redis restart → Task/Card/dedupe 恢复
Credential rotation/disable → 新请求切换或拒绝，历史 owner 不变
alias chain/loop/retarget → reject
Task mutation 中途失败 → Task/索引/outbox 全回滚
effect timeout/断线 → UNKNOWN，禁止自动重试/取消成功
grant 任一维度不匹配 → claim/queue 前拒绝
Principal/global queue → 有界、公平、计数可回收
公网/Windows → 6379 不可达
```

## 9.5 退出门禁

所有状态 API 只经 State Service；新任务不再写进程 `_tasks` 权威字典；Task mutation 与 outbox 原子，UNKNOWN effect、授权失败、准入失败和 State 故障全部 fail closed。

---

# 10. C3：NATS v1 Binding

## 10.1 目标

实现版本化 Subject/Envelope、11 操作映射、私有 reply，以及 Redis outbox 经 Event Relay 发布到 JetStream 的唯一事件路径。

## 10.2 文件

```text
src/a2amesh/bindings/nats_v1.py
src/a2amesh/bindings/envelope.py
src/a2amesh/bindings/subjects.py
tests/integration/test_nats_v1_*.py
nats/config/*.conf
```

## 10.3 任务

1. Subject 安全构造和 ACL fixture。
2. Envelope 官方 payload parse。
3. unary request/reply。
4. caller 私有 inbox 流。
5. queue group 单执行。
6. Event Relay 扫描 due outbox，以 `taskId:eventSeq` 发布并等待 PubAck；消费者按 eventSequence 去重。
7. Card upsert/presence/state RPC。
8. 11 操作语义套件复用 C1 tests。
9. timeout retry 使用稳定 messageId。
10. 禁止宽 `_INBOX.>` 和可预测输出 subject。
11. RFC 8785 canonical Envelope + NKey Ed25519 AuthContext 签名/验证。
12. signer/method/subject/expiry/requestId/deadline/replay 校验；payload caller 字段不可信。
13. Envelope/State RPC/event schema 的 major reject 与 minor N/N-1 fixture。

## 10.4 退出门禁

- 两 Peer 并发实例只执行一次；
- 非授权 Peer 无法订阅他人回复/Task event；
- Core 与 NATS Binding 语义等价；
- NATS 重启/重连不重复终态。
- Peer/Gateway/MCP Bridge 均不能伪造他人 Principal，过期/重放 AuthContext 在 claim 前拒绝。
- Relay 在 publish 前后崩溃不丢事件；Projector 重放只更新派生视图，不覆盖新快照/终态。
- major mismatch fail closed，N/N-1 minor fixture 通过。

---

# 11. C4：Runtime 与长任务 Supervisor

## 11.1 目标

让静默长任务可观察、可取消、可恢复，完成 Progress、SideEffectAdapter、公平准入和派生 Projector。

## 11.2 文件

```text
runtime/supervisor.py
runtime/progress.py
runtime/workspace.py
state/projector.py
state/event_relay.py
observer/rules.py
tests/unit/runtime/test_supervisor.py
tests/integration/test_long_task_*.py
```

## 11.3 任务

1. 拆分 Executor 与 Supervisor。
2. 独立 heartbeat/lease/cancel/event 协程。
3. Linux process group + Windows Job Object/进程树。
4. RuntimeEvent contract、一个 CORE 基线 Adapter 的固定版本 probe/fixture/真机测试，以及可扩展 Adapter 接口。
5. Progress Extension 映射。
6. State mutation/outbox event sequence/attempt。
7. Projector 只维护派生视图，不合并覆盖 Redis 权威 Task。
8. workspace alias/realpath/lock。
9. CapabilityPolicy + authorize_capability。
10. 有界公平 admission、queue deadline、请求/Artifact/context 大小。
11. SideEffectAdapter、ledger、UNKNOWN reconcile/compensate。
12. Observer deterministic rules（EXTENDED 前暂不调用 LLM）。

## 11.4 门禁

- 60 秒无 stdout 仍有 heartbeat；
- 无换行输出不阻塞 cancel；
- lease lost 后不再副作用；
- unsafe 崩溃不自动重跑；
- UNKNOWN effect 未对账前不重试，取消返回 FAILED + reconciliation_required；
- Principal/全局队列有界且公平，429 与 503 语义区分；
- 多订阅者事件顺序一致；
- 目标 CORE Runtime 在其支持平台能杀完整子进程树；其他 Adapter/平台进入 INTEROP 独立门禁。

---

# 12. C5：CORE 标准 A2A JSON-RPC/SSE Gateway

## 12.1 目标

用官方 SDK/Proto 暴露 well-known Card、JSON-RPC、SSE 和 A2A service parameters/header，完成 `CORE` 兼容门禁。

## 12.2 文件

```text
gateway/app.py
gateway/routes.py
gateway/sse.py
gateway/auth.py
bindings/jsonrpc_http.py
tests/conformance/test_official_client_*.py
```

## 12.3 任务

1. 官方 Server Adapter 最小启动。
2. `https://<agentId>.agents.<baseDomain>/.well-known/agent-card.json`、Host 校验、ETag/304。
3. JSON-RPC 11 方法接 Core。
4. `A2A-Version: 1.0`、`A2A-Extensions` 和 `VersionNotSupportedError/-32009`。
5. SendStreaming/Subscribe SSE。
6. SSE comment keepalive、慢客户端上限。
7. Get/List/Cancel。
8. 每客户端独立 `meshBearer` credentialId/secret、常量时间验证、轮换窗口；不建设 RBAC。
9. 九个 A2A Error 的全名、-32001～-32009 与 HTTP 映射测试。
10. Gateway 非主节点：Peer 东西向调用旁路测试。
11. JSON-RPC/NATS 共用 operation fixture、状态机、错误和幂等套件。
12. 官方 JSON-RPC SDK 独立虚拟环境黑盒。
13. 关闭旧协议默认入口。
14. JSON-RPC 非空 tenant 返回 -32602，且无 Task/Redis/NATS 副作用。
15. Bearer 解析为 Canonical Principal，跨 Binding ownership/Get/List/Cancel 套件通过。
16. capability、请求大小、Principal/global admission 在排队前执行；429/503 映射通过。
17. CORE 最低 Metrics/Audit/Health/Alerts 和 outbox/effect/admission Runbook。

## 12.4 退出门禁

官方 JSON-RPC client 可执行 11 个操作，Card 只声明已通过的 JSON-RPC interface。C0～C5 门禁和至少 Linux + 1 NAT Peer 真机通过后，才可声明 `CORE / A2A v1 JSON-RPC compatible`；不得提前声明 gRPC、Push、MCP 或 Observer。

---

# 13. C6：INTEROP 与 EXTENDED

## 13.1 任务

1. gRPC `A2AService` 11 RPC、两个 server-streaming、metadata/deadline/cancel。
2. JSON-RPC/gRPC/NATS 共用 operation fixture；官方 gRPC stub 独立黑盒。
3. gRPC 非空 tenant 返回 `INVALID_ARGUMENT` 且无副作用；通过后 Card 才追加 gRPC interface。
4. Push CRUD 和 Redis 配置。
5. Dispatcher durable consumer。
6. HTTPS/SSRF/DNS rebinding/redirect 防护。
7. deliveryId、签名、timeout、退避、DLQ。
8. 其余目标 Runtime Adapter 的固定版本 probe/fixture/真机门禁。
9. Observer rule aggregation + 可选 LLM adapter。
10. observe/intervention policy、冷却和次数限制。
11. Extended Card（若启用）与 JWS 签名。
12. Metrics、Audit、Trace、Health、Alerts。
13. 运行看板和 P1 Runbook。
14. MCP Client：stdio/Streamable HTTP、initialize、tools/resources/prompts、schema/cache/cancel。
15. MCP Server Bridge：`/mcp`、tools/resources、A2A Task handle 映射。
16. MCP OAuth 2.1 Protected Resource Metadata、Origin、audience/resource、旧 HTTP+SSE 拒绝。
17. `mesh_submit_task` JSON Schema 强制 messageId；canonical SendMessageRequest hash；created/same/conflict 套件。
18. 外部 AS：RFC 9728/RFC 8414、client_credentials、issuer/audience/scope/TTL、RS256/ES256、JWKS rotation/outage。
19. OAuth client_id → Canonical Principal；Token 不透传；AS/JWKS 故障 fail closed。

## 13.2 INTEROP 退出门禁

- 官方 gRPC stub 语义门禁通过，Card 只在门禁后发布 gRPC interface；
- Push 慢/失败不阻塞 Task，重复 Webhook 可去重；
- SSRF 测试覆盖 loopback/private/metadata/redirect/rebinding；
- 目标额外 Runtime 的 probe/fixture/真机测试通过。

## 13.3 EXTENDED 退出门禁

- Observer 不处理普通 heartbeat、不自触发；
- 日志 secret/思维链扫描通过，Card 签名篡改失败（若声明）；
- MCP stdio/Streamable HTTP 与 OAuth/Origin/Task handle 黑盒通过；Windows 无 MCP 入站；
- MCP 同 messageId 超时/并发重试只产生一个 Task；冲突 payload 不执行；
- JWKS rotation/outage、未知 kid、错误 audience/scope、Token expiry 全部 fail closed。

完成 gRPC + Push 子集并通过 C8 对应真机门禁后可声明 `INTEROP`。完成 MCP/OAuth + Observer 子集并通过独立黑盒后可声明 `EXTENDED`。C6 部分完成不得把未通过的子集写入 Card 或 README。

---

# 14. C7：部署与安全加固

## 14.1 Linux

- NATS TLS/NKey/ACL/JetStream 持久目录；
- Redis loopback/ACL/AOF/noeviction；
- Gateway HTTPS；HTTP/2/gRPC 与 MCP Streamable HTTP 仅在部署目标包含对应剖面时启用；
- State/Gateway/Peer systemd 或容器；
- 日志轮转、磁盘告警、Redis/JetStream 异机加密备份（恢复点间隔不超过 15 分钟）；
- firewall 只开放 HTTPS 和 NATS TLS/WSS；
- secret 文件最小权限。
- 配置外部 OAuth AS issuer/resource/JWKS，验证 metadata 与 key rotation；AS 可同机独立容器或外部托管，但不是 A2AMesh 内嵌签发器。

## 14.2 Windows

- 原生 Python/uv 环境或打包产物；
- NATS TLS CA/NKey；
- Runtime executable probe；
- workspace alias；
- Windows Service/Task Scheduler 自启动；
- Credential Manager/ACL；
- 防火墙验证无入站；
- 进程树清理测试。

## 14.3 门禁

- secret 不在 Git/日志；
- Redis/NATS 管理端口公网不可达；
- ACL 含 JetStream 实测；
- 备份恢复演练；
- 服务重启 15 分钟、整机恢复 4 小时和整机丢失 RPO 15 分钟门禁；
- P1 告警可送达；
- 单 Linux SPOF 在运行手册明确。

---

# 15. C8：三机真机与发布

## 15.1 测试矩阵

| Caller | Target | 场景 |
|---|---|---|
| Linux | Windows A/B | send/stream/cancel/long task |
| Windows A | Linux/Windows B | 对称调用 |
| Windows B | Linux/Windows A | 对称调用 |
| 官方 JSON-RPC Client | Gateway | CORE 11 操作 |
| 官方 gRPC stub | Gateway | INTEROP 11 操作（仅交付 INTEROP 时） |
| MCP Client | MCP Bridge | EXTENDED tools/resources/OAuth（仅交付 EXTENDED 时） |

故障注入：

- 任务中断开浏览器 SSE；
- 任务中断开 Peer 网络；
- 重启 NATS/Redis/State/Gateway；
- 在 Event Relay publish 前后 kill 进程并检查 outbox/PubAck/去重；
- kill Runtime/Peer；
- 模拟 Push 500/timeout/重复；
- 让 Projector lag，验证 Get/List 权威快照不受影响；
- 模拟 provider 已执行但响应丢失，验证 effect UNKNOWN 与 reconciliation；
- 压满 Principal/global 队列，验证公平性、429/503 和计数回收；
- 磁盘接近水位；
- 重复 messageId 并发提交。
- 在隔离环境执行服务重启与完整节点恢复，记录 RTO/RPO。

## 15.2 发布门禁

1. 目标交付剖面的全部自动化测试通过，无未知 skip。
2. 目标剖面的官方 SDK/stub/MCP 报告归档。
3. CORE 至少 Linux + 1 NAT Peer；INTEROP/EXTENDED 使用 Linux + 2 Windows 目标矩阵。
4. 故障恢复无重复副作用，UNKNOWN effect 有可执行对账路径。
5. 指标/告警/Runbook/备份和 RTO/RPO 演练通过。
6. 文档/代码/配置一致。
7. 兼容声明由评审批准。

---

# 16. 关键业务链路实施检查

## 16.1 注册

```text
Peer probe Runtime
→ build official Card
→ connect NATS
→ State upsert_card
→ presence loop
→ Gateway well-known/query 可见
```

检查：generation、ETag、skill index、offline 不删 Card。

## 16.2 长任务

```text
SendMessage(returnImmediately)
→ claim/dedupe
→ dispatch/lease
→ Supervisor heartbeat/events
→ Projector/Redis
→ SSE/Push/Observer
→ terminal/Artifact
```

检查：无 stdout、断线、取消、lease lost、unsafe retry。

## 16.3 编排

```text
root Task
→ validate Plan DAG
→ select Agents
→ child Tasks
→ track independent results
→ aggregate Artifact
```

检查：workspace 写串行、fan-out、失败策略、来源保留。

---

# 17. 测试体系

## 17.1 Unit

- state machine；
- subject/key builder；
- Card/extension；
- planner validator；
- runtime adapter/parser；
- observer rules；
- error mapping。

## 17.2 Integration

- Redis Lua 并发；
- NATS queue/private inbox/JetStream；
- Projector；
- Supervisor subprocess；
- Gateway/Core/State；
- Push mock server。

## 17.3 Conformance

独立环境安装官方 SDK，不导入项目 client：Card、11 操作、版本、错误、流顺序、每个 advertised Binding。

## 17.4 Security

- subject/key/path injection；
- NKey/ACL；
- SSRF/rebinding/redirect；
- secret/log scan；
- tool/workspace escape；
- zip/file URI 等输入边界；
- slow consumer/DoS limit。

## 17.5 E2E

Linux + 2 Windows，真实 Runtime 可用性按环境标记；发布环境不得跳过核心 Runtime/网络门禁。

---

# 18. 配置与 Secret

建议主配置：

```yaml
mesh:
  id: default
  agent_id: windows-a
nats:
  servers: ["tls://mesh.example.com:4222"]
  credentials_file: "${A2AMESH_NATS_CREDS_FILE}"
state:
  request_timeout_seconds: 5
execution:
  max_concurrent_tasks: 4
  task_heartbeat_seconds: 5
  lease_ttl_seconds: 30
  lease_renew_seconds: 10
workspaces:
  repo:a2amesh:
    path: "C:\\work\\a2amesh"
    mode: read-write
runtimes:
  hermes:
    executable: hermes
```

Redis URL、Bearer、Webhook encryption key、NKey seed 不进入 YAML/Git。

---

# 19. 数据与协议迁移

1. 旧 Pydantic/JSON Schema 冻结为 compatibility fixture。
2. 新 Application Core 使用官方对象。
3. NATS v1 新 Subject 与旧 Subject 并行，仅测试环境。
4. Gateway/Peer feature flag 逐步切换。
5. 旧 Task 不迁移为可宣称的标准 Task；可只读查询或清理。
6. 观察一个 Task retention 窗口。
7. 默认关闭旧入口。
8. 删除前确认无旧 client/consumer。

禁止依据 payload 外观自动猜 v0.3/v1；必须按 endpoint/subject/version 明确路由。

---

# 20. 上线与回滚

## 20.1 上线

```text
备份 Redis/NATS 配置和数据
→ 部署 State/Redis scripts
→ 部署 NATS stream/ACL
→ 部署 Peer（不接生产流量）
→ 部署 Gateway canary
→ 官方黑盒
→ 一台 Windows canary
→ 三机全量
→ 观察指标/告警
```

## 20.2 回滚

- Gateway 可回滚到前一版本，但不能把新 v1 Task 交给不识别的旧 Core；
- 内部 major 不兼容时禁止混部；minor 只允许 N/N-1；Schema/Key 变更遵循读旧写新阶段；
- NATS Stream/Redis Key 不在应用回滚时立即删除；
- 正在执行 Task 优先完成/取消，不迁移 owner；
- 发生幂等/lease 不确定时停止新提交而不是冒险回滚执行状态。
- outbox、effect ledger 和 reconciliation 记录不得因应用回滚删除；存在 UNKNOWN 时禁止自动重放。

---

# 21. 团队分工

| 角色 | 责任 |
|---|---|
| 架构/协议 | C0/C1、规范、兼容门禁 |
| 状态/后端 | C2、Lua、outbox/effect/grant/admission、Event Relay、派生 Projector |
| NATS/网络 | C3、ACL、JetStream、Relay PubAck、N/N-1、NAT 真机 |
| Runtime | C4、Adapter、Supervisor、SideEffectAdapter、Windows process |
| Gateway | C5 CORE JSON-RPC/SSE/Auth/Admission；C6 gRPC |
| MCP/可观测/安全 | C6/C7、MCP、Push、Observer、监控、OAuth、SSRF、RTO/RPO |
| 测试 | conformance、故障注入、三机矩阵 |

一人可兼任，但每个退出门禁需独立复核。

---

# 22. 风险清单

| ID | 风险 | 控制 |
|---|---|---|
| R-001 | SDK/规范版本漂移 | 固定版本、官方 fixture、升级评审 |
| R-002 | 重试重复副作用 | messageId+payloadHash+State dedupe+side-effect ledger/provider idempotency |
| R-003 | lease split-brain | fencing token、每副作用前校验 |
| R-004 | stdout 静默误判 | 独立 heartbeat/process watchdog |
| R-005 | 私有 subject 泄露 | 随机 inbox、NKey ACL 实测 |
| R-006 | Push SSRF | HTTPS、DNS/IP/redirect 重校验 |
| R-007 | Windows 子进程残留 | Job Object/进程树真机门禁 |
| R-008 | Observer 反馈环 | 规则过滤、cause seq、冷却、次数、只读默认 |
| R-009 | Redis 单点/数据丢失 | AOF、备份、恢复演练、fail closed |
| R-010 | 公网 Linux 单点 | 明示 SPOF，V2 HA |
| R-011 | Runtime CLI 参数漂移 | probe、固定 fixture、真机 smoke |
| R-012 | 文档与代码失配 | 每阶段文档/测试同提交，声明门禁 |
| R-013 | Binding 身份漂移/伪造 | Canonical Principal、签名 AuthContext、跨 Binding contract test |
| R-014 | MCP 网络重试重复执行 | required messageId、canonical hash、State dedupe |
| R-015 | OAuth AS/JWKS 故障或错误轮换 | 短 TTL、缓存上限、未知 kid fail closed、Runbook |
| R-016 | tenant 误入数据模型 | 前置拒绝测试、Redis Key/schema 扫描 |
| R-017 | Redis/JetStream 双写丢事件 | 原子 outbox、Relay PubAck、eventId 去重、dead outbox 告警 |
| R-018 | 外部副作用结果未知 | UNKNOWN fail closed、provider/local reconcile、取消不伪造成功 |
| R-019 | 已认证调用者越权调用能力 | capability grant 全维匹配、generation/expiry、排队前拒绝 |
| R-020 | 队列耗尽或调用方饥饿 | 全局/Principal 有界队列、公平调度、deadline、429/503 区分 |
| R-021 | 滚动升级协议漂移 | major reject、minor N/N-1、read-old/write-new、Card 延迟广告 |
| R-022 | Card 多实例竞争 | 单 publisher ownership、generation CAS、presence 分离 |
| R-023 | 恢复目标不可证明 | 15 分钟/4 小时 RTO、15 分钟 RPO、异机备份和定期演练 |

---

# 23. 里程碑状态表

| 阶段 | 状态 | 证据/备注 |
|---|---|---|
| C0 基线 | 未开始（文档基线已建立） | 需代码 CI/官方 fixture |
| C1 Canonical Core | 未开始 | — |
| C2 Redis State | 未开始 | — |
| C3 NATS v1 | 未开始 | 现有私有 NATS 仅作输入 |
| C4 Long Task | 未开始 | 现有 stdout stream 为部分实现 |
| C5 CORE JSON-RPC/SSE Gateway | 未开始 | — |
| C6 INTEROP/EXTENDED | 未开始 | gRPC/Push 与 MCP/Observer 分别门禁 |
| C7 Deployment Hardening | 未开始 | — |
| C8 Real Machines/Release | 未开始 | — |

状态更新必须附命令输出、测试报告或真机记录；不得把设计完成标记为代码完成。

---

# 24. 最终上线准入

- 八份版本化专项文档及本实施计划完成评审；
- 目标交付剖面的 BR/NFR 有对应实现和 TEST；
- CORE 必须通过官方 JSON-RPC SDK；INTEROP 才要求 gRPC stub；EXTENDED 才要求 MCP 2026-07-28 client；
- 每个实际发布的 Card interface 通过同一语义套件，未交付 Binding 不得广告；
- Redis/NATS/Peer/Gateway/Runtime 故障注入通过；
- 长任务心跳、断线、取消、恢复通过；
- CORE 必须通过 Tool/capability/admission/effect 安全门禁；Push 属于 INTEROP，MCP/OAuth/Observer 属于 EXTENDED；
- CORE 必须通过 TEST-IDENTITY-001、TEST-TENANT-001、TEST-OUTBOX-001、TEST-EFFECT-001、TEST-AUTHZ-001、TEST-ADMISSION-001、TEST-DR-001；MCP/OAuth 测试仅在 EXTENDED 强制；
- CORE 至少 Linux + 1 NAT Peer 双向调用；INTEROP/EXTENDED 才要求 Linux + 2 Windows 任意方向；
- 监控、审计、告警、备份、Runbook 完整；
- 无高危未决缺陷；
- README 与实际能力一致。
