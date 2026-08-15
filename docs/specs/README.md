# A2AMesh V1 设计文档索引

> 生成日期：2026-08-14
> 文档状态：**V1.6/V1.2 当前权威候选集；独立复审问题修复中，G0 正式批准待关闭**
> 实现状态：仍是 private A2A-inspired NATS prototype；以《A2AMesh 开发实施计划》为准
> 协议基线：A2A Specification v1.0.1，协商值 `1.0`

---

## 1. 文档目的

本目录是 A2AMesh V1 的唯一正式设计入口。当前活动集由 **8 份 V1.6 主专项、3 份 V1.2 控制面专项、1 份非权威综合分析和持续更新的实施计划**组成。旧 V1.5/V1.1 活动集及更早版本位于 [`docs/archive`](../archive/README.md)，仅用于审计追溯，不参与当前实现合同。

G0 正式通过仅表示关键状态、竞态、时序、版本、ACL 和恢复规则已有唯一设计答案，并须绑定复审台账/content manifest；**不表示代码已经实现、官方 A2A 黑盒已经通过、三机部署已经完成或生产已经可用**。本候选集在二次独立复审关闭前不得标记正式通过。

### 1.1 最新架构图

[![A2AMesh V1.6 最新架构](../assets/A2AMesh_V1.6_Architecture.svg)](../assets/A2AMesh_V1.6_Architecture.html)

- [交互式 HTML 架构图](../assets/A2AMesh_V1.6_Architecture.html)
- [SVG 架构图](../assets/A2AMesh_V1.6_Architecture.svg)
- [最新架构全量分析 V1.6](A2AMesh_最新架构全量分析_V1.6.md)

---

## 2. 当前文档清单与阅读顺序

| 序号 | 文档 | 权威内容 | 推荐读者 |
|---:|---|---|---|
| 1 | [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md) | 边界、剖面、拓扑、权威流、G0 总合同、RTO/RPO | 全员 |
| 2 | [Agent Card 与协议对象规范 V1.6](A2AMesh_AgentCard与协议对象规范_V1.6.md) | 官方对象、公开/内部 Card、Task 状态、扩展发布边界 | 协议、后端、测试 |
| 3 | [A2A 协议与 NATS 集成适配设计 V1.6](A2AMesh_A2A协议与NATS集成适配设计_V1.6.md) | durable dispatch、Subject/ACL、Envelope、Auth replay、Relay、版本兼容 | 架构、后端、运维 |
| 4 | [Redis 状态平面与数据设计 V1.6](A2AMesh_Redis状态平面与数据设计_V1.6.md) | Task/dispatch/outbox/effect/Plan/replay/workspace/Artifact/config/reconciliation/DR 数据合同 | 后端、DBA、测试 |
| 5 | [任务生命周期与长任务运行时设计 V1.6](A2AMesh_任务生命周期与长任务运行时设计_V1.6.md) | 完整迁移矩阵、Supervisor、Cancel、SSE、重试、故障恢复 | Runtime、前端、测试 |
| 6 | [编排器、Runtime 与工具适配设计 V1.6](A2AMesh_编排器_Runtime与工具适配设计_V1.6.md) | 持久 Plan、DRR、公平准入、workspace fencing、Runtime containment、Tool/MCP | Agent、后端、安全 |
| 7 | [接口请求与响应标准 V1.6](A2AMesh_接口请求与响应标准_V1.6.md) | 11 操作、入口顺序、JSON-RPC/gRPC/SSE/MCP、错误和幂等 | 联调、SDK、测试 |
| 8 | [Artifact 与对象存储设计 V1.2](A2AMesh_Artifact与对象存储设计_V1.2.md) | 稳定 URI、上传/完成/票据/删除竞态、授权保留和对象恢复 | 存储、安全、运维 |
| 9 | [受信配置与变更治理设计 V1.2](A2AMesh_受信配置与变更治理设计_V1.2.md) | genesis、canonical hash/JWS、可信 READY、generation rollout、publisher ownership | 架构、安全、运维 |
| 10 | [人工对账与运维操作设计 V1.2](A2AMesh_人工对账与运维操作设计_V1.2.md) | 正交 case 状态、Evidence、claim、resolutionHistory、stale APPLYING | 后端、安全、运维 |
| 11 | [统计、审计与运行监控规则 V1.6](A2AMesh_统计审计与运行监控规则_V1.6.md) | 指标、WORM Audit Sink、告警、Runbook、Recovery Manifest | 运维、测试、架构 |
| 12 | [开发实施计划](A2AMesh_开发实施计划.md) | current-vs-target、C0～C8、C2.5、C6-I/C6-E、门禁和发布 | 项目全员 |
| 13 | [最新架构全量分析 V1.6](A2AMesh_最新架构全量分析_V1.6.md) | 对 11 份权威专项的综合解释、G0 证据、当前实现差距和路线图；不覆盖专项 | 评审、项目负责人 |

