# A2AMesh 最新架构全量分析 V1.6

> 文档ID：`A2AM-REVIEW-001`
> 文档状态：G0 候选综合分析；非专项权威，不覆盖 11 份领域合同
> 权威范围：仅综合解释当前设计集、G0 证据、实现差距和路线图；领域规则仍以 11 份专项为准
> 评审状态：G0候选；结构与链接自检通过，R11-C状态合同与R11-D架构资产局部门禁已通过，最终独立复审与发布门禁仍待完成
> 分析日期：2026-08-14
> 最后更新：2026-08-16
> 分析范围：当前 8 份 V1.6、3 份 V1.2 专项、开发实施计划及仓库现状
> 首次版本：V1.6
> 实现结论：设计候选正在关闭复审；代码仍为 private A2A-inspired NATS prototype，未达到 CORE 交付门禁

---

## 1. 分析目的和判定方法

本文对当前全部最新架构设计进行横向整合，回答五个问题：

1. A2AMesh 最新目标架构到底是什么；
2. 每个组件、数据存储和协议层的唯一职责是什么；
3. 一个请求、事件、取消、副作用和恢复如何端到端闭合；
4. G0 设计冻结是否已经消除关键歧义；
5. 当前代码距离 CORE/INTEROP/EXTENDED 分别还有多远。

本文只做综合解释和评估。字段、状态、Key、API、Subject、超时、错误和测试的唯一权威仍是对应专项。若本文与专项不一致，以专项为准。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.6 | 2026-08-14 | 首次发布与 V1.6/V1.2 设计基线对应的全量综合分析、G0 关闭评估和实施差距 |

判定维度分开计算：

| 维度 | 当前结论 |
|---|---|
| 业务能力覆盖 | 目标设计覆盖完整 |
| 架构边界清晰度 | G0 候选；关闭复审待完成 |
| 实现合同精度 | 首轮问题已修订，待独立复核 |
| 代码实现程度 | 原型/部分原语 |
| 官方 A2A 兼容证据 | 未完成 |
| 三机故障闭环证据 | 未完成 |
| 生产发布就绪 | 未就绪 |

---

## 2. 最新文档基线

| 专项 | 版本 | 在整体架构中的角色 | G0 主要新增 |
|---|---:|---|---|
| 业务与总体架构 | V1.6 | 全局边界、拓扑、剖面和权威流 | durable dispatch/cancel、共同恢复点、操作矩阵 |
| Agent Card 与协议对象 | V1.6 | 标准对象和公开发现合同 | public/internal Card 分离、完整状态矩阵、replay 延后 |
| A2A 与 NATS | V1.6 | NAT 友好东西向 Binding | dispatch Worker、Relay claim/HOL、stream session/controller/provisioner、ACL、schema version |
| Redis 状态平面 | V1.6 | 唯一稳定状态与原子提交平面 | dispatch/replay/Plan/workspace/effect/recovery/stream-session Keys |
| Task 生命周期 | V1.6 | 长任务、取消、订阅和恢复 | 全迁移矩阵、取消线性化、heartbeat 采样、retry 规则 |
| 编排/Runtime/Tool | V1.6 | 多 Agent 编排和受控执行 | 持久 Plan、DRR、workspace fencing、Runtime containment |
| 接口标准 | V1.6 | 公开 JSON-RPC/gRPC/SSE/MCP 映射 | 11 操作矩阵、固定入口顺序、基础设施错误映射 |
| Artifact/Object Store | V1.2 | 大对象 saga 和授权 | stable URI、finalize/delete/ticket 竞态、owner tombstone |
| 受信配置 | V1.2 | 信任根、generation 和运行策略 | hash/JWS、genesis、可信READY、无自引用GateEvidenceRecord、stable publisher rollout |
| 人工对账 | V1.2 | UNKNOWN effect 的运营闭环 | 正交状态、resolution history、stale APPLYING scanner |
| 统计/审计/监控 | V1.6 | 可观测、审计和恢复证明 | WORM Audit Sink、dispatch/outbox/effect/DR 告警 |

旧 V1.5/V1.1 活动集整体归档，不再作为实现合同。

---

## 3. 最新目标架构总览

最新架构不是“公网 Gateway 调两个 Windows Agent”的主从系统，而是一个**对称执行、中心化可信状态和公网协议适配**的单 Mesh：

