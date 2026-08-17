# A2AMesh 开发实施计划

---

## 1. 文档目的

本文档给出 A2AMesh 从当前私有 NATS RPC 原型演进到可发布 A2A v1 Mesh 的完整实施计划，明确：

- 当前代码事实和目标设计的差距；
- V1 `CORE/INTEROP/EXTENDED` 累积交付剖面和明确不实现的范围；
- C0～C8 阶段的任务、依赖、文件、测试和退出门禁；
- Linux 公网节点与两台 Windows NAT Peer 的部署步骤；
- 兼容性、幂等、长任务、恢复、监控和上线准入；
- 风险、回滚、团队分工和持续维护方式。

本文件是唯一实施入口；业务与技术规则以同目录专项文档为权威来源。

### 1.1 文档维护方式

- 本文件持续更新，不在文件名中增加版本号；
- 阶段完成状态必须有测试/实机证据，不能仅凭代码存在勾选；
- 历史通过 Git 追踪；
- 带版本号的十一份专项文档发布后不可原地改写，修订需递增版本；
- 任何 README 兼容声明必须引用本文件的门禁结果；
- V1/V2 表示交付范围，不是本文档版本。

### 1.2 实施依据

| 序号 | 文档 | 主要约束 |
|---:|---|---|
| 1 | 业务与总体架构设计 V1.6 | dispatch/cancel/outbox/effect、交付剖面、授权、容量和共同恢复点 |
| 2 | Agent Card 与协议对象规范 V1.6 | 官方对象、公开/内部 Card 边界、publisher、剖面化发布 |
| 3 | A2A 协议与 NATS 集成适配设计 V1.6 | durable dispatch、Subject/ACL、Auth replay、Relay、N/N-1 |
| 4 | Redis 状态平面与数据设计 V1.6 | dispatch/outbox/effect/Plan/replay/workspace/DR 原子合同 |
| 5 | 任务生命周期与长任务运行时设计 V1.6 | 完整迁移矩阵、Supervisor、取消/对账、恢复 |
| 6 | 编排器、Runtime 与工具适配设计 V1.6 | Plan、DRR、公平准入、Runtime containment、Tool/MCP |
| 7 | 接口请求与响应标准 V1.6 | 11 操作矩阵、入口顺序、身份、错误和 Binding version |
| 8 | Artifact 与对象存储设计 V1.2 | 稳定 URI、竞态、授权保留、对象完整性和共同恢复 |
| 9 | 受信配置与变更治理设计 V1.2 | genesis、hash/JWS、可信 READY、generation rollout |
| 10 | 人工对账与运维操作设计 V1.2 | 正交 case 状态、effect attempt、陈旧 APPLYING、历史不可变 |
| 11 | 统计、审计与运行监控规则 V1.6 | dispatch/outbox/effect/audit sink/Recovery Manifest 指标告警 |
| 12 | 本实施计划 | 当前状态、顺序、交付和门禁 |

### 1.3 不作为实施依据

- 旧综合文档中与本套专项冲突的旧方法名和“已兼容”表述；
- 项目自带 client ↔ server 的自洽测试作为标准兼容证明；
- 未锁定版本的 Runtime CLI 博客/示例；
- 原型 UI 中未进入专项文档的字段；
- 被中断的审计任务结论；
- tenant/RBAC/Permission Center 方案（V1 明确不建设）。

---

## 2. 当前状态与实施结论

### 2.1 代码基线（2026-08-16）

本节记录的上一轮状态核验起始基线为 `9e5d9ea8da4a868b316f9a5e2172ce96d320a6c4`（`main`；核验时间 2026-08-16T18:57:39Z）。当前工作树包含尚未提交的R11-C状态合同、测试、规范和发布资产候选修改；该dirty state不是发布基线，必须在最终门禁通过后由checkpoint commit重新绑定。当前代码仍是私有 NATS RPC 原型；源码/测试数量随提交变化，不在计划中手工维护。后续状态更新必须记录新的commit SHA、dirty state、命令输出和证据 URI。

V1.6/V1.2 当前为 **G0 候选设计集**：首轮独立复审问题已纳入修复，但必须在关闭复审、评审台账和 content manifest 完成后才能标记正式冻结；无论设计状态如何，均不表示 C0 代码、官方兼容或生产验收完成。

### 2.1.1 R11 当前checkpoint（2026-08-16）

本节是living status，不改写上面的历史起始基线。当前仍是未冻结dirty候选；`HEAD=b9e929d4ed838099218e9d5102eb0ea06627c39b`仅作本轮修改前历史锚点，先前记录的tracked/untracked数量与diff/tree指纹均已失效。P1修复、全量门禁及二次独立复审完成前不生成新的release fingerprint；最终checkpoint必须一次性记录HEAD、status、diff SHA、index/tree OID和scoped hashes。架构资产当前path allowlist为53条，签名SHA-256=`7678eb87225928330523c8adfbef7de74d8c4986b1868f5d00f59f42f626db58`。

| 证据层 | 当前观察 | 能证明 | 不能证明 |
|---|---|---|---|
| 纯Python Artifact contract | `PYTHONDONTWRITEBYTECODE=1 uv run --extra test pytest -q tests/test_artifact_hold_expiry.py -p no:cacheprovider` → `85 passed` | strict wire、State-side SCAN allocation proof、SCAN authority seal、REPLAY current-authority pointer、cross-record fail-closed、单CAS write-set的确定性行为 | Redis Function/Lua、restart durability、真实NATS/AuthProof/ACL、跨进程/三机故障 |
| 架构资产静态门禁 | `tests/test_architecture_assets.py` → `9 passed`；machine-edge allowlist exact、53条path签名allowlist、SVG/HTML twin bytes相同；`TEST-ASSET-ARCH-001` | 当前SVG/HTML的路径集合、角色标签和声明边界 | 运行时ACL、真实组件身份、Redis/NATS权限 |
| 浏览器资产门禁 | `A2AMESH_REQUIRE_BROWSER_GATE=1 uv run --locked --extra test --extra browser-test pytest -q tests/test_architecture_browser_smoke.py` → `1 passed` | 真实Chromium下HTML标准模式、无page error、视口/缩放/控件/布局约束 | 任何Redis/NATS/A2A/Windows/NAT/安全授权能力 |
| 真实集成/发布 | 尚未完成 | — | 全量release verdict、官方A2A黑盒、Redis重启、NATS secure ACL/AuthProof、Windows NAT、多进程恢复、Object Store/生产就绪 |

R11-C/R11-D局部GREEN不能关闭G0，也不能改变`private A2A-inspired NATS prototype`结论。最终release gate仍必须在最终dirty tree执行全量pytest、Ruff、JetStream、sdist隔离、强制Chromium及独立复审；未完成前checkpoint commit不得使用`[verified]`。

### 2.2 能力矩阵

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
| Config/Artifact可执行纯状态合同 | 部分原型 | `config_slots.py`、`state_contracts/artifact_hold.py`及专项测试 | 已覆盖RequiredSlot投影和Artifact Hold严格wire/ledger/单CAS write-set、REPLAY_CLAIM current authority、immutable candidate/base-commit digest binding；不等于Redis Function、持久化、NATS/AuthProof或secure ACL集成 |
| Redis outbox/Event Relay | 缺失 | — | C2/C3 实现 |
| Side-effect ledger/Adapter | 缺失 | — | C2/C4 实现 |
| Capability grant/admission | 缺失 | — | C2/C4/C5 实现 |
| Artifact Broker/Object Store | 缺失 | — | C2/C4/C5/C7 实现并恢复验收 |
| Signed Config Controller/lifecycle | 缺失 | — | C2/C5/C7 实现，单 active generation |
| Reconciliation Service/ops CLI | 缺失 | — | C2/C4/C5/C7 实现，provider 证据门禁 |
| NATS v1 Binding | 缺失 | — | C3 实现 |
| TaskSupervisor/Progress | partial primitive | `runtime/agent.py`、`runtime/executor.py` | 已有进程跟踪、stream/cancel/process-tree 原语；缺独立 heartbeat/lease/State authority，C4 完成 |
| A2A JSON-RPC/SSE Gateway | 缺失 | — | C5 实现 |
| A2A gRPC Gateway | 缺失 | — | C6 INTEROP 实现，共用 Core/语义套件 |
| MCP Client/Server Bridge | Client 与有限 Server Bridge 原型 | `tools/`、`mcp_bridge/server.py` | 仍依赖私有对象/内存 dedupe，缺 OAuth、State/Core 集成和黑盒；C6-E 完成 |
| Identity/AuthContext 原语 | 部分原型 | `identity/`、`tests/test_identity.py` | replay 仍为进程内，Credential/Alias 仍非 State 权威；C2/C3/C5 完成 |
| 外部 OAuth AS 集成/JWKS | 缺失 | — | C6/C7 实现；A2AMesh 不签发 Token |
| Push Dispatcher/Observer | 缺失 | — | C6 实现 |
| 生产监控/备份/真机门禁 | 缺失 | — | C6～C8 |
| Durable dispatch/多 Relay claim | 缺失 | — | C2/C3；提交成功后必须最终 dispatch 或确定失败 |
| Planner/Plan 原语 | partial primitive | `orchestrator/` | 有进程内 Plan/Tracker 基础，缺 canonical DATA-PLAN 持久恢复 |
| Auth replay | partial primitive | `identity/auth_context.py` | 仅进程内 `_seen`，不满足多实例；C2/C3 完成 |
| workspace fencing/Merge Broker | absent | — | Redis lease 不能 fence 文件系统；C2/C4 实现 |
| Recovery Manifest | absent | — | C2/C2.5/C3/C7 实现并演练 |
| 独立 append-only Audit Sink | 缺失 | — | C2.5/C5/C7 |

