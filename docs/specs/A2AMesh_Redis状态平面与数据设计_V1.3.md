# A2AMesh Redis 状态平面与数据设计 V1.3
> 文档ID：`A2AM-DATA-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：Redis Key、原子函数、索引、租约、幂等与保留
> 目标读者：后端、数据、测试、运维
> 评审状态：文档自检通过；Lua/恢复/并发验收待完成
> 最后更新：2026-08-14
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 替代版本：V1.2
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh V1 的 Redis 状态平面、State Service、Key Schema、索引、原子函数、租约、幂等、事件 outbox、副作用账本、能力授权、准入控制、Push 配置、保留、备份和故障恢复规则。

Redis 只保存可查询的热状态和控制数据。JetStream 是实时事件日志；大 Artifact 使用对象存储；Peer 不直接访问 Redis。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立单 Mesh Redis Key、Task/Card/幂等/租约/Push 数据契约和 Lua 原子规则 |
| V1.1 | 2026-08-14 | 落地DATA标识、Agent发现查询模型和对应验收ID |
| V1.2 | 2026-08-14 | 补齐Principal Registry/Alias、Credential索引和跨协议幂等数据契约 |
| V1.3 | 2026-08-14 | 增加原子事件outbox、副作用账本、能力授权、队列准入与恢复目标数据契约 |

## 1.2 实施状态

当前代码尚无 Redis State Service；本文件全部为目标设计。现有进程内 `_tasks/_task_states` 和 KV 辅助状态不得作为 V1 持久化实现继续扩展。

---

# 2. 设计原则

1. Redis 只绑定公网 Linux 的 loopback/容器私网。
2. Gateway/Peer 通过认证 NATS State RPC 访问 State Service。
3. V1 单 Mesh，以配置 `mesh_id` 作为部署命名空间，不建设 tenant。
4. Key 前缀和对象 schema 版本化：`a2am:v1:{<meshId>}:...`。
5. 多 Key 状态迁移使用 Lua/Redis Function；禁止客户端多步“先读后写”。
6. Redis 不保存 JetStream 的第二份完整事件日志。
7. Redis 不保存大 Artifact、完整 stdout、思维链或凭据明文。
8. Task ID 由服务端生成；幂等通过 caller/target/messageId/payloadHash。
9. Lease 使用 fencing token；过期 owner 永远不能晚写。
10. `maxmemory-policy noeviction`；协议状态不能被静默逐出。
11. 每次 Task mutation 在同一原子函数内更新快照/索引并写 outbox；Event Relay 只发布已提交事实。
12. 副作用执行状态持久化；`UNKNOWN` 未完成 provider/local reconciliation 前不得自动重试。
13. capability grant 和 admission counter 由受信配置/服务维护，业务请求不能自报授权结果。

---

# 3. 拓扑

```text
Gateway / Application Core ─┐
Peer / Relay / Push ─────────┼─ NATS State RPC ─▶ State Service ─▶ Redis
Projector / Admin Probe ─────┘                         │
                                                       └─ Lua/Functions
