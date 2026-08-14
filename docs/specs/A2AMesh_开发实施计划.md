# A2AMesh 开发实施计划

---

# 1. 文档目的

本文档给出 A2AMesh 从当前私有 NATS RPC 原型演进到可发布 A2A v1 Mesh 的完整实施计划，明确：

- 当前代码事实和目标设计的差距；
- V1 必须交付和明确不实现的范围；
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
| 1 | 业务与总体架构设计 V1.0 | 定位、边界、拓扑、范围、NFR |
| 2 | Agent Card 与协议对象规范 V1.0 | 官方对象、Card、扩展、状态 |
| 3 | A2A 协议与 NATS 集成适配设计 V1.0 | Subject、Envelope、11 操作、投递 |
| 4 | Redis 状态平面与数据设计 V1.0 | Key、Lua、lease、幂等、保留 |
| 5 | 任务生命周期与长任务运行时设计 V1.0 | Supervisor、进度、SSE、Push、恢复 |
| 6 | 编排器、Runtime 与工具适配设计 V1.0 | Plan、Adapter、Tool、Workspace |
| 7 | 接口请求与响应标准 V1.0 | Gateway、请求、错误、示例 |
| 8 | 统计、审计与运行监控规则 V1.0 | 指标、日志、告警、保留 |
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

```text
Python 源文件：37
源代码行：2229
测试文件：9
测试代码行：1227
测试：31 passed, 4 skipped
分支：main
```

## 2.2 能力矩阵

| 能力 | 状态 | 现有位置 | 结论 |
|---|---|---|---|
| 项目 Pydantic AgentCard/Task/Message | 部分实现 | `contracts/models.py` | 需替换为官方对象 |
| 私有 NATS client/server | 已实现并有测试 | `a2anats/` | 作为迁移输入，不是 v1 Binding |
| 私有 send/stream/get/cancel | 部分实现 | `runtime/agent.py` | 语义需迁移到 11 操作 |
| Runtime Executor | 部分实现 | `runtime/executor.py` | 缺独立 heartbeat/lease |
| Hermes/Codex/Claude/OpenCode Adapter | 部分实现 | `runtime/adapters/` | 需固定版本 probe/fixture |
| Tool Registry/MCP | 部分实现 | `tools/` | 需风险策略/workspace 安全 |
| Planner/Dispatcher/Tracker/Aggregator | 基础实现 | `orchestrator/` | 需 Redis 状态和官方 Task |
| KV/Memory | 基础实现 | `memory/store.py` | 不作为 Redis State 替代 |
| 官方 Agent Card well-known | 缺失 | — | C5 实现 |
| 官方 A2A v1 Application Core | 缺失 | — | C1 实现 |
| Redis State Service | 缺失 | — | C2 实现 |
| NATS v1 Binding | 缺失 | — | C3 实现 |
| TaskSupervisor/Progress | 缺失 | — | C4 实现 |
| JSON-RPC/SSE Gateway | 缺失 | — | C5 实现 |
| Push Dispatcher/Observer | 缺失 | — | C6 实现 |
| 生产监控/备份/真机门禁 | 缺失 | — | C6～C8 |

## 2.3 总体实施结论

当前版本只能称为：

> private A2A-inspired NATS prototype

必须依次完成 canonical core、State Service、NATS Binding、长任务、标准 Gateway 和官方黑盒后，才能称为 A2A v1.0 JSON-RPC compatible。V1 不要求实现所有三种标准 Binding；只声明并测试 JSONRPC，NATS 以自定义 URI 声明。

---

# 3. 建设范围

## 3.1 V1 必须交付

1. A2A v1.0.1 官方对象和固定 SDK。
2. 11 个核心操作及统一 Application Core。
3. Agent Card well-known、ETag、Progress/Runtime 扩展。
4. Redis State Service、Task/Card/Context、List、幂等、lease、Push 配置。
5. NATS v1 Binding、私有 inbox、JetStream 有序事件。
6. TaskSupervisor、heartbeat、process tree cancel、恢复。
7. JSON-RPC/SSE Gateway、A2A-Version。
8. Push Dispatcher 和受控 Observer。
9. 四类 Runtime Adapter 与 workspace/tool policy。
10. 指标、审计、Trace、健康、告警、备份。
11. Linux + 2 Windows 真机任意方向调用。

## 3.2 V1 明确不实现

- tenant、RBAC、用户/组织权限；
- 通用后台 UI；
- Redis Cluster/跨区域 HA；
- 任意 shell 公开 Skill；
- 自动重试未知副作用任务；
- 原始 Chain-of-Thought；
- gRPC（除非另立变更）；
- 多 Mesh 联邦。

## 3.3 MVP 与生产 V1