- Linux 是唯一公网部署节点，但不是固定主 Agent；
- Linux 与两个 NAT 后 Windows Peer 都是对等 Agent，可通过 NATS 调用任意目标；
- Gateway 只承担外部 A2A/MCP 协议适配，不进入 Peer-to-Peer 东西向必经路径；
- NATS/JetStream 是出网友好的命令与事件数据平面；
- Redis State Service 是 Task/Card/Principal/dedupe/lease/dispatch/outbox/effect/Plan/config/reconciliation/stream-session 的唯一稳定状态权威；
- Object Store 是 Artifact blob 唯一权威；
- Config Controller 管理签名 generation，Audit Sink 保存不可变操作历史；
- Runtime/Tool 只有在被证明无法绕过 broker 时才能进入 MEDIATED 副作用路径。

[查看最新架构图](../assets/A2AMesh_V1.6_Architecture.html) · [SVG](../assets/A2AMesh_V1.6_Architecture.svg)

架构图显式区分**目标部署能力**与**当前可执行证据**：`RequiredSlotSetV1`和`ArtifactHoldExpiryCASState`已形成确定性纯状态合同；图中的Redis Function/Lua、真实NATS handler/AuthProof、持久化、多进程故障注入及生产一致性仍是目标能力，不得由纯合同测试推导为已实现。

### 3.1 R11当前证据分层

当前R11仍是未冻结dirty候选，先前tree指纹已经失效。已通过的局部门禁为：Artifact Hold纯合同`85 passed`（含State-side SCAN allocation proof、SCAN authority seal、REPLAY current-authority pointer及重绑拒绝）、架构资产allowlist `9 passed`、强制真实Chromium gate `1 passed`。这些结果分别属于纯合同和browser资产证据；它们不构成真实Redis restart durability、NATS TLS/NKey/AuthProof/ACL、Windows NAT、多进程恢复、Object Store、官方A2A黑盒或生产就绪证据。全量release门禁、最终tree冻结和二次独立复审完成前，整体结论仍为`private A2A-inspired NATS prototype`。

### 3.2 五层逻辑模型

#### 第一层：外部协议面

- A2A JSON-RPC/SSE：CORE 必交付；
- A2A gRPC/Push：INTEROP 才启用；
- MCP Streamable HTTP/stdio：EXTENDED，单独发现，不伪装成 A2A Binding；
- ops API/CLI：仅管理网络和独立机器 Credential。

#### 第二层：Canonical Application Core

Core复用官方A2A对象，提供11个操作的唯一语义实现。JSON-RPC、gRPC、NATS和MCP Bridge都是Adapter，不允许各自拥有第二套状态机、错误或幂等逻辑。Peer节点上的Application Core是独立进程/NKey，Peer Binding只通过受保护本地IPC转交canonical envelope和verified credential observation，不能借Binding NKey直接做Task mutation。

入口顺序固定为：

```text
TLS/Host/Content-Type/结构
→ A2A/Binding version
→ tenant 必须为空
→ Credential/AuthProof
→ Canonical Principal + immutable alias
→ ownership/capability
→ size/admission
→ State claim/mutation
```

#### 第三层：命令与事件数据面

- `DATA-DISPATCH-001` 保证 Task 受理后最终 dispatch 或确定失败；
- NATS target RPC queue 保证一个在线 target instance 接收；
- Redis event outbox + Event Relay + JetStream 保证已提交事件可重投；
- 流式 NATS RPC 只回一个 `StreamSessionOpenedV1`；Stream Session Controller 以 State session + fixed filtered consumer 向 caller 私有 delivery subject 发送后续 committed frame，JS Provisioner 是唯一 Consumer API 身份；
- SSE/Push/Observer 是独立消费者，互不阻塞。

#### 第四层：可信状态与控制面

Redis/State 承担：

- Principal/Credential/Alias/Auth replay；
- Task/Context/Card/presence/dedupe/lease；
- dispatch/outbox/admission；
- Plan/Step/workspace lease；
- effect ledger/reconciliation case；
- Artifact metadata；
- config bundle/READY/active pointer；
- audit outbox/Recovery Manifest。

Config、Artifact、Reconciliation、Audit 在实施计划 C2.5 先形成最小垂直闭环，避免 C4 反向依赖 C5/C7。