### 2.3 总体实施结论

当前版本只能称为：

> private A2A-inspired NATS prototype

兼容声明按累积交付剖面门禁：完成 canonical core、State/outbox、NATS Binding、长任务、JSON-RPC/SSE、C7/C8 CORE 和官方黑盒后，只能声明 `CORE / A2A v1 JSON-RPC compatible`。INTEROP 还需 gRPC/Push 与 C7/C8 INTEROP；EXTENDED 必须同时满足 INTEROP 和 MCP/Observer/OAuth 的 C7/C8 EXTENDED。NATS 始终只以自定义 Binding URI 声明。

---

## 3. 建设范围

### 3.1 CORE

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
11. 私有 Object Store、Artifact upload/finalize/download/delete、完整性、孤儿清理和一致恢复；未启用时仅允许 inline 上限。
12. 签名配置 bundle、validate/stage/activate、单 active generation、回滚/撤销、组件 READY 和 Card publisher lease/fencing。
13. UNKNOWN reconciliation case、evidence、claim lease、APPLIED/FAILED/COMPENSATED resolution、SLA 和终态 Task 不可改写。

### 3.2 INTEROP

1. 官方 gRPC Binding 及与 JSON-RPC 共用的 11 操作语义套件。
2. A2A Push Dispatcher、SSRF/签名/重试/DLQ。
3. 额外 Runtime Adapter 与 Linux + 2 Windows 全拓扑真机矩阵。

### 3.3 EXTENDED

1. MCP 2026-07-28 Client（stdio/Streamable HTTP）和 Server Bridge（tools/resources）。
2. 外部 OAuth AS/JWKS、`mesh_submit_task.messageId` 强制幂等和 canonical payload hash。
3. Observer 规则聚合、只读分析和受控干预。

### 3.4 V1 明确不实现

- tenant、RBAC、用户/组织权限；
- 通用后台 UI；
- Redis Cluster/跨区域 HA；
- 任意 shell 公开 Skill；
- 自动重试未知副作用任务；
- 原始 Chain-of-Thought；
- 多 Mesh 联邦。

### 3.5 剖面门禁摘要

| 能力 | CORE | INTEROP | EXTENDED |
|---|---|---|---|
| 标准入口 | JSON-RPC/SSE | + gRPC/Push | + MCP Bridge |
| Runtime | 至少 1 个固定版本 | 额外 Adapter | 不新增强制 Runtime |
| 真机 | Linux + 1 NAT Peer | Linux + 2 Windows | 在 INTEROP 拓扑上增加 MCP/OAuth/Observer |
| 声明 | 只声明已通过 JSON-RPC interface | 才可追加 gRPC/Push | MCP 单独发现，不写入 A2A supportedInterfaces |
| 运维闭环 | signed config + Artifact policy + UNKNOWN case 必须通过 | 复用 CORE | 复用 CORE |

---

## 4. 目标工程结构

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
│       ├── dispatch.lua
│       ├── outbox_claim.lua
│       ├── auth_replay.lua
│       ├── plan.lua
│       ├── workspace_lease.lua
│       ├── recovery_manifest.lua
│       ├── authorize_capability.lua
│       ├── admission.lua
│       ├── artifact.lua
│       ├── config_generation.lua
│       └── reconciliation.lua
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
├── artifact/
│   ├── service.py
│   ├── object_store.py
│   ├── signing.py
│   ├── integrity.py
│   └── reaper.py
├── config/
│   ├── controller.py
│   ├── bundle.py
│   ├── verifier.py
│   ├── readiness.py
│   └── publisher_lease.py
├── reconciliation/
│   ├── service.py
│   ├── evidence.py
│   ├── claims.py
│   ├── providers.py
│   └── cli.py
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
│   ├── audit.py
│   └── audit_relay.py
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

## 5. 技术依赖

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

## 6. 实施原则

1. 每阶段先写失败测试，再实现最小代码。
2. 每个 Binding 共享 Application Core 语义测试。
3. current vs target 文档同步更新。
4. 迁移期间双协议必须显式开关，不能自动猜版本。
5. 所有副作用重试必须先完成端到端幂等。
6. 每个阶段有退出门禁，未通过不得开始兼容宣传。
7. 真机配置和 secret 不提交 Git。
8. 每阶段提交粒度小、可回滚；文档/Schema/测试同提交。
9. callerPrincipal 只由可信入口注入；任何业务 payload 自报身份必须失败。
10. 入口顺序固定为结构/版本/tenant → Credential/AuthProof → Principal/alias → ownership/capability → admission → claim/mutation，不可调整。
11. Credential/Alias/Grant/Card/Profile/Artifact/Runtime/Tool policy 必须来自同一 active signed generation，不能分散读未签名配置。
12. 大型 Artifact finalize、UNKNOWN resolution 和配置激活都必须经 State 原子 mutation + outbox，外部组件不得直接声明权威事实。
13. Task 受理必须同原子提交 durable dispatch intent；NATS publish 不是受理事务的一部分，但由 Worker 最终重投或确定失败。
14. Cancel control、Task event、audit 和对象存储分别采用“Redis 事实 + 可重放 relay/saga”，不能把一次 fire-and-forget publish 当正确性依据。
15. 跨 Redis/JetStream/Object Store/config/audit 的恢复只能由共同 Recovery Manifest 判定成功。

---

## 7. C0：基线冻结与协议准备

### 7.1 目标

冻结当前行为、引入官方 fixture、建立状态标签和 CI，不改生产协议。

### 7.2 文件

创建：

```text
src/a2amesh/conformance/official_fixtures.py
src/a2amesh/conformance/fixtures/a2a_v1/
tests/conformance/test_official_types.py
tests/conformance/test_compatibility_claims.py
tests/conformance/test_wheel_resources.py
scripts/verify_a2a_fixtures.py
scripts/verify_wheel_resources.py
scripts/run_ci.py
```

修改：

```text
pyproject.toml
uv.lock
README.md
docs/specs/
```

### 7.3 任务

1. 锁定 A2A Spec v1.0.1 和 SDK 1.1.2。
2. 从官方 SDK 生成 Card/Message/Task/Artifact/Event/Error fixture。
3. 测试官方对象 parse/serialize。
4. README 标注当前为 prototype。
5. 建立 `current/target` 能力矩阵。
6. 运行当前测试并保存机器可读 CI 报告；不在文档中手工固化易漂移的通过数。
7. CI 增加 `pytest`、`ruff`、`compileall`、文档链接检查。
8. 增加非空 tenant、三类 Credential、AuthContext 和 MCP messageId fixture。
9. 官方 fixture 以 package resource 作为唯一权威源；CI 无依赖、非 editable 安装 wheel 后读取并验证全部资源。

### 7.4 退出门禁

- 官方 fixture 全部可解析；
- 当前旧 fixture 与 v1 fixture 明确分开；
- CI 安装 wheel 后仍能读取 Schema/fixture；
- README 无虚假兼容声明。

---

## 8. C1：Canonical A2A Application Core

### 8.1 目标

用官方对象和传输无关接口实现 11 操作、错误和状态机。

### 8.2 文件

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

### 8.3 任务

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

### 8.4 C1-1 当前模块：可信 Context 与首个 Unary Contract

截至 2026-08-17 11:32（Asia/Shanghai），已形成一个可独立回滚的 C1-1 实现候选：

- `core/application.py` 保留动态 protobuf SDK 的运行时精确校验，并提供 `CanonicalRequestContext`、`CanonicalApplication` 和 `dispatch_unary`；
- `CanonicalRequestContext` 只接受已验证 `Principal`、安全 `request_id`、目标 Agent 和正的 config generation，不持有 Token 或 Binding metadata；
- `protocol/application.py` 是对外稳定 facade，`protocol/errors.py` 统一暴露官方 A2A error 类型，禁止按 HTTP/NATS 重新定义 Core 错误；
- NATS v1 verified identity 转换为 `Principal` 后才进入 Core；unary path 复用统一 dispatcher，响应类型错误映射为官方 `InvalidAgentResponseError`；
- C1-1 首个纵向操作为 `GetTask` 的 `GetTaskRequest → Task` contract，另覆盖 request type、非空 tenant、streaming 误用和错误 response 的负例；
- 实测：C1 新增/受影响测试 `38 passed, 1 skipped`；此前全量候选测试 `370 passed, 8 skipped`；Ruff、compileall、diff-check 通过；
- 本模块不实现 Redis State、Task 状态机、11 操作业务语义或真实 NATS/JetStream 故障验收；因此不满足 C1 整体退出门禁，也不代表完整 A2A v1 兼容或生产就绪；
- C1-1 checkpoint 和独立只读复审完成前，状态保持 `candidate/pending-review`，不得写 `[verified]`。