```

State Service 从认证 NATS 身份派生 principal/agent，不接受 payload 覆盖。Redis 密码、ACL 用户和 TLS 配置只存在于 Linux 内部环境。

---

# 4. 数据权威边界

| 数据 | Redis | JetStream | 其他 |
|---|---|---|---|
| Agent Card 最新快照/ETag/索引 | 权威 | change event | Git/配置为输入 |
| Presence | 权威最新时间 | heartbeat 输入 | NATS connection 辅助 |
| Task 最新快照/列表索引 | 权威 | 可重建窗口 | — |
| 任务有序事件 | 只存 eventSeq 水位 | 权威短期日志 | 长期审计存日志系统 |
| 待发布事件 | 短期 outbox，权威投递待办 | PubAck 后进入事件日志 | 不对业务查询开放 |
| Message 幂等 | 权威 | 不负责 | — |
| Task lease/fencing | 权威 | 不负责 | — |
| 外部副作用状态/回执摘要 | 权威 ledger | 只发状态变化事件 | provider 为对账来源 |
| Capability grant | 权威当前配置 | 可发配置变更事件 | 配置仓为输入 |
| Admission counter/queue | 权威短期控制状态 | 可发过载审计事件 | — |
| Push 配置/投递状态 | 权威 | delivery event | 凭据应用层加密 |
| 大 Artifact | URI/hash/size | 可带引用 | Object Store |
| Runtime stdout | 可选最后摘要 | 限量进度事件 | 日志系统 |
| NKey seed/Token | 禁止 | 禁止 | Secret Store |

---

# 5. Key Schema

`{default}` 是 Redis hash tag 示例，实际由受信配置 `mesh_id` 生成。

## 5.1 DATA-CARD-001：Agent Card、Registry 与 Presence

| Key | 类型 | 字段/值 | TTL |
|---|---|---|---|
| `a2am:v1:{default}:card:<agentId>:public` | STRING | canonical AgentCard ProtoJSON | 无短 TTL |
| `...:card:<agentId>:extended` | STRING | 加密/受控 Extended Card | 无短 TTL |
| `...:card:<agentId>:meta` | HASH | version,etag,generation,updatedMs,instanceId | 无短 TTL |
| `...:agents:presence` | ZSET | member=agentId, score=lastSeenMs | 通过 score 判过期 |
| `...:agent:<agentId>:instances` | HASH | instanceId → heartbeat JSON | 90 秒 |
| `...:idx:skill:<tag>` | SET | agentId | 随 Card 更新 |
| `...:idx:binding:<bindingHash>` | SET | agentId | 随 Card 更新 |

Card 不随 heartbeat 重写。Agent offline 不删除 Card，只从在线查询中过滤；长期退役通过明确 unregister/tombstone。

Registry 查询规则：

- `GetAgentCard` 按 agentId 直接读取 Card/meta，并联查 presence；
- `ListAgents` 以 `agentId ASC` 排序，使用签名游标，pageSize 默认 50、最大 200；
- `SearchAgents` 先对 skill/binding 集合求交集，再按 runtime/presence/capacity 过滤；filtersHash 写入游标；
- `onlineOnly=true` 使用 presence score 与多 instance 记录判定，不能通过 Card 更新时间推断在线；
- unregister 写 Card meta tombstone 并从 skill/binding 索引移除，保留 30 天后清理；
- Gateway Host 路由的 `agentId` 必须先做格式校验，再调用 `GetAgentCard`；不存在与 tombstone 使用相同外部 not-found 行为。

## 5.2 DATA-TASK-001：Task 与 Context

| Key | 类型 | 字段 |
|---|---|---|
| `...:task:<taskId>` | HASH | 见 5.3 |
| `...:tasks:updated` | ZSET | member=taskId, score=updatedMs |
| `...:tasks:state:<state>` | ZSET | member=taskId, score=updatedMs |
| `...:context:<contextId>:tasks` | ZSET | member=taskId, score=createdMs |
| `...:caller:<principal>:tasks` | ZSET | caller 可见 Task |
| `...:agent:<agentId>:tasks` | ZSET | target/owner Task |
| `...:task:<taskId>:artifacts` | HASH | artifactId → metadata/URI JSON |

## 5.3 Task HASH

```text
taskJson                canonical Task ProtoJSON
state                   official TaskState
contextId               canonical context ID
callerPrincipal         authenticated caller
callerAgentId           optional caller agent
targetAgentId            selected target
ownerAgentId            lease owner agent
ownerInstanceId         lease owner instance
createdMs / updatedMs   epoch milliseconds
version                 optimistic task version
eventSeq                latest committed event sequence
lastHeartbeatMs         TaskSupervisor heartbeat
phase                   A2AMesh progress phase
phaseSummary            redacted human summary
progressJson            compact structured progress
attempt                 execution attempt
leaseUntilMs            cached display field; lease key is authority
fencingToken            latest token
cancelRequested         internal boolean
terminalReason          redacted reason
reconciliationRequired  external effect requires manual/provider reconciliation
artifactCount           count
```

`taskJson` 是标准对象权威表示；冗余字段用于索引和 CAS，必须由同一原子函数更新。

## 5.4 DATA-DEDUPE-001：Message 幂等

```text
a2am:v1:{default}:dedupe:<callerHash>:<targetAgentId>:<messageId>
```

HASH：

```text
payloadHash
taskId
createdMs
expiresMs
```

TTL 与 Task 热保留期一致。`callerHash` 由认证 principal 规范化后哈希，不能使用未清洗原文构造 Key。

## 5.5 DATA-LEASE-001：Task Lease 与 Fencing

```text
a2am:v1:{default}:lease:task:<taskId>
```

STRING JSON 或紧凑编码：

```json
{
  "ownerAgentId": "windows-a",
  "ownerInstanceId": "instance-uuid",
  "fencingToken": 17,
  "attempt": 1
}
```

使用 `PX 30000`；默认每 10 秒续租。fencing token 来自单调计数：

```text
a2am:v1:{default}:fence:task:<taskId>  INCR
```

## 5.6 DATA-PUSH-001：Push Notification

| Key | 类型 | 内容 |
|---|---|---|
| `...:task:<taskId>:pushcfg` | SET | configId |
| `...:pushcfg:<taskId>:<configId>` | HASH | url,authType,encryptedCredential,status,createdMs |
| `...:pushdelivery:<deliveryId>` | HASH | taskId,eventSeq,attempt,status,nextRetryMs,lastError |
| `...:push:due` | ZSET | member=deliveryId, score=nextRetryMs |
| `...:push:dlq` | ZSET | failed delivery IDs |

Webhook credential 必须 envelope encryption；Redis 中不存解密密钥。

## 5.7 DATA-CURSOR-001：游标与限流

优先使用签名无状态游标：

```json
{"updatedMs": 1723600000000, "taskId": "task-...", "filtersHash": "..."}
```

HMAC 后 base64url。若必须服务端游标：

```text
a2am:v1:{default}:cursor:<opaque> STRING TTL 10m
```

部署级限流：

```text
a2am:v1:{default}:rate:<principalHash>:<operation>
```

V1 不做租户配额，只防误用和故障风暴。

## 5.8 DATA-PRINCIPAL-001：Principal、Credential 与 Alias

| Key | 类型 | 内容 |
|---|---|---|
| `...:principal:<principalHash>:meta` | HASH | principalId,type,status,createdMs,updatedMs |
| `...:principal:alias:<sourceHash>` | HASH | sourcePrincipal,targetPrincipal,generation,updatedMs |
| `...:credential:a2a:<credentialId>` | HASH | principalId,secretDigest,status,createdMs,expiresMs,rotationGroup |
| `...:credential:nkey:<publicKeyHash>` | HASH | agentId,principalId,status,generation |
| `...:oauth:issuer:<issuerHash>` | HASH | issuer,audience,requiredScope,jwksUri,status,configGeneration |
| `...:principal:<principalHash>:tasks` | ZSET | 调用者可见 Task，score=updatedMs |

规则：

- opaque A2A Token 格式包含可公开的 credentialId 与至少 256-bit 随机 secret；Redis 只存 `HMAC-SHA256(key=credentialPepper, message=secret)`，pepper 在 Secret Store；
- MCP 不保存 access token，只保存 issuer 配置；验证后的 `issuer + client_id` 计算初始 Principal；
- alias 只由受信部署配置写入，使用 generation CAS；source→target 一经用于 Task 所有权即不可改指，只能禁用 source 或新增 alias；禁止链式 alias 和环；
- Principal ID 原文不进入 Redis Key，Key 使用 SHA-256/base64url hash；Task HASH/审计保存规范化 Principal ID；
- Credential disable/expire 立即阻止新请求，但不改变历史 Task owner；轮换通过 rotationGroup 短暂允许新旧 credential 指向同一 Principal；
- tenant 不属于 Principal/Key；`mesh_id` 只生成 `{default}` 部署 hash tag。

## 5.9 DATA-OUTBOX-001：Task Event Outbox

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:outbox:event:<taskId>:<eventSeq>` | HASH | eventId,taskId,eventSeq,taskVersion,eventType,payloadJson,createdMs,status,publishAttempts,lastError | PubAck 后保留 1 小时或立即删除 |
| `...:outbox:due` | ZSET | member=`<taskId>:<eventSeq>`，score=nextAttemptMs | Relay 处理后移除 |
| `...:outbox:dead` | ZSET | 超过受控重试上限的 eventId | 人工恢复前保留 |