#### 第五层：Peer 执行面

每个 Peer 包含：

- 使用独立NKey的NATS Peer Binding endpoint；
- 使用另一独立NKey的Application Core；
- 使用独立NKey并直接订阅DispatchTask的TaskSupervisor；
- 使用另一独立 NKey 的 Orchestrator；
- ProcessExecutor；
- Runtime Adapter；
- Tool Registry/MCP Client；
- SideEffectAdapter；
- workspace resolver/lease client；
- Artifact client；
- heartbeat/presence。

Peer 不直接写 Redis、不直接发布权威 Task event、不自批 reconciliation、不接受远程绝对路径/argv/env。

---

## 4. 核心端到端链路

### 4.1 SendMessage 与 durable dispatch

```text
Caller
→ Gateway/Peer NATS Binding验证协议与身份，并经受保护本地IPC调用Application Core
→ Application Core → State.claim_message
   ├─ dedupeKey = principal + target + messageId
   ├─ 独立保存 payloadHash
   ├─ 创建 SUBMITTED Task/索引/admission reservation
   ├─ 写 event outbox
   └─ 写 dispatch intent
→ 返回 Task handle
→ Dispatch Worker claim intent
→ NATS private DispatchTask
→ Task Supervisor command.get/acquire_lease/accept_dispatch
→ Task SUBMITTED → WORKING
```

关键改进是：Core 在 NATS publish 前崩溃不再形成永久 SUBMITTED 黑洞。dispatch intent 有 claim、重试、ACCEPTED、deadline 和 DEAD/FAILED 语义。

### 4.2 状态事件

```text
Application Core / Task Supervisor
→ State expectedVersion + lease/fencing mutation
→ Redis Task snapshot + per-Task eventSequence + outbox 原子提交
→ Event Relay 只认领每个 Task 的 head event
→ JetStream PubAck
→ 完成 outbox
→ SSE / Push / Observer / Projector 独立消费
```

权威顺序是“Redis 先提交、JetStream 后发布”。Relay 多实例可接管，但 `n+1` 不能越过失败/退避中的 `n`。

### 4.3 Cancel

```text
CancelTask
→ State CAS cancelRequested=true
→ control Subject（低延迟提示，可丢）
→ Supervisor heartbeat/lease 周期再次读取 Redis fact
→ 终止进程树
→ 检查 effect ledger
   ├─ 无未知/不可逆 effect：CANCELED
   └─ APPLIED/UNKNOWN 未闭合：FAILED + reconciliation_required
```

Cancel 与 complete 使用同一个 expectedVersion CAS 线性化。CANCELED 重复 Cancel 返回当前 Task，COMPLETED/FAILED/REJECTED 返回 `TaskNotCancelableError`。

### 4.4 副作用与人工对账

```text
adapter.prepare(effectIntentId/providerKey)
→ State.begin_effect → PREPARED/effectAttemptId
→ lease/fencing 检查
→ APPLYING
→ provider call
→ APPLIED / FAILED / UNKNOWN
```

如果进程在 provider 成功后崩溃，Effect Reconciler 会把“owner lease 已失效且超策略时间”的 APPLYING 原子转 UNKNOWN，并创建唯一 OPEN case。操作员通过 Evidence、claim fencing 和 resolution 处理；旧 Task 终态和历史 resolution 永不覆盖。

### 4.5 Artifact saga

```text
create upload session
→ signed PUT 临时对象
→ 服务端 HEAD/checksum/scan
→ copy-if-absent 正式 object key
→ State.finalize_artifact CAS
→ AVAILABLE metadata + Task Artifact + event outbox
```

稳定身份是 `a2amesh://artifacts/<artifactId>`，不是 signed URL。Redis 与 Object Store 不伪装成跨系统事务：允许可对账 orphan，不允许 Task 引用未验证 blob。finalize/Task terminal/delete/download-ticket 通过 Task/Artifact version 和 fencing 线性化。