### 8.5 C1-2 当前模块：官方 TaskState 纯状态合同

截至 2026-08-17 11:54（Asia/Shanghai），C1-2 已形成独立可回滚实现候选：

- `protocol/state_machine.py` 只依赖官方 `TaskState`，定义不可变的官方迁移表、四类终态集合、终态保护和 fail-closed 非法/`UNSPECIFIED` 校验；
- 迁移表严格对应 §6.2：`SUBMITTED`、`WORKING`、`INPUT_REQUIRED`、`AUTH_REQUIRED` 的合法去向，终态无后继；不允许隐式 self-transition；
- `protocol/__init__.py` 暴露同一 contract facade；NATS stream 删除重复 `_TERMINAL_STATES`，统一调用该唯一实现；
- RED→GREEN证据：状态机专项 `37 passed`；fresh interpreter `import a2amesh.protocol` 通过；Ruff、compileall、diff-check 通过；
- 本模块不实现 version/lease/fencing、Redis CAS、outbox、Task/Context持久化或真实State并发；这些属于C2，不能由本模块宣称已完成；
- C1-2 在独立复审和提交后全量门禁完成前保持 `candidate/pending-review`，不得写 `[verified]`。

### 8.6 退出门禁（C1 整体）

- 11 方法接口存在且有 contract test；
- 官方 SDK 可解析所有输入/输出；
- 非法终态回退、Task/Context 不匹配被拒绝；
- Card heartbeat 不改 ETag；
- 扩展被标准客户端忽略后对象仍合法。

---

## 9. C2：Redis State Service

### 9.1 目标

替换单进程 Task/Card 状态，建立 Principal/Credential/Alias、共享幂等、lease、outbox、side-effect ledger、capability grant、admission、Artifact metadata、signed config generation、reconciliation case、List 和恢复基础。

### 9.2 文件

创建 `state/` 目录、Lua、integration tests、Docker test fixture。

### 9.3 顺序

1. Redis config 和 async client。
2. Key builder（mesh_id、安全编码）。
3. `claim_auth_request` + `claim_message.lua`；原子创建 dedupe/immutable command/SUBMITTED Task/QUEUED admission/event outbox/BLOCKED_ADMISSION dispatch，Task ID 由 State 生成。
4. `transition_task.lua` 由 State 严格分配 `eventSeq=current+1`，完成状态/索引/outbox head 测试。
5. lease/fencing + 双实例测试。
6. Card/presence/index。
7. Get/List/cursor。
8. Push config/delivery state。
9. 传输无关 State Service application interface 与 in-process/Redis integration test；不在 C2 实现 NATS handler。
10. retention cleaner/backup metrics。
11. `resolve_principal.lua`、Credential disable/rotation、不可改指 alias CAS/环测试。
12. Task 固化 callerPrincipal/credentialId/aliasGeneration，历史 owner 不随配置变化。
13. mutation 原子写 Task/索引/outbox；完整 PENDING/CLAIMED/PUBLISHED/DEAD、per-Task watermark/claim 字段、唯一 dead recovery，多 Relay head-of-line。
14. 独立 effect-intent/effect-attempt Key 与唯一 CAS；prepare/begin/start/complete、仅 NOT_APPLIED 可新 attempt、陈旧 APPLYING→UNKNOWN 与唯一 case。
15. capability grant generation/expiry 和全维 fail-closed 匹配。
16. 全局/Principal queued/reserved/running、`reserved+running<=maxRunning`、FIFO/deficit/weight/round/cursor 持久 DRR；确定性 visit/cursor 规则、QUEUED→SELECTED→RUNNING 与 queue/dispatch deadline。
17. Artifact metadata、owner tombstone、source-centric typed source commit与双向ref index（无独立add/remove ref）、create/renew/release hold、retention lock及完整finalize/delete/Reaper CAS；实现hold expiry持久due/SCAN candidate authority、immutable commit、`REPLAY_CLAIM` exact operation ledger/current-authority pointer和全局fence，绑定`authorizedCandidateDigest/baseCommitDigest`并支持Redis重启后的exact replay。
18. 不可变 config bundle、components[] READY 全字段、WORM GenesisIntent/CommitReceipt 唯一提交点与 PREPARED/COMMITTED 恢复 saga、generation index root、active pointer-only CAS、publisher fencing。
19. UNKNOWN 唯一 case；immutable ResolutionRecord/history/idempotency、claim、resolve/close/reopen 原子矩阵。
20. immutable `DispatchTask` command、BLOCKED/PENDING/CLAIMED/SENT/ACCEPTED/ABORTED/DEAD、`mark_dispatch_sent/reclaim_expired_dispatch/accept_dispatch_and_start`、单调 attempt/token 和 sweeper。
21. 通用 AuthProof replay/wire 脱敏映射；完整 canonical Plan/Step/root-child 与 plan acquire/renew/recover fencing；workspace lease 仅供 Merge Broker。
22. cancelRequested、纯 heartbeat freshnessVersion、pre-accept provisional lease 撤销与 SUBMITTED cancel/accept race CAS。
23. `append_task_message/input intent/ack_input_and_resume`、`claim_recovery_attempt` 和五类 deadline。
24. DATA-AUDIT `AuditEnvelopeV1`、多来源 outbox/WAL、exact Segment JWS/WORM receipt/checkpoint；DATA-RECOVERY exact Manifest/transition receipt JWS、delete journal、双人 approval/release和 Redis 全损重建。

### 9.4 测试

```text
100 并发同 messageId → 1 Task
同 messageId 不同 payload → conflict
双 owner → 1 lease
旧 token late write → reject
List cursor → 无重/漏
Redis restart → Task/Card/dedupe 恢复
Credential rotation/disable → 新请求切换或拒绝，历史 owner 不变
alias chain/loop/retarget → reject
Task mutation 中途失败 → Task/索引/outbox/dispatch intent 全部不提交（脚本先校验后写，不依赖异常 rollback）
effect timeout/断线 → UNKNOWN，禁止自动重试/取消成功
grant 任一维度不匹配 → claim/queue 前拒绝
Principal/global queue → 有界、公平、计数可回收
Artifact finalize 中途失败 → blob/metadata/Task/outbox 不出现悬空成功
Artifact hold SCAN/EXPIRE提交前后或丢响应 → due/candidate tombstone/commit/audit/outbox同CAS且exact replay
REPLAY_CLAIM未过期时不同ID → reject且high-water不推进；过期后新ID只推进一次；Redis重启后current pointer与exact result不丢失
config activate 并发/READY 超时 → 单 active generation 或完整回滚
UNKNOWN 重复回调/并发 claim → 单 case、单 claimant、旧 fencing 拒绝
Task 已返回后 kill Dispatcher → intent 被接管并 ACCEPTED 或在 deadline 确定 FAILED
多 Relay + event n 退避 → n+1 不越过 n
AuthProof 在另一 State 实例重放 → reject
Protected Local IPC 同ipcRequestId同digest在Core重启后逐字节复用exact response；异digest、journal坏尾、IN_FLIGHT重复执行和5秒过期均拒绝/不重副作用
Config/GateEvidence General JWS signatures[] → kid UTF-8严格升序、唯一、threshold满足；重复/反序/额外字段/unprotected header均拒绝
陈旧 APPLYING + owner lease 失效 → 单 UNKNOWN/单 case
公网/Windows → 6379 不可达
```

### 9.5 退出门禁

所有状态 API 只经 State Service；新任务不再写进程 `_tasks` 权威字典；Task/Artifact/config/reconciliation mutation 与 outbox 原子。claim 同时创建 dispatch intent；cancel 为持久事实；多 Relay、replay、Plan、workspace、effect 和恢复 manifest 有并发测试。UNKNOWN effect、授权失败、准入失败、generation 漂移和 State 故障全部 fail closed。

---

## 10. C2.5：最小控制面垂直切片

### 10.1 目标

在 Runtime/C4 之前建立可执行的最小 Config、Artifact、Reconciliation、Audit 和 Recovery 控制面，消除“C4 门禁依赖 C5/C7 才存在的服务”的反向依赖。C2.5 只提供 CORE 所需最小闭环，完整公网 API、部署硬化和灾备演练仍在 C5/C7。

### 10.2 任务