`eventId` 固定为 `<taskId>:<eventSeq>`，JetStream publish 使用同一确定性消息 ID。outbox 是投递恢复缓冲，不支持 Task 历史查询，不得保存第二份无限事件流。

## 5.10 DATA-EFFECT-001：外部副作用账本

| Key | 类型 | 内容 |
|---|---|---|
| `...:task:<taskId>:effects` | ZSET | member=effectId，score=preparedMs |
| `...:effect:<effectId>` | HASH | taskId,attempt,stepId,toolName,risk,state,idempotencyKey,providerRef,requestHash,receiptHash,preparedMs,updatedMs,lastError |

`state` 只能按以下状态机迁移：

```text
PREPARED → APPLYING → APPLIED
                    ↘ UNKNOWN
PREPARED/APPLYING/APPLIED → COMPENSATED
PREPARED/APPLYING → FAILED
UNKNOWN → APPLIED / COMPENSATED / FAILED  （仅对账后）
```

账本只保存脱敏 provider reference、request/receipt hash 和必要状态，不保存 Token 或完整第三方响应。`UNKNOWN` 必须阻止自动重试和 canceled 终态，直到 provider 查询或本地证据完成对账。

## 5.11 DATA-GRANT-001：Capability Grant

| Key | 类型 | 内容 |
|---|---|---|
| `...:grant:<grantId>` | HASH | principalHash,targetAgentId,operations,skills,toolRisks,workspaceAliases,status,generation,expiresMs |
| `...:principal:<principalHash>:grants` | SET | grantId |
| `...:grant:generation` | STRING | 当前受信配置 generation |