有限期hold由独立Artifact Hold Reaper经closed `SCAN|EXPIRE|REPLAY_CLAIM`调用State。当前`ArtifactHoldExpiryCASState`已可执行验证全局candidate authority、`ACTIVE→CONSUMED` tombstone、`ACTIVE→EXPIRED`、immutable commit以及audit/outbox一一对应的单CAS write-set；terminal higher-fence replay必须先由唯一claim writer持久化exact claim request/result和current-authority，令`baseCommitDigest`逐字节绑定基准commit、`authorizedCandidateDigest`绑定原consumed candidate，并签发未消费且lease有效的新candidate。未过期current claim阻止新claim ID，higher claim持久化后所有低fence及原commit authority永久superseded，不能靠裸整数变大取得权限。它仍不是Redis Function、真实NATS ingress或Object Store删除集成。物理删除继续只属于使用不同Principal/NKey及provider credential的Artifact Delete Worker。

### 4.6 受信配置

```text
离线信任根/bootstrap
→ signed bundle validate
→ immutable generation stage
→ 确定性render ACL并冻结bundleContentSha256+aclDigest
→ 组件签名 READY/NACK
→ required tests输出PASS/0-skip不可变报告
→ 独立签名/stage GateEvidenceRecord（报告hash+readySetDigest）
→ 以evidenceSha256执行active pointer CAS
→ 新请求只使用新 generation
```

首次部署使用一次性genesis：只有空State、未使用nonce、双人批准签名时可激活g1；Gateway/Runtime/Artifact/dispatch在g1 ACTIVE前关闭。普通generation的bundle只声明requiredGateTestIds，报告只绑定已stage的bundle/ACL，GateEvidenceRecord在报告之后签发，因此没有bundle↔report摘要环。滚动窗口只允许旧实例完成固化in-flight，不形成两个可接收新操作的active generation。

当前`config_slots.py`已可执行验证`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`的稳定投影、全局Principal/NKey隔离、READY覆盖及recovery投影一致性；它不等于Config Controller、signed READY/NACK ingress、GateEvidence持久化或active pointer Redis CAS已经实现。

### 4.7 共同灾难恢复

独立备份时间接近不等于可恢复。`DATA-RECOVERY-001` 记录：

- config/trust-root generation；
- Redis backup ID；
- JetStream stream/consumer checkpoint；
- Object inventory/version watermark；
- Artifact metadata/delete journal；
- Audit Sink checkpoint/hash manifest。

签名 Manifest 正文的权威副本位于独立 WORM/异机恢复目录，Redis 仅保存可重建索引；因此 Redis 全损时可先验证外部 Manifest 再按水位恢复。任一水位缺失或不一致，系统只能开放最小运维修复和安全只读，不得开放新 Task、新 effect、新 Artifact upload。

---

## 5. 数据权威与一致性模型

| 数据 | 唯一权威 | 复制/投影 | 一致性原则 |
|---|---|---|---|
| Task/Context/lease/dedupe | Redis State | JetStream/SSE/Push/Projector | expectedVersion + fencing + outbox |
| 执行命令 | Redis dispatch intent | NATS request | at-least-once delivery + accept/lease dedupe |
| Task event | Redis outbox/eventSequence | JetStream | per-Task HOL + PubAck |
| Agent Card | Redis verified Card | Gateway cache | stable publisher lease/fencing |
| presence | Redis per-instance TTL | aggregated Agent state | heartbeat 不改 Card ETag |
| Artifact blob | Object Store | backup/inventory | checksum + Redis metadata saga |
| Effect result | Redis effect ledger | case/audit | evidence-based UNKNOWN resolution |
| Config | signed immutable bundle + active pointer | local verified cache | staged index + active CAS |
| Audit | append-only/WORM sink | logs/metrics summaries | deterministic event ID + hash manifest |
| Recovery readiness | Recovery Manifest | dashboards | all checkpoint watermarks required |

V1 选择单 Mesh/单 Redis hash slot，换取多 Key Lua 原子性。它不是水平扩展/跨区域 HA 设计；容量超出时应进入 V2 分片，而不是在 V1 中加入半完成 Redis Cluster。

---

## 6. 协议与互操作分析

### 6.1 A2A 标准面

- A2A v1.0.1 官方对象和错误是唯一标准；
- CORE 提供 JSON-RPC/SSE；
- INTEROP 增加 gRPC/Push；
- 11 个操作始终有确定处理：未启用 Push/Extended 时返回标准不支持，而不是方法不存在或虚假 advertise；
- public Agent Card 不发布私有 NATS route、NKey 或内部 Subject。