1. Config Controller genesis crash-safe saga、GenesisIntent/CommitReceipt exact JWS fixture、每个WORM/主机/Redis crash point、RFC8785 config hash/JWS、validate/stage/可信READY、stable component publisher identity；实现DATA-GATE-EVIDENCE-001独立JWS、Config/GateEvidence `signatures[]` kid排序/threshold/负例、确定性aclDigest/readySetDigest、不可变报告校验、唯一gate-evidence-stage API/CLI、显式evidenceSha256 activation，以及rollout lease/maintenance traffic gate/candidate ACL exact-byte promote与失败恢复状态机，禁止报告回写bundle。
2. 开发态私有Object Store fixture、Artifact Broker create/finalize/ticket/delete、hold create/renew/release、source-centric typed source commit、服务端checksum和Reaper竞态CAS；唯一HTTP path按source tuple，唯一NATS subject为`artifact.source.commit`，完整五字段refs一次可触及多个Artifact。Reaper垂直切片必须实现closed `SCAN|EXPIRE|REPLAY_CLAIM`真实Redis writer：持久due/scan/candidate/authority/high-water、immutable commit、claim exact request/result ledger与每个expire operation的current pointer；严格绑定`authorizedCandidateDigest/baseCommitDigest`、candidate lease duration上限`300000ms`、未过期claim排他、过期后更高fence接管、commit-before-reply及Redis重启exact replay，并通过`TEST-ARTIFACT-HOLD-EXPIRY-001`和`TEST-ARTIFACT-HOLD-REPLAY-001`。
3. Reconciliation Service 的 OPEN/RESOLVED/CLOSED、独立claim/escalation、Evidence、resolutionHistory和stale APPLYING scanner；实现`recon.claim` closed ACQUIRE/RENEW/RELEASE/EXPIRE/ESCALATE、persistent claim/SLA due、scanner lease/fence/candidate exact replay、server-time逻辑过期及API claim/renew/release。
4. Audit Relay 到本地不可变 append-only fixture，验证 State/non-State→AuditEnvelopeV1→outbox/WAL→exact Segment JWS→WORM receipt→ack，以及普通/轮换签名阈值；接口保持可替换为生产 WORM sink。
5. 实现 DATA-RECOVERY-001 的canonical payload、非detached General JWS、Manifest/summary DAG/`indexRootDigest`、ArchiveTransition/Verification/Restore/Approval/Release receipts、checkpoint-source interface、delete journal、双人release和外部证据重建；实现独立Recovery Compactor、persistent due/scan/source lease/fence和archive写入→exact read-back→transition receipt→新summary/Manifest→独立递归验证→State hot-index CAS compaction，以及`TEST-DR-MANIFEST-DAG-001`/`TEST-DR-COMPACTION-001`全部crash/replay门禁。本阶段接入Redis/Object/config/audit与确定性JetStream fixture，C3提供真实JetStream source。
6. 最小ops CLI/API仅绑定测试/管理网络，使用独立机器Credential和细分capability；`ops.config.evidence.stage`与`ops.config.activate`分离，Idempotency-Key同body重入、异body冲突，CLI不得直写State或自选deployed digest/rollout lease。

### 10.3 退出门禁

- g1只能通过WORM commit唯一线性化；各PREPARED/COMMITTED crash point同digest可恢复、异digest/伪造marker拒绝，READY伪造/重放/过期均拒绝；TEST-CONFIG-GATE-EVIDENCE-001证明bundle→报告→evidence单向hash依赖、唯一API/CLI可达、报告/ACL/READY漂移拒绝且activate绑定evidenceSha256；TEST-CONFIG-ROLLOUT-001证明STAGED后才render/隔离测试、生产promote始终在维护门内、CAS失败恢复旧ACL或保持fail-closed；
- Artifact finalize/terminal/delete/ticket/hold/ref/Reaper 并发不产生悬空引用、计数泄漏或复活对象；`TEST-ARTIFACT-HOLD-EXPIRY-001`与`TEST-ARTIFACT-HOLD-REPLAY-001`纯状态合同测试已通过并覆盖claim ledger/current pointer、strict wire、cross-record digest与逻辑lease；真实Redis Function、持久化/重启exact replay仍待C2/C3实现与验收，不把纯状态合同冒充真实集成；
- stale APPLYING 自动形成唯一 UNKNOWN case，case 可 claim/evidence/resolve/close/reopen，ResolutionRecord/history 不可变；
- State 与五类非 State 来源 Audit 重复投递幂等，global sequence、exact JWS、sealed segment、跨日 hash、轮换双签、WORM receipt 与 pseudonym 投影可验证；
- Recovery Manifest双签、summary DAG/indexRoot、archive transition、Verification/Restore/双approval/Release exact JWS可递归验证；缺任一enabled source、summary node、archive exact bytes、receipt或水位不能宣布恢复，清空Redis后可只凭外部证据重建；C3完成后JetStream变为CORE必选source；Protected Local IPC journal在Core重启边界同digest逐字节复用、异digest/IN_FLIGHT不重执行；Config/GateEvidence多签kid排序与threshold负例通过；
- C4 可只依赖 C1/C2/C2.5/C3 完成长任务、副作用和 Artifact 门禁。

---

## 11. C3：NATS v1 Binding

### 11.1 目标

实现版本化 Subject/Envelope、11 操作映射、私有 reply、durable Dispatch Worker，以及 Redis outbox 经 Event Relay 发布到 JetStream 的唯一事件路径。

### 11.2 文件

```text
src/a2amesh/bindings/nats_v1.py
src/a2amesh/bindings/envelope.py
src/a2amesh/bindings/subjects.py
src/a2amesh/streaming/session_controller.py
src/a2amesh/streaming/js_provisioner.py
src/a2amesh/streaming/wire.py
src/a2amesh/state/stream_session.py
tests/integration/test_nats_v1_*.py
tests/integration/test_nats_acl.py
tests/integration/test_nats_stream_session.py
nats/config/*.conf
```

### 11.3 任务

1. Subject 安全构造和 ACL fixture。
2. 官方 11 操作 Envelope 与独立内部 `DispatchTask`/`TaskEventEnvelope` schema、BindingCapabilities parse。
3. unary request/reply。
4. NATS 流式操作的 request inbox 只接收一次 `StreamSessionOpenedV1`；后续帧只走 caller 私有 `_DELIVER.a2amesh.stream.<scope>.<instanceId>.<streamOpenId>`，禁止多 response/inbox 流。
5. 目标Agent的Task Supervisor以独立NKey/queue group直接消费dispatch；先调用`task.command.get`核验immutable command，再调用`acquire_lease/accept_dispatch_and_start`，Peer Binding不订阅dispatch，重复投递不产生第二执行。
6. Dispatch Worker claim intent、`mark_dispatch_sent`、NATS publish、accept/reclaim/reschedule/dead；在 CLAIMED、SENT/publish 前和 publish/reply 前崩溃均可由更高 attempt/token 接管。
7. Event Relay claim due head outbox，以 `taskId:eventSeq` 发布并等待 PubAck；CLAIMED 过期由 `reclaim_expired_outbox` 接管，旧 token 拒绝；同 Task `n+1` 不越过未完成 `n`，消费者仍去重。
8. Card upsert/presence/state RPC；公开 Card 不发布私有 NATS route，内部 Registry metadata 才提供 Binding URI/minor。
9. State Service NATS handler：连接认证身份覆盖 payload，所有 mutation/query 复用 C2 application interface。
10. 11 操作语义套件复用 C1 tests。
11. transport retry 换 requestId/AuthProof、稳定 messageId/dispatchId；同 requestId 重放固定拒绝。
12. 逐身份ACL fixture覆盖全部89个`STATE_REQUEST_SUBJECTS_V1` literal、control、private inbox、`$JS.API`/delivery subject；Peer只可publish获授权RPC/Registry而不能Task mutation/events/他人inbox，Application Core独占task claim/get(GET|LIST)/cancel/append、push.config/stream.flush，Task Supervisor独占dispatch订阅与command.get/recover/heartbeat，State内置Admission Scheduler只经持久scheduler lease运行DRR，Artifact Hold Reaper独占artifact.hold.expire closed `SCAN|EXPIRE|REPLAY_CLAIM`且无Object Store凭据，higher-fence replay必须先持久化绑定`baseCommitDigest`的exact claim与current-authority pointer，未过期current claim禁止新ID，Artifact Adapter只可提交artifact.delete `REQUEST`，独立Artifact Delete Worker只可提交同subject `COMPLETE`并持有provider删除凭据，三者Principal/NKey selector两两不同；Reconciliation Service独占effect.scan-stale及recon.claim/recon.scan-due，Orchestrator独占plan.recovery.scan/Plan mutation，Recovery Compactor独占recovery.compact closed union，Ops Recovery独占outbox.recover，Config Controller独占stream-config.begin，只有JS Provisioner可调用broker-op及stream-config claim/complete，只有Config Controller可stage GateEvidenceRecord；ACL生成器向每个signed components[]同一NKey叠加且仅叠加config.ready。
13. RFC 8785 canonical Envelope + NKey Ed25519 AuthContext 签名/验证，replySubject 纳入签名且限 caller 私有 prefix。
14. signer/method/subject/expiry/requestId/deadline/Redis replay 校验；payload caller 字段不可信。
15. `bindingSchemaVersion` 与 `a2aProtocolVersion` 分离，major reject、minor N/N-1 fixture。
16. 实现真实JetStream checkpoint source并接入C2.5 Recovery Manifest summary DAG/indexRoot与archive transition；实现独立Recovery Compactor及`recovery.compact` closed union、persistent due/scan/source lease/fence/transition ledger和archive/receipt/summary/verify/hot-delete逐阶段CAS。Redis/JetStream水位、旧fence、summary range/count或archive receipt不一致时恢复门禁拒绝。
17. SendStreaming/Subscribe 都使用 consumer-first→读 snapshot/eventSeq；Gateway/SSE 路径按自身 consumer ACK 后抑制 `<=` 重复，NATS Stream Session 则必须经 State covered watermark/permit逐条 broker ACK，禁止无 ACK 内存丢弃；任何路径都禁止把 per-Task eventSeq 当 stream sequence。
18. NATS流式操作只回一个`StreamSessionOpenedV1`；实现DATA-STREAM-SESSION-001、Stream Session Controller与独立JS Provisioner。Session持久绑定configGeneration、canonical consumer config/digest、initialFrame/openedResponse bytes；consumer禁用inactive自动删除，使用稳定mesh-scoped controller delivery。所有Create/Info/Delete必须走仅含`brokerOpRequestDigest`的State签名BrokerOperationTicket和per-epoch持久`begin→Provisioner claim/complete→Controller consume`执行账本；每次side-effect claim单调推进session全历史apply上界，close/expire与claim同CAS并等待`brokerOpQuiesceUntilMs`后DELETE，以覆盖current pointer被新epoch替换前所有旧execution lease内迟到CREATE；snapshot-covered event逐条broker ACK，final走caller ACK→challenge INFO确认→delete/challenge not-found，deadline走EXPIRING→EXPIRED。Peer/Gateway无`$JS.API.*`/`$JS.ACK.*`；用`TEST-NATS-STREAM-SESSION-001`注入generation切换、Task后续变化、全Controller离线、旧INFO重放、旧CREATE e1 claim→e2覆盖→close竞态、ticket/claim/API/complete/consume、final/close/expire/delete各崩溃窗及跨Controller/Provisioner接管。