Grant 由受信配置生成并以 generation 原子替换。授权匹配必须同时满足 Principal、目标 Agent、operation/skill、Tool risk 和 workspace alias；缺失字段不表示通配。系统组件只拥有各自固定内部操作，不继承业务 caller grant。

## 5.12 DATA-ADMISSION-001：准入与公平排队

| Key | 类型 | 内容 | TTL |
|---|---|---|---|
| `...:admission:global` | HASH | queued,running,maxQueued,maxRunning | 活跃期间 |
| `...:admission:principal:<principalHash>` | HASH | queued,running,oldestQueuedMs | 空闲后短 TTL |
| `...:admission:queue` | ZSET | member=taskId，score=虚拟完成时间/入队序 | 出队移除 |
| `...:admission:task:<taskId>` | HASH | principalHash,enqueuedMs,queueDeadlineMs,sizeClass | 出队或超时后短 TTL |

准入函数必须在 dispatch 前校验全局/Principal 上限、queue deadline、请求/inline Artifact/context 大小和目标 Runtime 可用性。公平调度使用轮转或加权虚拟时间，禁止单一 Principal 长期占满队列。

---

# 6. 原子函数

## 6.1 `claim_message`

输入：

```text
verifiedAuth,targetAgentId,operation,skill,toolRisk,workspaceAlias,
messageId,payloadHash,requestSize,newTaskId,newContextId,nowMs,retentionMs,taskJson
```

行为：

1. 验证 AuthContext 并调用 `resolve_principal`；把最终 Principal 和 aliasGeneration 固化进 Task。
2. dedupe 已存在且 hash 相同：返回原 taskId 与当前 Task，`DUPLICATE_SAME`，不重复占用 admission。
3. dedupe 已存在且 hash 不同：返回 `DUPLICATE_CONFLICT`，不修改任何状态。
4. 新请求调用与 `authorize_capability`、`admit_task` 相同的规则，原子复核 grant generation、大小、全局/Principal 上限和 queue deadline。
5. 通过后创建 dedupe、Task、admission reservation、状态/时间/Context/Principal/Agent 索引，并以 `eventSeq=1` 写入 `TASK_SUBMITTED` outbox，返回 `CREATED`；任一步失败全部回滚。
6. newTaskId 必须由 State Service 生成，调用方不可指定。

### 6.1.1 `resolve_principal`

输入为已验证的 `authMethod + credentialId/NKey/issuer+clientId` 和配置 generation。函数读取 Credential、检查 status/expiry，再至多解析一个显式 alias；不存在、禁用、过期、alias 环或 generation 冲突均 fail closed。返回：

```text
principalId
principalType
credentialId（非 secret）
aliasGeneration
```

业务请求不得直接传 principalId 作为该函数输入。

## 6.2 `transition_task`

输入：

```text
taskId,expectedVersion,allowedFromStates,toState,
newTaskJson,eventSeq,nowMs,fencingToken,phase/progress
```

校验：

- Task 存在；
- version 匹配；
- from→to 合法；
- owner 写入时 fencing token 匹配；
- eventSeq 大于当前且不跳回；
- 终态不可迁出。

同一函数更新 Task HASH、state ZSET、updated ZSET 和 Context/Agent 索引，并写入 `<taskId>:<eventSeq>` outbox。若 outbox 或任一索引写入失败，整个 mutation 回滚；不得先发布 JetStream 再补写 Task。