### 6.2 内部 NATS Binding

NATS 是 A2A 自定义 Binding，不宣称官方标准传输。Envelope 分离：

- `bindingUri`；
- `bindingSchemaVersion`；
- `a2aProtocolVersion`。

内部 schema major 不同直接拒绝，minor 支持 N/N-1。ACL 允许 Peer publish 获授权 target RPC 和自身私有 reply/presence；只有 Event Relay 可 publish `a2a.v1.events.*`。

### 6.3 MCP

MCP 属于 EXTENDED：

- Server Bridge 映射 bounded tools/resources 到同一 Core；
- OAuth 2.1 使用外部 AS，A2AMesh 是 Resource Server；
- required messageId 与 State dedupe；
- Token 不向下游透传；
- Windows NAT Peer 不开放 MCP 入站。

当前仓库已有有限 `mcp_bridge/server.py` 原型，但仍使用私有对象和进程内 dedupe，因此不是 EXTENDED 实现证据。

---

## 7. 安全架构分析

### 7.1 身份

- JSON-RPC/SSE：每客户端 opaque Bearer → `a2a:<credentialId>`；
- NATS：NKey + signed AuthContext → `agent:<agentId>`；
- MCP：OAuth issuer/client_id → `mcp:<issuerHash>:<clientId>`；
- alias 显式、单跳、不可改指；
- Task owner 固化为 Canonical Principal，历史 owner 不随 Credential rotation 改写。

AuthProof 绑定 signer、method、subject、target、requestId、deadline、replySubject 和 config generation；replay 使用 Redis TTL Key，不能依赖单进程内存。

### 7.2 能力和最小权限

V1 不建设用户/RBAC，但保留部署级 capability：Principal、target Agent、operation/skill、Tool risk、workspace alias、generation/expiry 全维匹配。查询不存在和无权访问继续 no-leak。

### 7.3 Runtime containment

架构不再笼统宣称“包装了 SideEffectAdapter 就控制全部副作用”：

| 等级 | 设计语义 |
|---|---|
| MEDIATED | OS/workspace/egress/Tool broker 证明不能绕过；可进入 ledger 和受控重试 |
| SANDBOXED_READ_ONLY | 只读分析/测试，不宣称外部 effect 受控 |
| UNMEDIATED | 可直接 shell/网络/写入；禁止远程高风险和自动 effect retry |

这是当前实现风险最大的安全门禁之一，需要真实 OS sandbox、Windows Job Object、egress 和 workspace fixture，而不是只做 Python 类型检查。

---

## 8. 可用性、容量和可观测分析

- Task heartbeat snapshot 默认 5 秒；纯 heartbeat event 最多 30 秒采样；
- admission 有全局/Principal 上限、queue deadline 和大小限制；
- 公平算法固定 Deficit Round Robin，cost=1、weight 1～16、两轮饥饿上限；
- Metrics 不使用 Principal、Task、Artifact、Case、动态 consumer 作为 label；
- Audit Relay 投递到独立 WORM/append-only sink；Redis Stream/日志不承担 365 天唯一权威；
- dispatch、outbox HOL、stale APPLYING、workspace fencing、audit lag、Recovery Manifest 均有 P1 告警和 Runbook；
- queue 告警按实际 deadline 80%/过期触发，不再与 120 秒 deadline 使用矛盾的固定 5 分钟阈值。

系统仍有明确的 Linux 单点。V1 的可用性策略是 fail closed + 可验证恢复，不是伪装 HA。

---

## 9. G0 关闭评估