### 11.4 退出门禁

- 两 Peer 并发处理同一规范化请求时只创建或返回同一个 Task，旧 owner 不能推进权威状态；
- 非授权 Peer 无法订阅他人回复/Task event；
- Core 与 NATS Binding 语义等价；
- NATS 重启/重连不重复终态。
- Peer/Gateway/MCP Bridge 均不能伪造他人 Principal，过期/重放 AuthContext 在 claim 前拒绝。
- Relay 在 publish 前后崩溃不丢事件；Projector 重放只更新派生视图，不覆盖新快照/终态。
- major mismatch fail closed，N/N-1 minor fixture 通过。
- Task claim 后 Core/Dispatcher/NATS 任一故障都由 durable intent 最终 ACCEPTED 或在 deadline 确定 FAILED；不存在永久 SUBMITTED 黑洞。
- Cancel control 丢失时 Supervisor heartbeat/接管仍从 Redis 发现 cancelRequested。
- Recovery Manifest 使用真实 JetStream stream/consumer checkpoint，缺失或与 Redis outbox 水位不一致时 fail closed。
- `TEST-NATS-ACL-001`与`TEST-NATS-STREAM-SESSION-001`是C3真实broker退出条件（当前未验收）：89/89 State subject有且仅有授权角色及READY overlay；Artifact Adapter→artifact.source.commit一次提交多Artifact完整ref集合可达且旧ref增删subject不存在，Adapter只能artifact.delete REQUEST、Artifact Delete Worker只能COMPLETE、Artifact Hold Reaper只能hold.expire SCAN|EXPIRE|REPLAY_CLAIM且claim必须持久化新authority；`TEST-ARTIFACT-HOLD-REPLAY-001`覆盖同ID幂等、过期接管、higher fence裸整数拒绝和snapshot evidence交叉绑定，三组件不同Principal/NKey selector且Reaper无provider delete credential；Peer→本地Application Core→State、Dispatch Worker→Task Supervisor→command.get/lease/accept、Reconciliation Service→claim/scan-due及Recovery Compactor→compact均可达且越权反例拒绝；单open reply及历史响应逐字节稳定；snapshot覆盖窗口不死锁；无inactive自动删除；跨Controller/Provisioner稳定接管；旧签名not-found/已消费ticket无法重放；final/expired清理无早终态、重复broker副作用或consumer泄漏；`TEST-IPC-REPLAY-001`证明Core journal重启边界不重复执行。

---

## 12. C4：Runtime 与长任务 Supervisor

### 12.1 目标

依赖 C1/C2/C2.5/C3，让静默长任务可观察、可取消、可恢复，完成 Progress、SideEffectAdapter、ArtifactClient、持久 Plan、公平准入、UNKNOWN case 创建和派生 Projector。

### 12.2 文件

```text
runtime/supervisor.py
runtime/progress.py
runtime/workspace.py
state/projector.py
state/event_relay.py
artifact/client.py
reconciliation/providers.py
observer/rules.py
tests/unit/runtime/test_supervisor.py
tests/integration/test_long_task_*.py
```

### 12.3 任务

1. 拆分 Executor 与 Supervisor。
2. 独立 heartbeat/lease/cancel/event 协程。
3. Linux process group + Windows Job Object/进程树。
4. RuntimeEvent contract、一个 CORE 基线 Adapter 的固定版本 probe/fixture/真机测试，以及可扩展 Adapter 接口。
5. Progress Extension 映射。
6. State mutation/outbox event sequence/attempt。
7. Projector 只维护派生视图，不合并覆盖 Redis 权威 Task。
8. 所有写 attempt 强制私有 worktree；Linux openat2/Windows handle-relative 路径防逃逸；共享根仅 Merge Broker 同时校验 workspaceFencingToken/baseRevision/expectedDiffDigest/activeGeneration/policySnapshotHash 后提交。
9. CapabilityPolicy + authorize_capability。
10. 有界公平 admission、固定 Deficit Round Robin、queue deadline、请求/Artifact/context 大小。
11. 实现签名 ContainmentProfile、Linux namespace/seccomp/cgroup/egress 与 Windows restricted token/AppContainer/Job/ACL/firewall 等价控制、launch attestation；不可用即拒绝 MEDIATED。
12. SideEffectAdapter 按 `prepare request→prepare intent/begin attempt→lease/fence→start APPLYING→apply→complete`，仅 NOT_APPLIED 可新 attempt。
13. ExecutionPlan/Step/root-child 映射经 State 持久化；Plan HASH 还必须持久化 `recoveryState/recoveryEpoch/recoveryRevision/recoveryCursorStepId`。恢复按 `recover_plan_lease` begin/takeover→逐 Step 原子 reconcile+cursor→`finalize_plan_recovery` 执行；RECONCILING 中除 renew/recovery 系列外所有 Plan/child/workspace/effect 派生写拒绝。
14. Observer deterministic rules（EXTENDED 前暂不调用 LLM）。
15. 大型 Runtime 输出使用 upload/finalize；未 AVAILABLE 不得成功终态或写本地路径 URI。
16. UNKNOWN 原子创建 case；Runtime/Adapter 只能采集证据，不能用业务 Credential 自行 resolve。

### 12.4 门禁

- 60 秒无 stdout 仍有 heartbeat；
- 无换行输出不阻塞 cancel；
- lease lost 后不再副作用；
- unsafe 崩溃不自动重跑；
- UNKNOWN effect 未对账前不重试，取消返回 FAILED + reconciliation_required；
- case 可 claim/evidence/resolve，且事后 resolution 不改写已失败 Task 标准终态；
- 大型 Artifact 完整性验证后才附加 Task；Object Store 故障不产生虚假成功结果；
- Principal/全局队列有界且公平，429 与 503 语义区分；
- 多订阅者事件顺序一致；
- 目标 CORE Runtime 在其支持平台能杀完整子进程树；其他 Adapter/平台进入 INTEROP 独立门禁。
- DRR 两轮饥饿上限、Plan 持久 recovery gate（begin/每 Step/cursor/finalize 全写点故障注入）、workspace fencing 和 UNMEDIATED 高风险拒绝通过；`TEST-PLAN-RECOVERY-001` 必须证明跨两次 owner 崩溃仍从同一 epoch/cursor继续且不重复 child。

---

## 13. C5：CORE 标准 A2A JSON-RPC/SSE Gateway

### 13.1 目标

用官方 SDK/Proto 暴露 well-known Card、JSON-RPC、SSE 和 A2A service parameters/header，完成 `CORE` 兼容门禁。

### 13.2 文件

```text
gateway/app.py
gateway/routes.py
gateway/sse.py
gateway/auth.py
bindings/jsonrpc_http.py
tests/conformance/test_official_client_*.py
```

### 13.3 任务