推荐顺序：`1 → 13 → 2 → 3 → 4 → 5 → 6 → 7 → 8/9/10 → 11 → 12`。编码人员先读 1、12，再按组件读对应专项。

---

## 3. 权威边界

| 规则 | 唯一权威文档 |
|---|---|
| 产品边界、拓扑、剖面、全局权威数据流 | 总体架构 |
| Agent Card、Task/Message/Artifact 协议字段和扩展 | Agent Card 与协议对象规范 |
| NATS Subject、ACL、Envelope、dispatch/event 传输 | NATS 集成适配 |
| Redis Key、字段、原子函数、TTL、索引 | Redis 状态平面 |
| Task 状态、heartbeat、Cancel、SSE、恢复 | 任务生命周期 |
| Plan、调度、Runtime containment、Tool/workspace | 编排与 Runtime |
| 请求头、11 操作、错误、Binding 映射 | 接口标准 |
| blob、stable URI、票据、删除和存储恢复 | Artifact 专项 |
| bundle/JWS、genesis、READY、激活/回滚/撤销 | 受信配置专项 |
| UNKNOWN case、Evidence、claim、resolution history | 人工对账专项 |
| Metrics、Audit Sink、告警、看板和观测保留 | 监控规则 |
| 当前完成度、阶段依赖、退出门禁 | 开发实施计划 |
| 跨文档解释和评估 | 综合分析；冲突时无权覆盖上述专项 |

业务规则与计划状态冲突时：业务规则以专项为准，当前实现与排期以实施计划和真实测试证据为准。

---

## 4. G0 设计冻结关闭矩阵

| G0 项 | 主权威文档与章节 | 定义 ID/对象 | 支持文档 | 验收 TEST ID |
|---|---|---|---|---|
| G0-01 durable dispatch | Redis §5.16、§6.14 | `DATA-DISPATCH-001` / `DispatchTask` | NATS §6.1.1、§16.2；Task §7.2 | `TEST-DISPATCH-001` |
| G0-02 durable cancel | Redis §6.5 | `request_cancel` / Task CAS | Task §12.1；API §9 | `TEST-CANCEL-RACE-001` |
| G0-03 ordered Event Outbox | Redis §5.9、§6.15 | `DATA-OUTBOX-001` | NATS §16.3 | `TEST-OUTBOX-ORDER-001` |
| G0-04 effect recovery | Redis §5.10、§6.7 | `DATA-EFFECT-001` | Task §13；Runtime §8.4；对账 §3.4 | `TEST-EFFECT-001`、`TEST-EFFECT-STALE-001` |
| G0-05 Task 状态机 | Task §3 | official TaskState matrix | Agent Card §6；API §5/§9 | `TEST-TASK-STATE-001` |
| G0-06 Plan/DRR | Redis §5.12、§5.17、§6.9、§6.16 | `DATA-ADMISSION-001` / `DATA-PLAN-001` | Runtime §4、§14 | `TEST-PLAN-RECOVERY-001`、`TEST-DRR-001` |
| G0-07 Auth replay | Redis §5.18、§6.18 | `DATA-AUTH-REPLAY-001` | NATS §6.5、§16.7、§16.9；API §19 | `TEST-AUTH-REPLAY-001`、`TEST-IPC-REPLAY-001` |
| G0-08 Binding version | NATS §16.1、§16.5 | `BindingCapabilities` / `TaskEventEnvelope` | API §14 | `TEST-BINDING-VERSION-001` |
| G0-09 profile×operation | 总体 §11、§12.1 | cumulative profile lattice | Agent Card §3、§14；API §4 | `TEST-PROFILE-OPS-001` |
| G0-10 NATS ACL/stream session | NATS §4.3、§9.4、§16.6 | NKey permission matrix / `DATA-STREAM-SESSION-001` | Config §3.2；Redis §5.21、§6.23 | `TEST-NATS-ACL-001`、`TEST-NATS-STREAM-SESSION-001` |
| G0-11 Artifact races | Artifact §4～§8 | `ArtifactAccessTombstone` / `ArtifactHold` / typed ref | Redis §5.13、§6.11 | `TEST-ARTIFACT-RACE-001`、`TEST-ARTIFACT-AUTH-RETENTION-001`、`TEST-ARTIFACT-HOLD-REF-001` |
| G0-12 trusted config | Config §3、§3.3、§4、§7.1 | signed bundle / `components[]` / READY / `DATA-GATE-EVIDENCE-001` | Redis §5.14、§6.12 | `TEST-CONFIG-HASH-001`、`TEST-CONFIG-GENESIS-001`、`TEST-CONFIG-READY-AUTH-001`、`TEST-CONFIG-GATE-EVIDENCE-001` |
| G0-13 reconciliation | 对账 §3、§6、§7 | `ResolutionRecord` / case matrix | Redis §5.15、§6.13 | `TEST-RECON-STATE-001`、`TEST-RECON-REOPEN-HISTORY-001` |
| G0-14 Runtime containment | Runtime §8.5、§11 | `ContainmentProfile` / Merge Broker | Config §3.2；Redis §5.18 | `TEST-RUNTIME-CONTAINMENT-001`、`TEST-WORKSPACE-FENCE-001` |
| G0-15 DR/Audit | Redis §5.19、§5.20、§6.21～§6.22 | `DATA-AUDIT-001` / `DATA-RECOVERY-001` | Artifact §8.3；Config §8；监控 §11.1 | `TEST-AUDIT-SINK-001`、`TEST-DR-MANIFEST-001` |