| 风险 | V1.5/V1.1 问题 | V1.6/V1.2 冻结结果 | 状态 |
|---|---|---|---|
| Task 返回后 dispatch 丢失 | Task 与命令投递分离无恢复 | durable intent + Worker/accept/deadline | 候选合同已补齐，待关闭复审 |
| Cancel publish 丢失 | control message 是唯一触发 | Redis fact + heartbeat/接管复查 | 候选合同已补齐，待关闭复审 |
| 多 Relay 越序 | 无 claim/HOL | claim lease + per-Task head | 候选合同已补齐，待关闭复审 |
| provider 成功后 APPLYING 卡死 | 只有显式 UNKNOWN | stale scanner + unique case | 候选合同已补齐，待关闭复审 |
| Cancel/complete/retry 歧义 | 状态图不完整 | 全迁移矩阵和 CAS 线性化 | 候选合同已补齐，待关闭复审 |
| 编排重启丢 Plan | 内存 Tracker | DATA-PLAN-001 | 候选合同已补齐，待关闭复审 |
| 多实例 replay | 进程内 seen set | Redis replay Key | 候选合同已补齐，待关闭复审 |
| 版本混用 | protocolVersion 承担两层含义 | Binding schema/A2A version 分离 | 候选合同已补齐，待关闭复审 |
| public Card 泄露 NATS | 示例直接发布 route | public/internal Card 分离 | 候选合同已补齐，待关闭复审 |
| Artifact 生命周期竞态 | finalize/delete/retention 不闭合 | version CAS、stable URI、owner tombstone | 候选合同已补齐，待关闭复审 |
| Config bootstrap/hash | 自引用、READY 信任和 genesis 未定义 | exact hash/JWS、一次性 genesis、signed READY | 候选合同已补齐，待关闭复审 |
| Case 状态混杂 | CLAIMED/ESCALATED 与业务状态互斥 | workflow/claim/escalation 正交 | 候选合同已补齐，待关闭复审 |
| Runtime 可绕过 ledger | 无 containment 证明 | 三等级和 MEDIATED 门禁 | 候选合同已补齐，待关闭复审；实施风险高 |
| 跨存储恢复 | 只有相近时间备份 | Recovery Manifest/delete journal | 候选合同已补齐，待关闭复审；实施风险高 |
| 长期审计权威 | Redis/日志职责模糊 | Audit outbox + WORM sink | 候选合同已补齐，待关闭复审 |

因此当前只能判定为 G0 候选；原 P0/P1 的关闭复审和评审台账完成后，才可判定设计冻结。即使通过，“设计关闭”也不是“测试通过”。

---

## 10. 当前代码事实与目标差距

经仓库现状核对，当前已有：

- 私有 Pydantic AgentCard/Task/Message；
- 私有 NATS request/reply、部分 send/stream/get/cancel；
- Runtime Executor 和多个 CLI Adapter 原型；
- Planner/Dispatcher/Tracker/Aggregator 基础代码；
- Identity/Credential/Alias/AuthContext 原语及测试；
- `config_slots.py`中的确定性`RequiredSlotSetV1`投影、全局身份隔离与recovery投影纯合同；
- `state_contracts/artifact_hold.py`中的严格SCAN/EXPIRE/REPLAY_CLAIM wire、candidate authority ledger、CONSUMED tombstone、commit-bound replay claim及单CAS write-set纯合同；
- MCP Client 与有限 MCP Server Bridge 原型。

当前关键缺口：

1. 官方对象和 11 操作 Canonical Core；
2. Redis State Service 和全部原子 Key/函数；
3. durable dispatch、ordered outbox、Event Relay；
4. distributed replay、lease/fencing、Plan/workspace；
5. Config/Artifact纯合同到Redis/NATS/持久化集成，以及Reconciliation/Audit/Recovery C2.5；
6. Peer Binding/Application Core/TaskSupervisor/Orchestrator独立Principal与ACL，以及真实Runtime containment；
7. JSON-RPC/SSE Gateway 与官方黑盒；
8. gRPC/Push、MCP OAuth/Observer 独立门禁；
9. 三机故障注入和生产部署证据。

特别说明：当前 `identity/auth_context.py` 的 replay 是进程内 `_seen`，当前 `mcp_bridge/server.py` 的 submission dedupe 也是进程内映射；它们是迁移输入，不满足多实例 State 权威合同。`RequiredSlotSetV1`和`ArtifactHoldExpiryCASState`虽有可执行测试，但只证明确定性纯状态语义，不能作为Redis/NATS部署、持久化或生产就绪证据。

---

## 11. 推荐实施顺序

```text
G0 候选修订与关闭复审（进行中）
→ C0 官方 fixture/CI
→ C1 Canonical Core
→ C2 Redis State primitives
→ C2.5 Config/Artifact/Reconciliation/Audit/Recovery 最小垂直切片
→ C3 NATS Binding + Application Core State identity + Dispatch Worker + Event Relay + Stream Session Controller + JS Provisioner
→ C4 Supervisor/Plan/Runtime containment/SideEffect
→ C5 CORE JSON-RPC/SSE + 官方黑盒
├→ C7-CORE → C8-CORE
├→ C6-I gRPC/Push/额外 Runtime → C7-INTEROP → C8-INTEROP
└→ C6-I + C6-E MCP/OAuth/Observer → C7-EXTENDED → C8-EXTENDED
```