| 能力 | MVP | 生产 V1 |
|---|---|---|
| Gateway | 本地 HTTPS/测试凭据 | 正式证书、部署级认证、限流 |
| Redis | 单实例 AOF | 备份、恢复演练、监控 |
| NATS | 单实例 JetStream | TLS/NKey/ACL/持久目录/备份 |
| Runtime | Hermes + 1 个 Adapter | 四个 Adapter 固定版本 |
| Push | 本地 mock webhook | SSRF、签名、重试、DLQ |
| Observer | 规则日志 | 受控 LLM 分析/防环 |
| 真机 | Linux + 1 Windows | Linux + 2 Windows 任意方向 |

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
│   └── nats_v1.py
├── state/
│   ├── client.py
│   ├── service.py
│   ├── redis_repository.py
│   ├── projector.py
│   ├── models.py
│   └── scripts/
│       ├── claim_message.lua
│       ├── transition_task.lua
│       ├── lease.lua
│       └── upsert_card.lua
├── gateway/
│   ├── app.py
│   ├── auth.py
│   ├── routes.py
│   ├── sse.py
│   └── push_dispatcher.py
├── runtime/
│   ├── agent.py
│   ├── supervisor.py
│   ├── executor.py
│   ├── progress.py
│   ├── workspace.py
│   └── adapters/
├── orchestrator/
├── observer/
│   ├── consumer.py
│   ├── rules.py
│   └── policy.py
├── tools/
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
  "a2a-sdk[http-server,signing,telemetry]==1.1.2",
  "redis[hiredis]==8.1.0"
]
```

继续使用固定版本 `nats-py`、Pydantic、pytest。HTTP server 优先复用官方 SDK 支持的框架/adapter；不要自行实现另一套 JSON-RPC parser。最终版本写入 lockfile。

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
6. 保留当前 31 passed / 4 skipped 基线。
7. CI 增加 `pytest`、`ruff`、`compileall`、文档链接检查。

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

## 8.4 退出门禁

- 11 方法接口存在且有 contract test；
- 官方 SDK 可解析所有输入/输出；
- 非法终态回退、Task/Context 不匹配被拒绝；
- Card heartbeat 不改 ETag；
- 扩展被标准客户端忽略后对象仍合法。

---

# 9. C2：Redis State Service

## 9.1 目标

替换单进程 Task/Card 状态，建立共享幂等、lease、List 和恢复基础。

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

## 9.4 测试

```text
100 并发同 messageId → 1 Task
同 messageId 不同 payload → conflict
双 owner → 1 lease
旧 token late write → reject
List cursor → 无重/漏
Redis restart → Task/Card/dedupe 恢复
公网/Windows → 6379 不可达
```

## 9.5 退出门禁

所有状态 API 只经 State Service；新任务不再写进程 `_tasks` 权威字典；故障时 fail closed。

---

# 10. C3：NATS v1 Binding

## 10.1 目标

实现版本化 Subject/Envelope、11 操作映射、私有 reply、JetStream 事件。

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
6. JetStream stream/consumer/projection event。
7. Card upsert/presence/state RPC。
8. 11 操作语义套件复用 C1 tests。
9. timeout retry 使用稳定 messageId。
10. 禁止宽 `_INBOX.>` 和可预测输出 subject。

## 10.4 退出门禁

- 两 Peer 并发实例只执行一次；
- 非授权 Peer 无法订阅他人回复/Task event；
- Core 与 NATS Binding 语义等价；
- NATS 重启/重连不重复终态。

---

# 11. C4：Runtime 与长任务 Supervisor

## 11.1 目标

让静默长任务可观察、可取消、可恢复，完成 Progress/Projector。

## 11.2 文件

```text
runtime/supervisor.py
runtime/progress.py
runtime/workspace.py
state/projector.py
observer/rules.py
tests/unit/runtime/test_supervisor.py
tests/integration/test_long_task_*.py
```

## 11.3 任务

1. 拆分 Executor 与 Supervisor。
2. 独立 heartbeat/lease/cancel/event 协程。
3. Linux process group + Windows Job Object/进程树。
4. RuntimeEvent contract 和四 Adapter parser。
5. Progress Extension 映射。
6. JetStream event sequence/attempt。
7. Projector 合并 Redis 快照。
8. workspace alias/realpath/lock。
9. retry-safe policy。
10. Observer deterministic rules（暂不调用 LLM）。

## 11.4 门禁

- 60 秒无 stdout 仍有 heartbeat；
- 无换行输出不阻塞 cancel；
- lease lost 后不再副作用；
- unsafe 崩溃不自动重跑；
- 多订阅者事件顺序一致；
- Windows/Linux 均能杀完整子进程树。

---

# 12. C5：标准 A2A Gateway

## 12.1 目标

用官方 SDK 暴露 well-known Card、JSON-RPC、SSE 和 A2A-Version。

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
2. `/.well-known/agent-card.json`、ETag/304。
3. JSON-RPC 11 方法接 Core。
4. `A2A-Version: 1.0` 和 VersionNotSupported。
5. SendStreaming/Subscribe SSE。
6. SSE comment keepalive、慢客户端上限。
7. Get/List/Cancel。
8. 部署级 Bearer/mTLS 可配置；不建设 RBAC。
9. 官方 SDK 独立虚拟环境黑盒。
10. 关闭旧协议默认入口。

## 12.4 退出门禁

官方 client 可执行 send/get/list/cancel/stream/subscribe；Card 只声明通过的接口。此时才可称 JSON-RPC A2A v1 compatible。

---

# 13. C6：Push、Observer、Card 增强与可观测

## 13.1 任务

1. Push CRUD 和 Redis 配置。
2. Dispatcher durable consumer。
3. HTTPS/SSRF/DNS rebinding/redirect 防护。
4. deliveryId、签名、timeout、退避、DLQ。
5. Observer rule aggregation + 可选 LLM adapter。
6. observe/intervention policy、冷却和次数限制。
7. Extended Card（若启用）与 JWS 签名。
8. Metrics、Audit、Trace、Health、Alerts。
9. 运行看板和 P1 Runbook。

## 13.2 退出门禁

- Push 慢/失败不阻塞 Task；
- SSRF 测试覆盖 loopback/private/metadata/redirect/rebinding；
- 重复 Webhook 可去重；
- Observer 不处理普通 heartbeat、不自触发；
- 日志 secret/思维链扫描通过；
- Card 签名篡改失败（若声明）。

完成 C6 后达到本项目定义的完整 A2A 功能覆盖。

---

# 14. C7：部署与安全加固

## 14.1 Linux

- NATS TLS/NKey/ACL/JetStream 持久目录；
- Redis loopback/ACL/AOF/noeviction；
- Gateway HTTPS；
- State/Gateway/Peer systemd 或容器；
- 日志轮转、磁盘告警、备份；
- firewall 只开放 HTTPS 和 NATS TLS/WSS；
- secret 文件最小权限。

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
| 官方 Client | Gateway | 11 操作 |

故障注入：

- 任务中断开浏览器 SSE；
- 任务中断开 Peer 网络；
- 重启 NATS/Redis/State/Gateway；
- kill Runtime/Peer；
- 模拟 Push 500/timeout/重复；
- 让 Projector lag；
- 磁盘接近水位；
- 重复 messageId 并发提交。

## 15.2 发布门禁

1. 全部自动化测试通过，无未知 skip。
2. 官方 SDK 报告归档。
3. 三机矩阵通过。
4. 故障恢复无重复副作用。
5. 指标/告警/Runbook/备份通过。
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
- Schema/Key 变更遵循读旧写新阶段；
- NATS Stream/Redis Key 不在应用回滚时立即删除；
- 正在执行 Task 优先完成/取消，不迁移 owner；
- 发生幂等/lease 不确定时停止新提交而不是冒险回滚执行状态。

---

# 21. 团队分工

| 角色 | 责任 |
|---|---|
| 架构/协议 | C0/C1、规范、兼容门禁 |
| 状态/后端 | C2、Lua、Projector、List/Push state |
| NATS/网络 | C3、ACL、JetStream、NAT 真机 |
| Runtime | C4、Adapter、Supervisor、Windows process |
| Gateway | C5、JSON-RPC/SSE/Auth |
| 可观测/安全 | C6/C7、Push、Observer、监控、SSRF、备份 |
| 测试 | conformance、故障注入、三机矩阵 |

一人可兼任，但每个退出门禁需独立复核。

---

# 22. 风险清单

| ID | 风险 | 控制 |
|---|---|---|
| R-001 | SDK/规范版本漂移 | 固定版本、官方 fixture、升级评审 |
| R-002 | 重试重复副作用 | messageId+payloadHash+State dedupe |
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

---

# 23. 里程碑状态表

| 阶段 | 状态 | 证据/备注 |
|---|---|---|
| C0 基线 | 未开始（文档基线已建立） | 需代码 CI/官方 fixture |
| C1 Canonical Core | 未开始 | — |
| C2 Redis State | 未开始 | — |
| C3 NATS v1 | 未开始 | 现有私有 NATS 仅作输入 |
| C4 Long Task | 未开始 | 现有 stdout stream 为部分实现 |
| C5 Gateway | 未开始 | — |
| C6 Push/Observer/Telemetry | 未开始 | — |
| C7 Deployment Hardening | 未开始 | — |
| C8 Real Machines/Release | 未开始 | — |

状态更新必须附命令输出、测试报告或真机记录；不得把设计完成标记为代码完成。

---

# 24. 最终上线准入

- 八份版本化专项文档及本实施计划完成评审；
- 所有 BR/NFR 有对应实现和 TEST；
- 官方 SDK 黑盒通过；
- 每个 Card interface 通过同一语义套件；
- Redis/NATS/Peer/Gateway/Runtime 故障注入通过；
- 长任务心跳、断线、取消、恢复通过；
- Push/Observer/Tool 安全门禁通过；
- Linux + 2 Windows 任意方向调用通过；
- 监控、审计、告警、备份、Runbook 完整；
- 无高危未决缺陷；
- README 与实际能力一致。