G0 的通过条件是：每项恰有一个主权威、定义对象、支持文档、失败语义和完整 TEST ID，并由评审台账绑定候选 content manifest。代码门禁状态仍全部按实施计划维护。

---

## 5. 版本管理规则

1. 带版本号的专项发布后不可原地修订；变更时复制、递增版本并更新索引。
2. 当前活动基线是 V1.6/V1.2；旧活动集整体归档在 [`docs/archive/v1.5`](../archive/v1.5/README.md)。
3. `A2AMesh_开发实施计划.md` 是 living document，不在文件名增加版本。
4. 项目文档版本、A2A 协议版本、内部 Binding schema 版本、SDK/package 版本彼此独立。
5. 只有实施门禁有真实命令输出、官方黑盒和真机证据后，README 才可写“已实现/已兼容/生产可用”。

---

## 6. 标识与写作规范

| 前缀 | 含义 | 示例 |
|---|---|---|
| `BR` | 业务需求 | `BR-020` 受理命令不丢 |
| `NFR` | 非功能需求 | `NFR-019` 每 Task 事件有序 |
| `ADR` | 架构决策 | `ADR-031` durable dispatch intent |
| `API` | 接口契约 | `API-A2A-006` SubscribeToTask |
| `DATA` | 数据契约 | `DATA-DISPATCH-001` |
| `EVT` | 事件契约 | `EVT-PROGRESS-001` |
| `OBS` | 指标/告警 | `OBS-ALERT-031` dispatch 堵塞 |
| `TEST` | 验收用例 | `TEST-DISPATCH-001` |

通用规则：

1. 当前实现与目标设计分开；规划不得伪装成已交付。
2. 官方对象以 A2A v1.0.1 Proto/SDK 为准；内部扩展不得覆盖标准字段。
3. V1 是单 Mesh/单信任域，不建设 tenant、RBAC、Permission Center；tenant 非空在认证业务处理前拒绝。
4. Mermaid/JSON/Lua 伪代码必须配权威说明、失败语义和验收条件。
5. 文档不得包含真实 Token、NKey seed、密码、signed URL 或隐私内容。

---

## 7. 需求追踪矩阵

### 7.1 业务需求

| 需求 | 主要设计 | 核心契约 | 验收 ID |
|---|---|---|---|
| BR-001 对称调用 | 总体、NATS | `a2a.v1.rpc.<agentId>`、Gateway 东西向旁路 | TEST-MESH-001 |
| BR-002 NAT 零入站 | 总体、部署 | Peer 主动 NATS TLS/WSS | TEST-NAT-001 |
| BR-003 标准互操作 | 对象、API | 11 操作、9 个错误、官方对象 | TEST-A2A-001 / TEST-ERROR-001 |
| BR-004 多 Runtime | Runtime | RuntimeAdapter/Probe/containment | TEST-RUNTIME-001 |
| BR-005 长任务可观察 | Task | heartbeat/progress/lease | TEST-LONG-001 |
| BR-006 断线恢复 | Task、API | snapshot-first + GetTask/Subscribe | TEST-RECOVERY-001 |
| BR-007 幂等提交 | Redis、NATS | DATA-DEDUPE-001 + payload conflict | TEST-IDEMP-001 |
| BR-008 多 Agent 观察 | Runtime | Observer rule/policy | TEST-OBSERVER-001 |
| BR-009 可运维 | 监控、对账 | OBS-ALERT-001～036、ops case | TEST-OBS-001 |
| BR-010 可演进 | 全部 | URI/Key/Envelope/schema version | TEST-VERSION-001 |
| BR-011 gRPC 互操作 | API | 官方 A2AService 11 RPC | TEST-GRPC-001 |
| BR-012 MCP 互操作 | Runtime、API | MCP 2026-07-28 tools/resources | TEST-MCP-001 |
| BR-013 身份一致 | NATS、Redis、API | Canonical Principal/AuthProof | TEST-IDENTITY-001 |
| BR-014 MCP 幂等 | Runtime、Redis | required messageId + canonical hash | TEST-MCP-IDEMP-001 |
| BR-015 OAuth 闭环 | Runtime、监控 | RFC9728/RFC8414/JWKS | TEST-OAUTH-001 |
| BR-016 无租户互操作 | 对象、API | tenant reject-before-side-effect | TEST-TENANT-001 |
| BR-017 大型 Artifact | Artifact、Redis | stable URI + finalize/delete saga | TEST-ARTIFACT-001 |
| BR-018 受信配置 | Config | signed generation + READY + fencing | TEST-CONFIG-ATOMIC-001 |
| BR-019 UNKNOWN 可运营 | 对账 | Evidence/claim/resolution/history | TEST-RECON-RESOLVE-001 |
| BR-020 受理命令不丢 | NATS、Redis、Task | DATA-DISPATCH-001 | TEST-DISPATCH-001 |
| BR-021 可验证恢复 | Artifact、Config、监控 | DATA-RECOVERY-001 + WORM audit | TEST-DR-MANIFEST-001 |