不建议跳过 C2.5 直接写 C4，因为 C4 的 effect、Artifact、case、config 和 audit 门禁会再次落入内存/假实现。

---

## 12. 剩余架构风险

这些不是 G0 歧义，而是必须用实现证据消除的风险：

| 风险 | 需要的证据 |
|---|---|
| 官方 SDK 1.1.2 对 11 操作和错误的真实行为 | 独立环境 fixture/black-box，不导入项目 client |
| NATS ACL/JetStream 在真实 server 的权限语义 | TLS/NKey account fixture 和负例 |
| Redis 单 slot 容量上限 | 目标负载压测、内存和 Lua latency |
| Object Store checksum/version/delete marker 差异 | 选定 provider fixture 和故障注入 |
| Runtime containment 是否真能阻止绕过 | Linux/Windows OS 级 sandbox/egress/workspace 测试 |
| provider idempotency/reconciliation 质量 | 每个 SideEffectAdapter 的 provider-specific contract |
| WORM Audit Sink 和删除 journal 成本/可恢复性 | 隔离恢复演练、hash chain 验证 |
| 单 Linux 节点故障 | 15 分钟服务恢复、4 小时整机恢复和 15 分钟 RPO 演练 |

---

## 13. 最终结论

A2AMesh V1.6/V1.2 已从“组件齐全的架构蓝图”提升为“关键故障窗口有唯一处理方式的实现级设计基线”：

- 对称 A2A Agent Mesh 的方向保持不变；
- 公网 Gateway 与内部 NATS Binding 的边界更清晰；
- Redis 不只是 Task 数据库，而是命令、状态、幂等、lease、Plan、effect 和控制面事实的原子提交中心；
- fire-and-forget 正确性路径已被 durable intent/outbox/fact 取代；
- Artifact、Config、Reconciliation、Audit 和 DR 已形成可实施控制面；
- Runtime 副作用安全从“Adapter 约定”升级为“必须证明 containment”；
- 实施计划已消除阶段反向依赖；C6-I/C6-E 可并行实现，但剖面声明保持 EXTENDED⊃INTEROP⊃CORE。

当前准确状态是：

> **G0 design candidate under closure review; implementation and conformance not verified.**

只有 C0～C5（含 C2.5）、C7-CORE/C8-CORE、官方 JSON-RPC 黑盒和至少 Linux + 1 NAT Peer 真机通过后，才能声明 CORE；INTEROP 还需 C6-I、C7/C8 INTEROP；EXTENDED 必须累积满足 CORE+C6-I+C6-E及 C7/C8 EXTENDED，不能由 C6-E 独立声明。

---

## 14. 参考依据

- [当前设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [Agent Card 与协议对象规范 V1.6](A2AMesh_AgentCard与协议对象规范_V1.6.md)
- [A2A 协议与 NATS 集成适配设计 V1.6](A2AMesh_A2A协议与NATS集成适配设计_V1.6.md)
- [Redis 状态平面与数据设计 V1.6](A2AMesh_Redis状态平面与数据设计_V1.6.md)
- [任务生命周期与长任务运行时设计 V1.6](A2AMesh_任务生命周期与长任务运行时设计_V1.6.md)
- [编排器、Runtime 与工具适配设计 V1.6](A2AMesh_编排器_Runtime与工具适配设计_V1.6.md)
- [接口请求与响应标准 V1.6](A2AMesh_接口请求与响应标准_V1.6.md)
- [Artifact 与对象存储设计 V1.2](A2AMesh_Artifact与对象存储设计_V1.2.md)
- [受信配置与变更治理设计 V1.2](A2AMesh_受信配置与变更治理设计_V1.2.md)
- [人工对账与运维操作设计 V1.2](A2AMesh_人工对账与运维操作设计_V1.2.md)
- [统计、审计与运行监控规则 V1.6](A2AMesh_统计审计与运行监控规则_V1.6.md)
- [开发实施计划](A2AMesh_开发实施计划.md)