1. 官方 Server Adapter 最小启动。
2. `https://<agentId>.agents.<baseDomain>/.well-known/agent-card.json`、Host 校验、ETag/304。
3. JSON-RPC 11 方法接 Core；CORE 六个 Task 操作走成功路径，Push/Extended 未启用时返回精确标准不支持错误。
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
18. Artifact upload/completion/download-ticket/delete及source-centric typed source commit API的ownership、path/body绑定、五字段完整refs、old∪new expected versions、幂等、409/413/423/503和signed URL脱敏；不存在target-centric ref增删路由。
19. Config ops API/CLI 的 validate/stage/activate/rollback/revoke、独立 Credential、generation CAS 和启动 fail closed。
20. Reconciliation ops API/CLI 的list/show/claim/claim-renew/claim-release/evidence/resolve/close/reopen、五操作closed wire、revision/双层fencing/idempotency、persistent due和SLA；EXPIRE/ESCALATE仅由受信scanner触发，无公共HTTP入口。
21. Card publisher 候选从 active bundle 读取，Redis lease/fencing 决定唯一 publisher；Profile 只在组件 READY 与兼容门禁通过后广告。
22. 公开 Card 只发布已验收标准 interface，不发布 NATS route/NKey；内部 Registry Card 需已认证 Peer。
23. Subscribe 固定 live-consumer-first/buffer→snapshot/watermark→dedupe，V1.6 不交付私有 replay cursor。
24. Artifact/Config/Reconciliation/Audit 复用 C2.5 服务，C5 负责公网/运维适配与完整 CORE 语义，不重新建立第二套状态。

### 13.4 退出门禁

C5 本地退出条件是：官方 JSON-RPC client 在 CI/隔离集成环境执行 11 个操作；Card fixture 只声明已通过的 JSON-RPC interface；signed config、Artifact policy/Object Store（启用大对象时）、UNKNOWN reconciliation、ops 权限和 CORE Metrics/Audit/Health 均通过 C0～C5（含 C2.5）的本地实现/故障注入门禁。C5 完成**不授权**声明 `CORE compatible`，也不要求尚未执行的 C7/C8 真机/发布证据；任何 CORE 对外声明统一等待 §16.2 发布门禁、C7-CORE/C8-CORE、Linux + 1 NAT Peer 真机和官方黑盒全部通过。不得提前声明 gRPC、Push、MCP 或 Observer。

---

## 14. C6-I / C6-E：可并行实现工作流与累积剖面

C6 拆成两个可并行开发的工作流和独立证据集；实现顺序可独立，但**剖面声明严格累积**：`EXTENDED ⊃ INTEROP ⊃ CORE`。C6-E 完成不等于可跳过 C6-I 宣称 EXTENDED，任一工作流也不能借另一阶段结果宣传。

### 14.1 C6-I（INTEROP）任务

1. gRPC `A2AService` 11 RPC、两个 server-streaming、metadata/deadline/cancel。
2. JSON-RPC/gRPC/NATS 共用 operation fixture；官方 gRPC stub 独立黑盒。
3. gRPC 非空 tenant 返回 `INVALID_ARGUMENT` 且无副作用；通过后 Card 才追加 gRPC interface。
4. Push CRUD、Redis 配置和 Push durable consumer；与 Task Dispatch Worker 分离。
5. HTTPS/SSRF/DNS rebinding/redirect 防护，deliveryId、签名、timeout、退避、DLQ。
6. 其余目标 Runtime Adapter 的固定版本 probe/fixture/真机门禁。
7. INTEROP Metrics/Audit/Trace/Health/Alerts、看板和 P1 Runbook。

### 14.2 C6-I 实现证据门禁

- 官方 gRPC stub 语义门禁通过，Card 只在门禁后发布 gRPC interface；
- Push 慢/失败不阻塞 Task，重复 Webhook 可去重；
- SSRF 测试覆盖 loopback/private/metadata/redirect/rebinding；
- 目标额外 Runtime 的 probe/fixture/真机测试通过；
- 本节通过只表示 INTEROP 功能实现候选完成；仍需 C7-INTEROP 硬化和 C8-INTEROP 三机矩阵才可声明 `INTEROP`。

### 14.3 C6-E（EXTENDED）任务

1. Observer rule aggregation、可选 LLM adapter、干预 policy、cause sequence、冷却和次数限制。
2. Extended Card（若启用）与 JWS 签名；MCP 永不写入 A2A supportedInterfaces。
3. MCP Client：stdio/Streamable HTTP、initialize、tools/resources/prompts、schema/cache/cancel。
4. 把已有 `mcp_bridge/server.py` 原型迁移到官方 Core/State：`/mcp`、tools/resources、A2A Task handle，不再使用进程内 dedupe/私有 Task 对象。
5. MCP OAuth 2.1 Protected Resource Metadata、Origin、audience/resource、旧 HTTP+SSE 拒绝。
6. `mesh_submit_task` JSON Schema 强制 messageId；canonical SendMessageRequest hash；created/same/conflict 套件。
7. 外部 AS：RFC 9728/RFC 8414、client_credentials、issuer/audience/scope/TTL、RS256/ES256、JWKS rotation/outage。
8. OAuth client_id → Canonical Principal；Token 不透传；AS/JWKS 故障 fail closed。
9. EXTENDED Metrics/Audit/Trace/Health/Alerts、看板和 P1 Runbook。

### 14.4 C6-E 实现证据门禁

- Observer 不处理普通 heartbeat、不自触发；
- 日志 secret/思维链扫描通过，Card 签名篡改失败（若声明）；
- MCP stdio/Streamable HTTP 与 OAuth/Origin/Task handle 黑盒通过；Windows 无 MCP 入站；
- MCP 同 messageId 超时/并发重试只产生一个 State Task；冲突 payload 不执行；
- JWKS rotation/outage、未知 kid、错误 audience/scope、Token expiry 全部 fail closed；
- 本节通过只表示 EXTENDED add-on 实现候选完成；只有 CORE + C6-I + C6-E、C7-EXTENDED 硬化和 C8-EXTENDED 真机/黑盒全部通过才可声明 `EXTENDED`。

---

## 15. C7：部署与安全加固

C7 按目标剖面执行，不强迫 CORE 等待可选 C6：发布路径可为 `C5 → C7-CORE → C8-CORE`；INTEROP 为 `C5 → C6-I → C7-INTEROP → C8-INTEROP`；EXTENDED 为 `C5 → C6-I + C6-E → C7-EXTENDED → C8-EXTENDED`。

### 15.1 Linux

- NATS TLS/NKey/ACL/JetStream 持久目录；
- Redis loopback/ACL/AOF/noeviction；
- Gateway HTTPS；HTTP/2/gRPC 与 MCP Streamable HTTP 仅在部署目标包含对应剖面时启用；
- State与Peer节点的Peer Binding/Application Core以独立systemd服务或容器运行；Peer→Core只走受保护本地IPC。Public Gateway的transport adapter与Core library固定同一受信进程/部署单元，以typed in-process interface调用，Gateway NKey零State权限、Core key handle不暴露给adapter；启用执行/编排的节点还以不同NKey运行Task Supervisor与Orchestrator，不能把权限并回Peer/Gateway/Runtime；
- Dispatch Worker、Event Relay与Ops Recovery使用三个独立NKey/服务；Ops Recovery API仅绑定受控管理网络并只可调用`outbox.recover`，Event Relay不能继承该权限；
- 至少两个可接管的Stream Session Controller实例和独立JS Provisioner；分别使用signed `components[]` Principal/NKey，Provisioner是唯一Consumer API身份；
- Config Controller、签名 bundle 只读制品、信任根和受保护本地 cache；
- 私有 Object Store/Artifact Broker、服务端加密、生命周期、inventory 和 Reaper；
- Reconciliation Service及reconciliation/outbox recovery ops API仅绑定受控管理网络，capability与服务NKey分离；
- 独立 append-only/WORM Audit Sink、AuditEnvelopeV1、exact General JWS segment/跨日链/轮换双签、审计读取审计；
- Redis/JetStream/Object Store/config/audit异机加密备份及exact JWS Recovery Manifest/summary DAG/archive transition/Verification/Restore/双approval/Release receipts（恢复点间隔不超过15分钟）；
- firewall 只开放 HTTPS 和 NATS TLS/WSS；
- secret 文件最小权限。
- 配置外部 OAuth AS issuer/resource/JWKS，验证 metadata 与 key rotation；AS 可同机独立容器或外部托管，但不是 A2AMesh 内嵌签发器。

### 15.2 Windows

- 原生 Python/uv 环境或打包产物；
- NATS TLS CA/NKey；
- Runtime executable probe；
- workspace alias；
- Windows Service/Task Scheduler 自启动；
- Credential Manager/ACL；
- 防火墙验证无入站；
- 进程树清理测试。
- Peer Binding、Application Core、Task Supervisor、Orchestrator使用不同NKey/受保护凭据并分别报告READY；Runtime/Tool不持有四者NKey，Peer/Core只走受保护本地IPC。

### 15.3 门禁