## 6.3 `acquire_lease`

- Task 必须为 submitted/working/recoverable；
- lease 不存在或已过期；
- `INCR fence` 获取新 token；
- `SET lease PX ttl NX`；
- 更新 owner/attempt/version；
- 同一原子函数写 `TASK_OWNER_CHANGED` outbox；
- 返回 token 和新 version。

## 6.4 `renew_lease`

仅当 ownerAgentId、ownerInstanceId、fencingToken 全匹配时 `PEXPIRE`。失败后旧 Supervisor 必须立即停止副作用。

## 6.5 `request_cancel`

- 终态：返回当前 Task（幂等）或标准 not-cancelable 语义；
- 非终态：设置 `cancelRequested=1`、version+1；
- 同一原子函数写 `TASK_CANCEL_REQUESTED` outbox；
- 返回 owner instance，Core 再发 control message；
- 真正 canceled 由 Supervisor 确认进程退出后 transition。

## 6.6 `upsert_card`

- generation 小于等于当前时拒绝/幂等；
- 删除旧 skill/binding 索引；
- 写 Card、meta 和新索引；
- Card JSON 已由官方 SDK 验证；
- heartbeat 不调用本函数。

## 6.7 `begin_effect` / `complete_effect` / `reconcile_effect`

- `begin_effect` 在调用 provider 前创建 `PREPARED`，取得 provider idempotency key 后转 `APPLYING`；同 effectId/requestHash 重入返回现有记录，不重复调用；
- `complete_effect` 只能由持有有效 fencing token 的 owner 写 `APPLIED/FAILED/UNKNOWN` 和脱敏回执摘要；超时、连接断开或无法证明 provider 未执行时必须写 `UNKNOWN`；
- `reconcile_effect` 只接受 provider 查询结果、provider 幂等记录或本地不可变回执证据，把 `UNKNOWN` 转为 `APPLIED/COMPENSATED/FAILED`；
- effect 状态变化与 Task 的 `reconciliationRequired` 汇总字段、对应 outbox 必须原子更新；
- `UNKNOWN` 存在时，Task 不得自动开启新 attempt，也不得写 `CANCELED`。

## 6.8 `authorize_capability`

输入为服务端解析后的 Principal、targetAgentId、operation、skill、toolRisk、workspaceAlias 和 grantGeneration。函数读取有效 grant 并要求全部维度匹配；generation 漂移、过期、禁用或无匹配项统一拒绝，不产生 Task/outbox/队列副作用。

## 6.9 `admit_task` / `release_admission`

- 原子检查全局与 Principal 的 queued/running 上限和 queue deadline；
- 成功时写 admission task、计数和公平队列位置；失败不创建执行 attempt；
- queued 超限返回 caller-scoped overload，Runtime/State 不可用返回 service unavailable；
- Task 出队、取消、终态或超时必须幂等释放计数。

`claim_message` 对新 Task 内联执行同一套授权和 admission 规则；独立 `authorize_capability/admit_task` 只用于预检、已有 Task 的新 attempt 或编排子步骤，不能替代最终提交时的原子复核。

## 6.10 `project_derived_view`

- 以 taskId/eventSeq 去重，只更新可重建的消费水位、通知状态或统计视图；
- `taskVersion <= Redis Task.version` 时不得覆盖 Task JSON、state、phase、Artifact 或 terminalReason；
- 任何事件都不得把终态改回非终态；
- 发现 JetStream 事件领先 Redis committed eventSeq 时进入一致性告警和人工恢复，不能把事件当作无条件写回权威快照的命令。

---

# 7. ListTasks 与查询

## 7.1 过滤

支持：

```text
contextId
state
callerPrincipal（服务端注入）
targetAgentId
updatedAfter/updatedBefore
pageSize/pageToken
historyLength
includeArtifacts
```

V1 单 Mesh 下调用者只能查询自己发起、自己执行或由 Gateway 管理的 Task；这是资源归属检查，不建设 RBAC。

## 7.2 排序与游标

固定按 `(updatedMs DESC, taskId DESC)`。下一页使用最后一项的二元组；filtersHash 不一致时游标无效。pageSize 默认 50，最大 200。

## 7.3 一致性

列表直接读取 Redis 已提交权威快照，不依赖 Projector 才可见。JetStream/Projector 延迟只影响 SSE、Push、Observer 和派生统计，不得造成 Get/List 比已确认命令更旧；终态和 Artifact 元数据通过同步 State mutation 提交。