### 7.2 非功能需求

| 需求 | 设计控制 | 验收 ID |
|---|---|---|
| NFR-001 presence 时效 | 5s heartbeat、15s suspect、30s offline | TEST-PRESENCE-001 |
| NFR-002 路由开销 | Core/NATS P50/P95/P99 | TEST-PERF-001 |
| NFR-003 Task 幂等/单 owner | dedupe + lease/fencing；effect 另用 ledger | TEST-IDEMP-001 / TEST-EFFECT-001 |
| NFR-004 流事件顺序 | eventSequence + JetStream + per-Task HOL | TEST-OUTBOX-ORDER-001 |
| NFR-005 静默 heartbeat | Supervisor 独立协程、30s 事件采样 | TEST-LONG-001 |
| NFR-006 重启一致性 | Redis AOF、dispatch/outbox claim、fencing | TEST-RECOVERY-001 |
| NFR-007 隐私脱敏 | keyed pseudonym、低基数 metrics、Tool policy | TEST-SEC-001 |
| NFR-008 不虚假 HA | 单 Linux SPOF 明示 | TEST-DOC-001 |
| NFR-009 跨 Binding 身份 | Canonical Principal + immutable alias | TEST-IDENTITY-001 |
| NFR-010 OAuth fail closed | TTL/JWKS cache/unknown kid reject | TEST-OAUTH-001 |
| NFR-011 RTO | 服务 15 分钟、整机 4 小时 | TEST-DR-001 |
| NFR-012 RPO | 持久卷健康为 0；整机故障≤15 分钟 | TEST-DR-MANIFEST-001 |
| NFR-013 有界准入 | global/Principal queue、DRR、deadline | TEST-ADMISSION-001 / TEST-DRR-001 |
| NFR-014 最小授权 | Principal/Agent/op/skill/tool/workspace 全维 | TEST-AUTHZ-001 |
| NFR-015 Artifact 完整性 | 服务端 checksum、CAS、owner tombstone | TEST-ARTIFACT-INTEGRITY-001 |
| NFR-016 配置原子一致 | canonical JWS、genesis、staged index、active CAS | TEST-CONFIG-ATOMIC-001 |
| NFR-017 对账可审计 | claim fencing、Evidence、history immutable | TEST-RECON-IMMUTABLE-001 |
| NFR-018 命令最终投递 | dispatch intent/claim/ACCEPTED/deadline | TEST-DISPATCH-001 |
| NFR-019 事件严格有序 | Relay lease + per-Task head-of-line | TEST-OUTBOX-ORDER-001 |
| NFR-020 副作用故障闭合 | effect intent/attempt + stale scanner | TEST-EFFECT-STALE-001 |
| NFR-021 共同恢复点 | Recovery Manifest + deletion journal | TEST-DR-MANIFEST-001 |
| NFR-022 执行与审计可信 | Runtime containment + WORM Audit Sink | TEST-RUNTIME-CONTAINMENT-001 / TEST-AUDIT-SINK-001 |

---

## 8. 文档集验收

- 11 份当前权威专项、综合分析、索引和实施计划存在且链接可达；旧集已归档。
- 每份专项有版本记录、权威边界、失败/恢复和验收 ID。
- G0-01～15 可从需求追到 DATA/API/ADR/TEST，不存在两个互相冲突的权威路径。
- A2A protocol、内部 Binding schema、文档和 package 版本不混用。
- 无 tenant/RBAC/Permission Center 误入 V1；capability 不是通用 RBAC。
- 实施计划明确 C2.5 依赖、C6-I/C6-E 可并行实现，以及 EXTENDED⊃INTEROP⊃CORE 的累积声明门禁。
- 兼容/生产声明只引用最新真实测试、官方黑盒和真机证据。