- secret 不在 Git/日志；
- Redis/NATS 管理端口公网不可达；
- ACL 含 JetStream 实测；
- 备份恢复演练；
- Recovery Manifest任一source、summary node、archive exact bytes、transition receipt、水位或签名缺失/不一致时业务保持fail closed；删除journal/tombstone与archive transition可覆盖最长备份保留期；外部证据可在Redis全损后递归重建状态和summary index；
- signed bundle 激活/回滚/撤销、过期启动和 publisher split-brain 演练；
- Object Store 禁用/不可用/对象缺失/hash 损坏/orphan/Reaper 演练；
- reconciliation 5 分钟 P1、15 分钟未 claim 升级、10 分钟 claim lease 和 provider outage 演练；
- 服务重启 15 分钟、整机恢复 4 小时和整机丢失 RPO 15 分钟门禁；
- P1 告警可送达；
- 单 Linux SPOF 在运行手册明确。
- candidate stage根据signed bundle的`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`确定性生成`deliveryProfile.requiredSlots[]`，不再维护手写组件列表；运行组件以自身全局唯一Principal/NKey调用`config.ready`，fixed base slot以descriptor绑定且全局唯一的`readyReporterPrincipal/probeNkeySelector`调用同一入口，receipt仍绑定稳定`(componentType,componentPrincipal,nodeId)`。production promote前复用同一stable slot set，在gated-passive生产维护域报告`PRODUCTION_GATED` READY并绑定rollout/deployed/environment digest。缺任一required slot、projection、probe authority、组件无法报告、plane/lease/fence/digest错误、角色ACL越权或`TEST-NATS-ACL-001`/`TEST-NATS-STREAM-SESSION-001`失败时不得激活。

---

## 16. C8：三机真机与发布

### 16.1 测试矩阵

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
- 在 claim_message 返回后、NATS dispatch 前 kill Core/Dispatch Worker，验证 intent 接管或 deadline FAILED；
- 丢弃 cancel control message，验证 Supervisor heartbeat/新 owner 仍执行取消；
- 运行两个 Relay 并让 event n 失败，验证 n+1 不越序；
- kill Runtime/Peer；
- 模拟 Push 500/timeout/重复；
- 让 Projector lag，验证 Get/List 权威快照不受影响；
- 模拟 provider 已执行但响应丢失，验证 effect UNKNOWN 与 reconciliation；
- 模拟 owner 崩溃且 effect 长期 APPLYING，验证 scanner 只建一个 UNKNOWN case；
- 模拟两个操作员并发 claim/旧 fencing 晚到、证据冲突和 resolution 后 reopen；
- 模拟 Artifact 上传中断、hash/size 不符、finalize/State 故障、对象缺失、删除重试和 Redis/Object Store 一致恢复；
- 并发执行 Artifact finalize/Task terminal/delete/download-ticket，验证版本 CAS 和 URL 暴露窗口；
- 模拟 config 签名错误、generation CAS 冲突、组件 NACK/READY 过期、bundle expiry、回滚撤销和 Card publisher 网络分区；
- 压满 Principal/global 队列，验证公平性、429/503 和计数回收；
- 磁盘接近水位；
- 重复 messageId 并发提交。
- 在隔离环境执行服务重启与完整节点恢复，记录 RTO/RPO。
- 破坏AuditEnvelope/segment JWS/跨日链/轮换签名，或Recovery Manifest/summary node/indexRoot/archive transition/receipt/approval/source水位，验证告警、REJECTED和恢复门禁；覆盖compaction每个写点及response loss。
- kill当前Stream Session Controller并由第二实例接管稳定delivery；在snapshot-covered ACK与final/expire/delete各写点故障注入，确认无死锁、早终态或consumer泄漏。

### 16.2 发布门禁

1. 目标交付剖面的全部自动化测试通过，无未知 skip。
2. 目标剖面的官方 SDK/stub/MCP 报告归档。
3. CORE 至少 Linux + 1 NAT Peer；INTEROP/EXTENDED 使用 Linux + 2 Windows 目标矩阵。
4. 故障恢复不自动重放 `UNKNOWN` effect；已知 effect 复用相同 provider idempotency key，UNKNOWN case 有可认领、可收证、可裁决、可审计的对账路径，且不改写失败 Task 终态。
5. 指标/告警/Runbook/备份和 RTO/RPO 演练通过。
6. Artifact blob/metadata、active config generation、reconciliation case/effect/Task、JetStream 与 audit checkpoint 可按外部 Manifest/Verification/Restore/Approval/Release receipts 跨存储对账并在 Redis 全损后重建。
7. 文档/代码/配置一致。
8. 兼容声明由评审批准。
9. `TEST-NATS-ACL-001`和`TEST-NATS-STREAM-SESSION-001`必须作为CORE必跑证据归档，报告PASS/0-skip并绑定staged bundleContentSha256+aclDigest；独立签名GateEvidenceRecordV1必须绑定报告SHA-256和当前readySetDigest，active pointer必须绑定其production-bound evidenceSha256。`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`生成的每个stable slot必须在candidate与production gated域分别取得合法READY；运行组件NKey或descriptor-bound外部probe credential在slot间互异。production READY还必须绑定同一rollout lease/fence、deployed ACL/stream/environment，缺一不得发布。

---

## 17. 关键业务链路实施检查

### 17.1 注册

```text
Peer verify active config + probe Runtime
→ stable publisher Principal acquires lease/fencing
→ build official public Card（无私有 NATS route）
→ connect NATS
→ State upsert_card
→ presence loop
→ Gateway well-known/query 可见
```

检查：generation、ETag、skill index、offline 不删 Card。

### 17.2 长任务

```text
SendMessage(returnImmediately)
→ claim/dedupe + durable dispatch intent
→ Dispatch Worker/NATS → Task Supervisor command.get → provisional lease → accept_dispatch_and_start CAS
→ register containment attestation → Supervisor heartbeat/events/Runtime
→ Projector/Redis
→ SSE/Push/Observer
→ terminal/Artifact
```

检查：无 stdout、断线、取消、lease lost、unsafe retry。

### 17.3 编排

```text
root Task
→ validate Plan DAG
→ persist Plan/Step/root-child mapping
→ select Agents
→ child Tasks
→ track independent results
→ aggregate Artifact
```

检查：Plan 重启恢复、DRR 公平、workspace affinity/lease/fencing、fan-out、失败策略、来源保留。

---

## 18. 测试体系

### 18.1 Unit

- state machine；
- subject/key builder；
- Card/extension；
- planner validator；
- runtime adapter/parser；
- observer rules；
- error mapping。

### 18.2 Integration

- Redis Lua 并发；
- NATS queue/private inbox/JetStream；
- Projector；
- Supervisor subprocess；
- Gateway/Core/State；
- Push mock server。

### 18.3 Conformance

独立环境安装官方 SDK，不导入项目 client：Card、11 操作、版本、错误、流顺序、每个 advertised Binding。

### 18.4 Security

- subject/key/path injection；
- NKey/ACL；
- SSRF/rebinding/redirect；
- secret/log scan；
- tool/workspace escape；
- zip/file URI 等输入边界；
- slow consumer/DoS limit。

### 18.5 E2E

Linux + 2 Windows，真实 Runtime 可用性按环境标记；发布环境不得跳过核心 Runtime/网络门禁。

---

## 19. 配置与 Secret

本地主机文件只允许保存启动引导信息，不是业务配置权威：

```yaml
mesh:
  id: default
bootstrap:
  config_controller: "https://config.internal.example"
  trust_root_file: "${A2AMESH_CONFIG_TRUST_ROOT_FILE}"
  cache_file: "${A2AMESH_CONFIG_CACHE_FILE}"
nats:
  servers: ["tls://mesh.example.com:4222"]
  credentials_file: "${A2AMESH_NATS_CREDS_FILE}"
state:
  request_timeout_seconds: 5
secrets:
  provider: "os-secret-store"
```

Credential/Alias/Grant、Card publisher、delivery profile、Artifact policy、Runtime/Tool/workspace policy、容量和超时全部进入 RFC 8785 canonical JSON + JWS 的签名 bundle，经 `VALIDATED → STAGED → ACTIVE` 激活。Redis URL、Bearer、Webhook encryption key、NKey seed、对象存储密钥和签名私钥不进入 bundle/YAML/Git，只使用 `secretRef` 指向 OS Secret Store/受保护文件。组件启动必须验证签名、meshId、active generation、expiry 和撤销状态，不能从未签名 YAML fail open。

---

## 20. 数据与协议迁移

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

## 21. 上线与回滚

### 21.1 上线