---

# 8. Presence

Peer 每 5 秒发送：

```json
{
  "agentId": "windows-a",
  "instanceId": "uuid",
  "startedAt": "...",
  "lastSeenAt": "...",
  "runtimes": {"hermes": "available", "codex": "unavailable"},
  "runningTasks": 2,
  "capacity": 4
}
```

State Service 使用认证身份覆盖 agentId。15 秒未更新为 suspect，30 秒为 offline。多实例 Agent 只要存在健康实例即可在线；调度必须选择具体 instance/queue group，不把 presence 当 lease。

---

# 9. 保留与清理

| 数据 | 默认热保留 | 后续 |
|---|---:|---|
| Task/Context 快照 | 7 天 | 可归档摘要后删除 |
| dedupe | 与 Task 相同 | 到期删除 |
| terminal Artifact metadata | 30 天 | Object Store 按独立策略 |
| Card | 直到 unregister | tombstone 30 天 |
| instance presence | 90 秒 | 自动过期 |
| Push config | Task 终态后 24 小时 | 删除凭据 |
| Push delivery/DLQ | 7 天 | 脱敏归档 |
| rate key | 1～10 分钟 | 自动过期 |
| delivered outbox | 0～1 小时 | PubAck 后删除或短留用于诊断 |
| dead outbox | 7 天 | 恢复/审计后删除 |
| side-effect ledger | 至少与 Task 审计同周期 | 脱敏冷归档 |
| capability grant | 配置有效期 | generation 替换后保留 24 小时回滚窗口 |
| admission task/counter | 排队/运行期间 | 终态后短 TTL 并对账清理 |

清理使用扫描器和小批量 Lua，不在 Redis 主线程执行大范围阻塞操作。

---

# 10. Redis 配置

MVP：

```conf
bind 127.0.0.1
protected-mode yes
appendonly yes
appendfsync everysec
maxmemory-policy noeviction
```

还需：

- ACL 独立 State Service 用户；
- 容器网络不映射 6379；
- AOF rewrite 监控；
- 定期 RDB 和异机加密备份；
- 配置磁盘水位和 stop-writes 告警；
- 生产禁用危险命令或限制管理员通道。

---

# 11. 故障与恢复

| 故障 | 行为 |
|---|---|
| Redis 短暂不可用 | 停止新提交/Card 更新/lease 和新副作用；不能绕过 State 直接发布“已提交”事件 |
| State Service 重启 | NATS queue group 接管；Lua 保证幂等 |
| Event Relay 重启 | 扫描 due outbox 重投；确定性 eventId + JetStream 去重窗口 + consumer eventSeq 去重 |
| Event Relay 长期不可用 | Task 快照仍权威，但 SSE/Push 延迟；优先允许取消/终态，outbox 超过容量/年龄阈值后停止新提交并返回 unavailable |
| Projector 重启 | durable consumer 从 ack 位点重放；只重建派生视图，不覆盖新 Task |
| AOF 丢失尾部 | 从备份/AOF 恢复并与 JetStream/副作用 ledger 对账；事件只能辅助发现缺口，不能无条件重建命令状态 |
| maxmemory | noeviction 导致写失败并 P1 告警，不能静默丢 Task |
| 双 Supervisor | lease/fencing 只允许新 owner 写；旧 owner 停止副作用 |
| 清理器误删 | terminal/dedupe TTL 一致；存在 active lease 时禁止清理 |

Redis 不可用时“继续接受新任务、稍后补写”会破坏幂等，必须 fail closed。

恢复目标：服务进程重启 RTO 15 分钟，完整单节点恢复 RTO 4 小时；受控进程/服务重启且持久卷完好时目标 State RPO 为 0，整机、磁盘或电源故障时 State/Event RPO 不超过 15 分钟。`appendfsync everysec` 不得被解释为突发掉电 RPO 0；Redis AOF/RDB、JetStream 持久目录和配置/Secret 元数据必须至少每 15 分钟形成异机加密恢复点，并定期演练跨组件一致性对账。

---

# 12. 数据迁移

Key schema 变更：

1. 新代码先支持读旧/写新；
2. 后台迁移带幂等 checkpoint；
3. 校验数量/hash/索引；
4. 切换读新；
5. 观察一个保留窗口；
6. 删除旧 Key。

禁止原地改变已发布字段语义。大版本使用 `a2am:v2:` 前缀。