```text
备份 Redis/NATS/Object Store/active ACL/config bundle/audit 和信任根元数据
→ 以active generation兼容模式部署 State/Redis scripts 与 Config Controller（不改变生产ACL/流量）
→ validate/stage 新 bundle，不激活
→ 从immutable STAGED bytes确定性render candidate NATS stream/ACL，计算aclDigest
→ 在隔离candidate broker部署同一exact bytes和专用State candidate ingress（仅READY写权威staged keys，其余门禁调用写隔离测试Redis namespace），执行TEST-NATS-ACL-001/TEST-NATS-STREAM-SESSION-001并销毁fixture namespace；生产broker仍使用active ACL
→ 在candidate网络按signed bundle的`RequiredSlotSetV1`部署全部运行组件与descriptor-bound外部基础slot probe，所有实例保持GATED_PASSIVE、不接生产流量，并只经candidate State ingress报告对应stable slot READY
→ 按signed bundle的`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`读取并核对唯一stable slot set；每个`(componentType,componentPrincipal,nodeId)`恰有一份当前合法READY receipt，动态instanceId只作为该slot的实例证据；candidate与production不得混用，唯一Card publisher及generation必须一致
→ 将candidate两份PASS/0-skip报告写入不可变URI，绑定staged bundleContentSha256+aclDigest+candidate environmentDigest，生成candidate GateEvidence供隔离门禁审计（不可单独授权production CAS）
→ 以已stage的candidate evidenceSha256调用`ops config rollout prepare`，只执行PREPARE并取得持久rolloutLeaseId/fence/revision；长窗口用显式`rollout renew`续租，再调用`rollout enter-maintenance`只执行ENTER_MAINTENANCE，关闭Gateway/业务State入口并排空producer（这些阶段均无未来productionEvidenceSha256）
→ 在流量仍关闭时对生产candidate config执行`nats-server -t`、reload隔离测试过的同一exact bytes并重读deployedAclDigest
→ 以同一exact production ACL/stream、generation和rollout lease/fence在维护域启动新组件GATED_PASSIVE实例，只开放health/config.ready，不接业务流量；各required slot以`PRODUCTION_GATED`、deployed ACL/stream digest和production environmentDigest报告READY
→ 读取所有production READY receipt并生成新的production-bound GateEvidenceRecord，通过`ops config evidence stage-production <generation> <rolloutLeaseId>`独立持久化；缺任一production slot、错误environment/deployed digest或candidate-only receipt时该阶段拒绝，尚未调用ACTIVATE
→ 用已知production evidenceSha256调用`ops config rollout activate <generation> <rolloutLeaseId>`，Controller从持久rollout读取current fence/revision与部署read-back digests并只执行ACTIVATE active CAS；CAS前失败显式recover/restore，CAS后组件已在gated域运行，再以`rollout finish`只执行FINISH_ROLLOUT切换业务listener/开State业务门，最后开外部Gateway；任一lease过期只能由`rollout recover`双凭据TAKEOVER，State按active pointer唯一判定RESTORE或FINISH并永久拒绝旧fence
→ 官方黑盒
→ 一台 Windows canary
→ 三机全量
→ 观察指标/告警
```

### 21.2 回滚

- Gateway 可回滚到前一版本，但不能把新 v1 Task 交给不识别的旧 Core；
- 内部 major 不兼容时禁止混部；minor 只允许 N/N-1；Schema/Key 变更遵循读旧写新阶段；
- NATS Stream/Redis Key 不在应用回滚时立即删除；
- 正在执行 Task 优先完成/取消，不迁移 owner；
- 发生幂等/lease 不确定时停止新提交而不是冒险回滚执行状态。
- outbox、effect ledger 和 reconciliation 记录不得因应用回滚删除；存在 UNKNOWN 时禁止自动重放。
- 配置回滚必须发布更高 generation 指向旧内容，不能降低 active pointer 或手改 Redis；回滚前重新验证 secretRef、组件 READY 和 profile 门禁。
- Object Store 对象、Artifact metadata 和删除 tombstone 不随应用版本回滚；先停止新上传，再执行 inventory/一致性核对。

---

## 22. 团队分工

| 角色 | 责任 |
|---|---|
| 架构/协议 | C0/C1、规范、兼容门禁 |
| 状态/后端 | C2、Lua、outbox/effect/grant/admission、Event Relay、派生 Projector |
| NATS/网络 | C3、ACL、JetStream、Relay PubAck、Stream Session Controller、JS Provisioner、N/N-1、NAT 真机 |
| Runtime | C4、Adapter、Supervisor、SideEffectAdapter、Windows process |
| Gateway | C5 CORE JSON-RPC/SSE/Auth/Admission；C6 gRPC |
| Artifact/存储 | C2/C4/C5/C7，Object Store、完整性、Reaper、备份恢复 |
| 配置/安全 | C2/C5/C7，签名 bundle、generation、READY、回滚撤销、publisher fencing |
| 对账/运维 | C2/C4/C5/C7，case/evidence/claim/resolution、provider adapter、SLA |
| MCP/可观测/安全 | C6/C7、MCP、Push、Observer、监控、OAuth、SSRF、RTO/RPO |
| 测试 | conformance、故障注入、三机矩阵 |

一人可兼任，但每个退出门禁需独立复核。

---

## 23. 风险清单

| ID | 风险 | 控制 |
|---|---|---|
| R-001 | SDK/规范版本漂移 | 固定版本、官方 fixture、升级评审 |
| R-002 | 重试重复副作用 | Task dedupe 只防重复 Task；effectIntent/effectAttempt、side-effect ledger、provider idempotency 和 UNKNOWN 对账控制外部效果，不声明通用至多一次 |
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
| R-024 | Artifact 悬空、损坏或 signed URL 泄漏 | finalize 原子附加、SHA-256/size、短 TTL URL 不持久化、inventory/Reaper |
| R-025 | 组件配置 generation 分裂或未签名 fail open | JWS/canonical hash、READY、单 active CAS、过期/撤销启动门禁 |
| R-026 | UNKNOWN 人工处理不可追踪或误改 Task | 唯一 case、claim fencing、证据类型、原子 resolution、terminal immutable |
| R-027 | Task 已受理但执行命令丢失 | 同 claim 创建 durable dispatch intent、claim lease、ACCEPTED、deadline sweeper |
| R-028 | Cancel 控制消息丢失 | Redis cancelRequested 为事实，control 仅加速，heartbeat/接管重复检查 |
| R-029 | 多 Relay 越序或重复发布 | outbox claim lease、每 Task head-of-line、PubAck 后完成 |
| R-030 | Runtime 绕过 SideEffectAdapter | MEDIATED containment 门禁；UNMEDIATED 禁高风险和自动重试 |
| R-031 | 多存储备份时间相近但无法共同恢复 | Recovery Manifest、水位校验、删除 journal、fail closed |

---

## 24. 里程碑状态表

| 阶段 | 状态 | 证据/备注 |
|---|---|---|
| C0 基线 | 进行中，门禁未通过 | `pyproject.toml/uv.lock` 已锁 A2A SDK 1.1.2、MCP 2.0.0、Redis 8.1.0；current/target 文档已建立；仍缺完整官方 fixture/CI/compatibility negative evidence |
| C1 Canonical Core | 未开始 | — |
| C2 Redis State | 未开始 | 当前纯状态合同不计入Redis实现；仍缺Redis Function、真实持久化/重启及多实例门禁 |
| C2.5 最小控制面 | 未开始 | Config/Artifact/Reconciliation/Audit/Recovery 垂直切片 |
| C3 NATS v1 | 未开始 | 现有私有 NATS 仅作输入 |
| C4 Long Task | 未开始 | 现有 stdout stream 为部分实现 |
| C5 CORE JSON-RPC/SSE Gateway | 未开始 | — |
| C6-I INTEROP | 未开始 | gRPC/Push/额外 Runtime 实现证据；剖面声明仍累积依赖 CORE/C7/C8 |
| C6-E EXTENDED | 未开始（已有有限 MCP Bridge 原型） | 迁移到 Core/State 后再验收 MCP/OAuth/Observer |
| C7 Deployment Hardening | 未开始 | — |
| C8 Real Machines/Release | 未开始 | — |

状态更新必须附命令输出、测试报告或真机记录；不得把设计完成标记为代码完成。

---

## 25. 最终上线准入

- 十一份版本化专项文档及本实施计划完成 G0 评审；G0 只代表设计冻结，不替代下列实现证据；
- 目标交付剖面的 BR/NFR 有对应实现和 TEST；
- CORE 必须通过官方 JSON-RPC SDK；INTEROP 才要求 gRPC stub；EXTENDED 才要求 MCP 2026-07-28 client；
- 每个实际发布的 Card interface 通过同一语义套件，未交付 Binding 不得广告；
- Redis/NATS/Object Store/Config/Audit/Peer/Gateway/Runtime/Reconciliation 及 dispatch/outbox/cancel 故障注入通过；
- 长任务心跳、断线、取消、恢复通过；
- CORE 必须通过 Tool/capability/admission/effect、signed config、Artifact 和 reconciliation 安全门禁；Push 属于 INTEROP，MCP/OAuth/Observer 属于 EXTENDED；
- CORE 必须通过 TEST-IDENTITY-001、TEST-TENANT-001、TEST-OUTBOX-001、TEST-EFFECT-001、TEST-AUTHZ-001、TEST-ADMISSION-001、TEST-ARTIFACT-001、TEST-ARTIFACT-HOLD-EXPIRY-001、TEST-ARTIFACT-HOLD-REPLAY-001、TEST-NATS-ACL-001、TEST-CONFIG-ATOMIC-001、TEST-RECON-RESOLVE-001、TEST-RECON-IMMUTABLE-001、TEST-AUDIT-SINK-001、TEST-DR-MANIFEST-001、TEST-DR-001；MCP/OAuth 测试仅在 EXTENDED 强制；
- CORE 至少 Linux + 1 NAT Peer 双向调用；INTEROP/EXTENDED 才要求 Linux + 2 Windows 任意方向；
- 监控、WORM 审计、告警、Recovery Manifest/备份、Runbook 完整；
- 无高危未决缺陷；
- README 与实际能力一致。