---

# 13. 监控

至少暴露：

```text
redis_state_rpc_latency_seconds
redis_state_rpc_errors_total
redis_task_count{state}
redis_task_projection_lag_seconds
redis_outbox_due_count / redis_outbox_oldest_age_seconds
redis_outbox_publish_failures_total
redis_effect_count{state,risk}
redis_reconciliation_required_count
redis_capability_denied_total{reason}
redis_admission_queued / redis_admission_rejected_total{scope,reason}
redis_lease_renew_failures_total
redis_dedupe_hits_total{result}
redis_card_count
redis_presence_age_seconds{agent}
redis_push_due_count / redis_push_dlq_count
redis_used_memory_bytes / redis_evicted_keys / redis_aof_rewrite
```

`evicted_keys > 0` 为 P1 配置错误。

---

# 14. 验收用例

- **TEST-IDEMP-001 / DATA-DEDUPE-001**：同 messageId 同 payload 并发 100 次只生成一个 Task；不同 payload 全部冲突。
- **TEST-LEASE-001 / DATA-LEASE-001**：两实例竞争 lease 只有一个成功，旧 token 不能晚写，terminal 不被 late event 覆盖。
- **TEST-REGISTRY-001 / DATA-CARD-001**：Card generation、ETag、List/Search 游标、presence、tombstone 和索引替换正确。
- **TEST-IDENTITY-001 / DATA-PRINCIPAL-001**：Credential disable/rotation、NKey/OAuth 映射、alias CAS/环、伪造 Principal 和历史 owner 不变。
- **TEST-MCP-IDEMP-001 / DATA-DEDUPE-001**：MCP 同 messageId 重试复用 Task，跨 Binding alias 后也复用；不同 payload 冲突。
- **TEST-TENANT-001**：非空 tenant 在 `resolve_principal/claim_message` 前拒绝，Redis 不出现 tenant Key/字段。
- **TEST-LIST-001 / DATA-CURSOR-001**：ListTasks/Agents 在相同排序值下无重复或遗漏，filtersHash 不匹配时拒绝。
- **TEST-STREAM-001 / DATA-TASK-001**：Projector 重放同 eventSeq 不改变 Task version，旧事件和终态回放均不能覆盖权威快照。
- **TEST-RECOVERY-001**：Redis 重启后 Task/Card/dedupe/lease 语义符合预期。
- **TEST-SEC-001**：Redis 从公网和 Windows 不可达；凭据不以明文保存。
- **TEST-CAPACITY-001**：内存满时写失败并告警，不发生静默逐出。
- **TEST-OUTBOX-001 / DATA-OUTBOX-001**：mutation/outbox 原子；Relay 在 publish 前后崩溃均不丢事件，重复事件可去重。
- **TEST-EFFECT-001 / DATA-EFFECT-001**：effect 状态机合法，`UNKNOWN` 阻止自动重试/取消，只有对账证据可解除。
- **TEST-AUTHZ-001 / DATA-GRANT-001**：任一授权维度不匹配、grant 过期或 generation 漂移均在副作用前拒绝。
- **TEST-ADMISSION-001 / DATA-ADMISSION-001**：全局/Principal 上限、queue deadline、公平性和计数回收无泄漏。
- **TEST-DR-001**：隔离恢复演练满足 15 分钟/4 小时 RTO，整机、磁盘或电源故障的恢复点不超过 15 分钟。
---

# 15. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.3](A2AMesh_业务与总体架构设计_V1.3.md)
- [AgentCard与协议对象规范 V1.3](A2AMesh_AgentCard与协议对象规范_V1.3.md)
- [A2A协议与NATS集成适配设计 V1.3](A2AMesh_A2A协议与NATS集成适配设计_V1.3.md)
- [任务生命周期与长任务运行时设计 V1.3](A2AMesh_任务生命周期与长任务运行时设计_V1.3.md)
- [编排器 Runtime与工具适配设计 V1.3](A2AMesh_编排器_Runtime与工具适配设计_V1.3.md)
- [接口请求与响应标准 V1.3](A2AMesh_接口请求与响应标准_V1.3.md)
- [统计审计与运行监控规则 V1.3](A2AMesh_统计审计与运行监控规则_V1.3.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
- [A2A v1.0.1 canonical Proto](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)
- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A Custom Protocol Bindings](https://a2a-protocol.org/latest/topics/custom-protocol-bindings/)
