# A2AMesh Redis 状态平面与数据设计 V1.6
> 文档ID：`A2AM-DATA-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：Redis Key、原子函数、索引、租约、幂等与保留
> 目标读者：后端、数据、测试、运维
> 评审状态：G0 候选自检完成；首轮独立复审问题已纳入修复，关闭复审待完成；代码、故障注入与交付剖面验收未完成
> 最后更新：2026-08-14
> 替代版本：V1.5；旧版位于 `docs/archive/v1.5/`，不得作为当前实现合同
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 维护方式：版本化不可变文档；后续修订递增版本

---

## 1. 文档目的

本文档定义 A2AMesh V1 的 Redis 状态平面、State Service、Key Schema、索引、原子函数、租约、幂等、事件 outbox、副作用账本、能力授权、准入控制、Push 配置、保留、备份和故障恢复规则。

Redis 只保存可查询的热状态和控制数据。JetStream 是实时事件日志；大 Artifact 使用对象存储；Peer 不直接访问 Redis。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立单 Mesh Redis Key、Task/Card/幂等/租约/Push 数据契约和 Lua 原子规则 |
| V1.1 | 2026-08-14 | 落地DATA标识、Agent发现查询模型和对应验收ID |
| V1.2 | 2026-08-14 | 补齐Principal Registry/Alias、Credential索引和跨协议幂等数据契约 |
| V1.3 | 2026-08-14 | 增加原子事件outbox、副作用账本、能力授权、队列准入与恢复目标数据契约 |
| V1.4 | 2026-08-14 | 增加 Artifact 元数据、配置 generation、对账 case 的状态平面边界和原子操作引用 |
| V1.5 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，State 与 effect 数据合同不变 |
| V1.6 | 2026-08-14 | 闭合 G0：dispatch/replay/Plan/workspace/recovery Key、原子函数和竞态线性化 |

### 1.2 实施状态

当前代码尚无 Redis State Service；本文件全部为目标设计。现有进程内 `_tasks/_task_states` 和 KV 辅助状态不得作为 V1 持久化实现继续扩展。

---

## 2. 设计原则

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

## 3. 拓扑

```text
Gateway / Application Core ─┐
Peer / Relay / Push ─────────┼─ NATS State RPC ─▶ State Service ─▶ Redis
Projector / Admin Probe ─────┘                         │
                                                       └─ Lua/Functions
```

State Service 从认证 NATS 身份派生 principal/agent，不接受 payload 覆盖。Redis 密码、ACL 用户和 TLS 配置只存在于 Linux 内部环境。

---

## 4. 数据权威边界

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
| Capability grant | active generation 的权威运行快照 | 可发配置变更事件 | 签名 bundle 为输入 |
| Admission counter/queue | 权威短期控制状态 | 可发过载审计事件 | — |
| Push 配置/投递状态 | 权威 | delivery event | 凭据应用层加密 |
| Artifact 稳定元数据/上传会话 | 权威 | 可带引用/状态事件 | Object Store 是 blob 权威 |
| Trusted config generation/READY/publisher lease | 权威运行指针与协调状态 | 配置变更事件 | 签名 bundle 制品为内容权威 |
| Reconciliation case/claim/evidence digest | 权威热状态 | 对账结果事件 | provider/不可变回执为证据来源 |
| Runtime stdout | 可选最后摘要 | 限量进度事件 | 日志系统 |
| NKey seed/Token | 禁止 | 禁止 | Secret Store |

---

## 5. Key Schema

`{default}` 是 Redis hash tag 示例，实际由受信配置 `mesh_id` 生成。

### 5.1 DATA-CARD-001：Agent Card、Registry 与 Presence

| Key | 类型 | 字段/值 | TTL |
|---|---|---|---|
| `a2am:v1:{default}:card:<agentId>:public` | STRING | canonical AgentCard ProtoJSON | 无短 TTL |
| `...:card:<agentId>:extended` | STRING | 加密/受控 Extended Card | 无短 TTL |
| `...:card:<agentId>:meta` | HASH | version,etag,cardGeneration,configGeneration,fencingToken,updatedMs,instanceId,bindingCapabilitiesJson,capabilitiesDigest | 无短 TTL |
| `...:agents:presence` | ZSET | member=agentId, score=lastSeenMs | 通过 score 判过期 |
| `...:agent:<agentId>:instances` | SET | 活跃 instanceId 索引 | 清理器移除过期成员 |
| `...:presence:instance:<agentId>:<instanceId>` | STRING | heartbeat JSON | Key TTL 90 秒 |
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

### 5.2 DATA-TASK-001：Task 与 Context

| Key | 类型 | 字段 |
|---|---|---|
| `...:task:<taskId>` | HASH | 见 5.3 |
| `...:task:<taskId>:attempt:<attempt>:containment` | HASH | attestationId,attestationJwsJson,attestationJwsDigest,containmentLevel,supervisorPrincipal,registeredMs | 与Task历史同周期；同attempt不可覆盖 |
| `...:task:<taskId>:stream-claim-initial` | HASH | streamInitialFrameJson,streamInitialFrameDigest,claimEventSeq,createdMs | 与Task同周期；仅SendStreamingMessage claim CAS写入 |
| `...:tasks:updated` | ZSET | member=taskId, score=updatedMs |
| `...:tasks:state:<state>` | ZSET | member=taskId, score=updatedMs |
| `...:context:<contextId>:tasks` | ZSET | member=taskId, score=createdMs |
| `...:caller:<principal>:tasks` | ZSET | caller 可见 Task |
| `...:agent:<agentId>:tasks` | ZSET | target/owner Task |

### 5.3 Task HASH

```text
taskJson                canonical Task ProtoJSON
state                   official TaskState
contextId               canonical context ID
callerPrincipal         authenticated caller
principalType           agent/a2a/mcp/system
credentialId            non-secret credential identifier
aliasGeneration         resolved alias generation
callerAgentId           optional caller agent
targetAgentId            selected target
configGeneration        Task 创建时 active generation
policySnapshotHash      immutable policy snapshot/ref digest
canonicalCommandJson    immutable canonical Send request/command
canonicalCommandHash    cross-Binding conflict digest
ownerAgentId            lease owner agent
ownerInstanceId         lease owner instance
createdMs / updatedMs   epoch milliseconds
version                 optimistic task version
eventSeq                latest committed event sequence
lastHeartbeatMs         TaskSupervisor heartbeat
freshnessVersion        仅 heartbeat 新鲜度，不改变 Task ETag/version
phase                   A2AMesh progress phase
phaseSummary            redacted human summary
progressJson            compact structured progress
attempt                 execution attempt
leaseUntilMs            cached display field; lease key is authority
fencingToken            latest token
cancelRequested         internal boolean
queueDeadlineMs         absolute queue deadline
dispatchDeadlineMs      DRR SELECTED 后 absolute dispatch deadline
softExecutionDeadlineMs WORKING 后 soft deadline
hardExecutionDeadlineMs WORKING 后 hard deadline
maxAttempts             frozen retry limit
nextRetryMs             next eligible recovery time
retryPolicy             frozen retry safety class
terminalReason          redacted reason
reconciliationRequired  external effect requires manual/provider reconciliation
artifactCount           count
containmentAttestationRef latest execution-attempt State ref
containmentAttestationDigest latest exact JWS digest
streamClaimInitialFrameJson  SendStreamingMessage claim时冻结的SUBMITTED首帧
streamClaimInitialFrameDigest  首帧exact bytes SHA-256
```

`taskJson` 是标准对象权威表示；冗余字段用于索引和 CAS，必须由同一原子函数更新。

### 5.4 DATA-DEDUPE-001：Message 幂等

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

### 5.5 DATA-LEASE-001：Task Lease 与 Fencing

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

### 5.6 DATA-PUSH-001：Push Notification

| Key | 类型 | 内容 |
|---|---|---|
| `...:task:<taskId>:pushcfg` | SET | configId |
| `...:pushcfg:<taskId>:<configId>` | HASH | url,authType,encryptedCredential,status,createdMs |
| `...:pushdelivery:<deliveryId>` | HASH | taskId,eventSeq,attempt,status,nextRetryMs,lastError |
| `...:push:due` | ZSET | member=deliveryId, score=nextRetryMs |
| `...:push:dlq` | ZSET | failed delivery IDs |

Webhook credential 必须 envelope encryption；Redis 中不存解密密钥。

### 5.7 DATA-CURSOR-001：游标与限流

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

### 5.8 DATA-PRINCIPAL-001：Principal、Credential 与 Alias

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

### 5.9 DATA-OUTBOX-001：Task Event Outbox

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:outbox:event:<taskId>:<eventSeq>` | HASH | eventId,taskId,eventSeq,taskVersion,eventType,payloadJson,payloadDigest,configGeneration,createdMs,status,nextAttemptMs,publishAttempts,claimOwner,claimToken,claimExpiresMs,publishedMs,jetStreamStreamSeq,lastError,lastDeadReason,lastDeadAt,recoveryCount,lastRecoveryEvidenceSha256,lastRecoveryPrincipalHash,lastRecoveredAt | PubAck 后保留 1 小时或按 policy 删除 |
| `...:outbox:due` | ZSET | member=`<taskId>:<eventSeq>`，score=nextAttemptMs | Relay 处理后移除 |
| `...:outbox:dead` | ZSET | 超过受控重试上限的 eventId | 人工恢复前保留 |
| `...:outbox:task:<taskId>` | HASH | headSeq,publishedSeq,blockedByDeadSeq,version | Task event 热窗口 |
| `...:outbox:recovery-idempotency:<taskId>:<operatorPrincipalHash>:<idempotencyKeyHash>` | HASH | requestDigest,resultJson,resultDigest,repairEvidenceSha256,createdMs | 至少365天；WORM audit长期保留 |

`eventId` 固定为 `<taskId>:<eventSeq>`，状态机固定 `PENDING → CLAIMED → PUBLISHED`，失败可由 `CLAIMED → PENDING/DEAD`；只有 `recover_dead_outbox` 可将 `DEAD → PENDING`。JetStream publish 使用同一确定性消息 ID。State 为 mutation 分配严格 `eventSeq=current+1`，调用方不得传序列。`blockedByDeadSeq` 非空时不得删除或越过该事件；outbox 是投递恢复缓冲，不支持 Task 历史查询，不得保存第二份无限事件流。

### 5.10 DATA-EFFECT-001：外部副作用账本

| Key | 类型 | 内容 |
|---|---|---|
| `...:task:<taskId>:effect-intents` | ZSET | member=effectIntentId，score=createdMs |
| `...:effect-intent:<effectIntentId>` | HASH | taskId,stepId,toolName,risk,requestHash,currentAttemptId,attemptNo,providerIdempotencyKey,configGeneration,policySnapshotHash,version,createdMs,updatedMs |
| `...:effect-attempt:<effectAttemptId>` | HASH | effectIntentId,effectAttemptId,taskId,executionAttempt,containmentAttestationRef,containmentAttestationDigest,providerCallNo,state,providerRef,requestHash,receiptHash,staleAfterMs,preparedMs,applyingMs,updatedMs,lastError |
| `...:effect:stale-due` | ZSET | member=effectAttemptId，score=staleAfterMs；仅APPLYING且owner lease失效的候选 | 扫描CAS移除 |
| `...:effect:scanner-lease` | HASH | scannerInstanceId,scannerFencingToken,leaseUntilMs | State/Reconciliation Service运行期；fence单调 |
| `...:effect:scan:<scanOperationId>` | HASH | requestDigest,effectAttemptId,expectedStaleAfterMs,resultJson,resultDigest,createdMs | 与effect/case同周期；不可覆盖 |

`state` 只能按以下状态机迁移：

```text
PREPARED → APPLYING → APPLIED
                    ↘ UNKNOWN
PREPARED/APPLYING/APPLIED → COMPENSATED
PREPARED/APPLYING → FAILED
UNKNOWN → APPLIED / COMPENSATED / FAILED  （仅对账后）
```

`effectIntentId` 由 State 在首次 prepare 时分配并以 `requestHash` 唯一 CAS，跨安全重试稳定；provider idempotency key 从该 ID 派生且不可更换。`effectAttemptId` 表示一次真实 provider 调用，不能复用。只有前一 attempt 已被可信 evidence 证明 `NOT_APPLIED/FAILED_BEFORE_CALL`、retry policy 允许且无 `UNKNOWN/APPLIED` 时，`begin_effect_attempt` 才能原子递增 `attemptNo` 并创建下一 attempt。账本只保存脱敏 provider reference、request/receipt hash 和必要状态，不保存 Token 或完整第三方响应。`UNKNOWN/APPLIED` 必须阻止自动新 attempt 和 canceled 终态，直到对账/补偿完成。

### 5.11 DATA-GRANT-001：Capability Grant

| Key | 类型 | 内容 |
|---|---|---|
| `...:grant:<grantId>` | HASH | principalHash,targetAgentId,operations,skills,toolRisks,workspaceAliases,status,generation,expiresMs |
| `...:principal:<principalHash>:grants` | SET | grantId |
| `...:grant:generation` | STRING | 当前受信配置 generation |

Grant 由受信配置生成并以 generation 原子替换。授权匹配必须同时满足 Principal、目标 Agent、operation/skill、Tool risk 和 workspace alias；缺失字段不表示通配。系统组件只拥有各自固定内部操作，不继承业务 caller grant。

### 5.12 DATA-ADMISSION-001：准入与公平排队

| Key | 类型 | 内容 | TTL |
|---|---|---|---|
| `...:admission:global` | HASH | queued,reserved,running,maxQueued,maxRunning | 活跃期间 |
| `...:admission:principal:<principalHash>` | HASH | queued,reserved,running,deficit,weight,lastRound,joinedRound,oldestQueuedMs | 空闲后短 TTL |
| `...:admission:principals` | LIST/ZSET | active principal ring，score/order 为 round cursor | 空队列时移除 |
| `...:admission:principal:<principalHash>:fifo` | ZSET | member=taskId，score=monotonic enqueue sequence | 出队移除 |
| `...:admission:task:<taskId>` | HASH | principalHash,state,enqueuedMs,selectedMs,runningMs,queueDeadlineMs,sizeClass,cost,slotToken | 终态后短 TTL |
| `...:admission:round` / `...:admission:cursor` | STRING | 单调 round 与当前 principal | 活跃期间 |
| `...:admission:scheduler-lease` | HASH | ownerInstanceId,fencingToken,leaseUntilMs | State实例租约；不因业务空闲删除 |
| `...:admission:operation:<selectorOperationId>` | HASH | requestDigest,expectedCursor,resultJson,resultDigest,createdMs | 与admission状态同周期；同ID不可覆盖 |

准入状态固定 `QUEUED → SELECTED → RUNNING → RELEASED`；SELECTED 对应已占用但尚未执行的 `reserved` slot。硬不变量为全局及每 Principal 的 `reserved + running <= maxRunning`：QUEUED→SELECTED 原子 `queued--/reserved++`，SELECTED→RUNNING 原子 `reserved--/running++`，各失败/取消路径只按当前状态减一次对应计数。Task claim只创建QUEUED，normal dispatch intent此时不可due。Redis §6.19的RECOVERY_RESUME不是第二次admission：它必须复用同Task既有RUNNING记录/slotToken且计数全程不变，禁止RUNNING→QUEUED/SELECTED或创建第二条admission记录。

`select_admission_for_dispatch` 按 active principal ring 执行确定性 Deficit Round Robin：从持久 cursor 指向的 Principal 开始；Principal 在本 round 首次 visit 时原子增加 `quantum × weight` 并写 lastRound；随后只从该 Principal FIFO head 开始，在 `head.cost<=deficit` 且全局/Principal `reserved+running` 尚有容量时逐个 SELECTED、扣 deficit、生成单调 slotToken并令对应 dispatch due。遇到 deficit 不足、容量满或 FIFO 空即结束该 visit，**无论是否仍有 deficit 都把 cursor 推进到下一个 active Principal**，剩余 deficit 跨 round 保留；round 开始时 ring 中每个 Principal 均完成一次 visit 后 round+1，新加入 Principal 的 joinedRound 为下一 round。V1 cost=1、signed weight 1～16；空 FIFO 才从 ring 移除。禁止立即 dispatch 绕过 scheduler。

### 5.13 DATA-ARTIFACT-001：Artifact 与上传会话

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:artifact:<artifactId>` | HASH | taskId,ownerPrincipalHash,taskOwnerSnapshotRef,objectKey,contentSha256,sizeBytes,mediaType,status,configGeneration,policySnapshotHash,version,timestamps | 按 Artifact policy |
| `...:task:<taskId>:artifacts` | HASH | artifactId → 稳定 metadata JSON，不含 signed URL | 随 Task/Artifact 保留 |
| `...:artifact:<artifactId>:access-tombstone` | HASH | taskId,ownerPrincipalHash,ownerAliasGeneration,grantDigest,createdMs,expiresMs,status | 与 Artifact/审计同周期，不单独授予权限 |
| `...:artifact:<artifactId>:holds:<holdId>` | HASH | reason,sourceCaseId/sourceTaskId,createdBy,expiresMs,status,digest | hold 释放/过期后保留审计 |
| `...:artifact:<artifactId>:holds` | SET | active hold IDs | Reaper 原子读取 |
| `...:artifact:<artifactId>:refs` | HASH | field=`<refType>:<refId>`，value=`refType,refId,sourceVersion,createdMs,digest` canonical JSON；refType 仅 TASK/CASE/EVIDENCE | 引用变化原子维护 |
| `...:artifact:ref-source:<refType>:<refId>` | SET | 当前引用的 artifactId；用于 source 删除/恢复对账 | 与 source/Artifact 同周期 |
| `...:artifact:source-commit:<commitId>` | HASH | sourceType,sourceId,sourceVersion,sourceDigest,refDigests,artifactVersions,requestDigest,resultJson,resultDigest,state,createdMs | 无短TTL；source可见性与refs同CAS；同commit重放逐字节 |
| `...:artifact:upload:<uploadId>` | HASH | artifactId,taskId,ownerPrincipalHash,tempObjectKey,expectedHash,expectedSize,status,expiresMs,idempotencyHash | 默认 24 小时 |
| `...:artifact:uploads:due` | ZSET | member=uploadId, score=expiresMs | Reaper 清理 |
| `...:artifact:delete:due` | ZSET | member=artifactId, score=nextAttemptMs | 删除完成后移除 |
| `...:artifact:retention-lock:<artifactId>` | HASH | activeTaskRefs,activeCaseRefs,activeEvidenceRefs,legalHoldIds,minimumDeleteAt,policyGeneration | 无锁不可删除 |

Redis 不保存 blob、signed URL、文件内容或客户端 object key。状态机、object key、访问和保留规则以《Artifact 与对象存储设计》为唯一权威。

### 5.14 DATA-CONFIG-001：受信配置与 publisher lease

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:config:bundle:<generation>` | STRING | 已签名 canonical bundle 或不可变制品引用/contentSha256/status | 至少 365 天/归档 |
| `...:config:gate-evidence:<generation>:<evidenceSha256>` | STRING | 已签名canonical GateEvidenceRecordV1或不可变制品引用/evidenceSha256/status | 至少365天/归档；不可覆盖 |
| `...:config:active` | HASH | generation,bundleId,contentSha256,evidenceSha256,aclDigest,streamConfigDigest,readySetDigest,indexRootDigest,rolloutLeaseId,activatedMs | 无短 TTL |
| `...:config:rollout:<generation>` | HASH | rolloutLeaseId,ownerPrincipal,ownerInstanceId,rolloutFencingToken,revision,expectedActiveGeneration,state,trafficGate,activeAclDigest,candidateAclDigest,deployedAclDigest,activeStreamConfigDigest,candidateStreamConfigDigest,deployedStreamConfigDigest,candidateEvidenceSha256,productionEvidenceSha256,leaseExpiresMs,updatedMs,terminalOperationId,terminalIdempotencyScope,terminalIdempotencyKeyHash,terminalRequestDigest,terminalAt,resultJson,resultDigest,operationReplayJson,operationReplayDigest,auditRef | 非终态/FAILED_CLOSED不设物理TTL；仅COMPLETED/RESTORED可在至少365天后压缩为同key终态tombstone，永不删除generation/request/result绑定 |
| `...:config:rollout:<generation>:operation:<rolloutOperationId>` | HASH | operation,idempotencyScope,idempotencyKeyHash,requestDigest,resultJson,resultDigest,createdMs | 无物理TTL；首次业务CAS同原子写；terminal compaction前全部折叠进operationReplayJson |
| `...:config:component:<generation>:<componentType>:<instanceId>:<requestId>` | HASH | 不可变READY/NACK receipt热副本：componentPrincipal,nodeId,instanceId,generation,readinessPlane,rolloutLeaseId,rolloutFencingToken,contentSha256,deployedAclDigest,deployedStreamConfigDigest,environmentDigest,status,reasonCode,requestId,authProofDigest,readyRequestDigest,attestationJwsJson,attestationJwsDigest,observedAt,expiresAt,observedMs,expiresMs | expiresMs后可清理热副本；被选择的exact JWS已内嵌GateEvidence并随其至少365天/归档；存续期不可覆盖 |
| `...:config:component-current:<generation>:<componentType>:<instanceId>` | HASH | latestRequestId,status,readinessPlane,rolloutLeaseId,rolloutFencingToken,attestationJwsDigest,deployedAclDigest,deployedStreamConfigDigest,environmentDigest,expiresMs | 短TTL current pointer；不改旧receipt |
| `...:config:genesis-marker:<deploymentId>` | HASH | state,genesisNonceDigest,bundleId,contentSha256,intentUri,intentJwsDigest,hostMarkerDigest,commitReceiptUri,commitReceiptDigest,preparedMs,committedMs | 永久；WORM commit 是权威，Redis/主机为投影 |
| `...:card-publisher:<agentId>` | HASH | instanceId,configGeneration,fencingToken,expiresMs | lease TTL |
| `...:config:audit` | STREAM | generation,action,result,actorPrincipalPseudonym,reasonCode,timestamp | 热索引；WORM 正文保存受限 canonical actorPrincipal |

active pointer只能由Config Controller CAS更新。READY request的`observedAt/expiresAt`只接受UTC、恰3位毫秒和`Z`的RFC3339；State严格解析成epoch毫秒`observedMs/expiresMs`仅供TTL/index比较，digest和GateEvidence始终使用receipt内原始canonical时间字符串，禁止实现自行格式化回写。热key的`attestationJwsJson`保存完整State签名General JWS bytes；GateEvidence readyAttestations item还必须内嵌同一ASCII envelope string及digest并由GateEvidence签名覆盖。热key缺失时stage/activate从record内嵌bytes验State签名、TTL、slot和current NACK；历史重验不需要current pointer。expiry只阻止新stage/activate，不能删除GateEvidence中的历史bytes或把其降为digest摘要。

rollout state只允许`PREPARING→MAINTENANCE→ACTIVATED→COMPLETED`；active CAS前可在验证旧ACL/stream config后`→RESTORED`，任一未知/不可恢复错误`→FAILED_CLOSED`。rollout key由独立机器Credential取得单owner lease/fence；`leaseExpiresMs`只是逻辑期限，不得映射为Redis key TTL。未过期时只有当前owner/fence可RENEW或阶段推进；过期后旧owner永久不能renew/写入，只有具`ops.config.recover`机器Credential且属于signed Config Controller组件的`TAKEOVER_ROLLOUT`可在一个CAS取得更高fence/revision，trafficGate保持CLOSED。State根据active pointer修正恢复方向：仍为expected old generation时只允许RESTORE_ACTIVE_ACL或继续尚未执行active CAS的安全步骤；已为candidate generation时只允许验证新ACL/stream/health后FINISH_ROLLOUT；未知pointer/digest保持FAILED_CLOSED。`FAILED_CLOSED`是可受审接管的持久状态，不是可compaction终态；缺key一律按未知故障fail closed，不能解释为“无rollout”。

每个rollout操作的`idempotencyScope=meshId+"\0"+generation+"\0"+operation`，`idempotencyKeyHash=SHA-256(UTF8(idempotencyScope+"\0"+Idempotency-Key))`，`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`；首次状态CAS、operation result、audit/outbox同原子。相同scope/key/body逐字节返回已存result，异body零写入冲突。COMPLETED/RESTORED完整记录至少365天后，compactor必须按rolloutOperationId UTF-8排序，把每项`rolloutOperationId,operation,idempotencyScope,idempotencyKeyHash,requestDigest,resultJson,resultDigest`exact数组写入operationReplayJson并重算operationReplayDigest，再原子替换为同key tombstone；迟到任一阶段同请求从该数组逐字节返回原result，异请求冲突，绝不重建lease、改active pointer或自动开流量。MAINTENANCE后State除config/recovery和既有安全收尾外拒绝新Task/effect/upload。bundle不含secret明文；完整validate/stage/activate/rollback/revoke合同以《受信配置与变更治理设计》为准。

### 5.15 DATA-RECON-001：人工对账 case

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:recon:case:<caseId>` | HASH | taskId,effectIntentId,effectAttemptId,attempt,workflowState,escalated,priority,currentResolutionId,revision,claimOwnerHash,claimOwnerInstanceId,claimExpiresMs,claimFencingToken,claimGeneration,claimSlaCycle,claimSlaDueMs,claimedInCurrentOpenCycle,timestamps | 至少 365 天/归档 |
| `...:recon:effect-attempt:<effectAttemptId>` | STRING | 唯一 active caseId | 与 case 同周期 |
| `...:recon:cases:workflow:<workflowState>` | ZSET | member=caseId, score=updatedMs | workflow 迁移原子维护 |
| `...:recon:cases:escalated` | ZSET | member=caseId, score=escalatedMs | escalation 改变时原子维护 |
| `...:recon:claim-fence:<caseId>` | STRING counter | 单调claim/tombstone fencing token，永不回退或复用 | 无物理TTL；与case tombstone同周期 |
| `...:recon:due` | ZSET | member=`CLAIM_EXPIRE:<caseId>`或`ESCALATE:<caseId>`，score=对应server dueMs | writer同CAS插入/重排/移除；只作候选索引 |
| `...:recon:scanner-lease` | HASH | scannerLeaseId,ownerInstanceId,scannerFencingToken,revision,leaseUntilMs | 逻辑TTL；接管保留更高fence |
| `...:recon:scanner-fence` | STRING counter | 单调scanner fencing token | 无物理TTL |
| `...:recon:due-scan:<scanOperationId>` | HASH | operation,requestDigest,scannerLeaseId,scannerFencingToken,candidatesJson,resultJson,resultDigest,createdMs | 与case最长保留期一致；同ID exact replay |
| `...:recon:claim-operation:<claimOperationId>` | HASH | operation,caseId,idempotencyScope,idempotencyKeyHash,requestDigest,resultJson,resultDigest,createdMs | 与case同周期；首次业务CAS同原子写 |
| `...:recon:evidence:<evidenceId>` | HASH | caseId,type,source,result,payloadSha256,canonicalSourceJson,sourceVersion,sourceDigest,visibility,refCommitId,artifactId,collectorHash,timestamps | append-only；VISIBLE只在typed source commit CAS后出现 |
| `...:recon:case:<caseId>:evidence` | ZSET | member=evidenceId, score=collectedMs | 与 case 同周期 |
| `...:recon:resolution:<resolutionId>` | HASH | immutable ResolutionRecord fields + recordDigest | 至少 365 天/归档 |
| `...:recon:case:<caseId>:resolutions` | ZSET | member=resolutionId, score=caseRevision | append-only |
| `...:recon:idempotency:<caseId>:<keyHash>` | HASH | operation,requestDigest,resultRef,createdMs | 与 case 同周期 |
| `...:recon:audit` | STREAM | caseId,revision,action,result,actorPrincipalPseudonym,evidenceDigest,timestamp | 热索引；WORM 正文保存受限 canonical actorPrincipal |

Case/evidence/claim/resolution 的业务状态机和权限以《人工对账与运维操作设计》为唯一权威；Redis 文档只定义热状态和原子索引。

### 5.16 DATA-DISPATCH-001：持久执行投递

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:dispatch:<taskId>` | HASH | dispatchId,taskId,targetAgentId,state,dispatchMode,recoveryOperationId,dispatchAttempt,requestHash,payloadRef,payloadDigest,configGeneration,policySnapshotHash,bindingSchemaVersion,admissionSlotToken,nextAttemptMs,claimOwner,claimToken,claimExpiresMs,claimedMs,sentMs,replyInboxDigest,dispatchDeadlineMs,lastError | Task 终态后 24 小时 |
| `...:dispatch:due` | ZSET | member=taskId, score=nextAttemptMs | ACCEPTED/DEAD 后移除 |
| `...:dispatch:dead` | ZSET | 超过 deadline 或策略的 taskId | 人工恢复前保留 |
| `...:task:recovery:due:<targetAgentId>` | ZSET | member=taskId，score=当前execution leaseUntilMs | lease续租同CAS重排；终态/cancel移除 |
| `...:task:recovery:operation:<recoveryOperationId>` | HASH | identityJson,taskId,expiredFencingToken,fromAttempt,toAttempt,recoveryDispatchId,requestDigest,resultJson,resultDigest,committedMs | 与Task及dispatch历史同周期；同tuple不可覆盖 |
| `...:task:recovery:scan:<targetAgentId>:<recoveryScanIdHash>` | HASH | requestJson,requestDigest,resultJson,resultDigest,createdMs | 与Task恢复热窗口同周期；exact bytes replay |

INITIAL_START dispatch状态固定为`BLOCKED_ADMISSION → PENDING → CLAIMED → SENT → ACCEPTED`；RECOVERY_RESUME由§6.19在复核既有RUNNING slot后直接创建为PENDING，之后共用`PENDING → CLAIMED → SENT → ACCEPTED`，绝不经过BLOCKED_ADMISSION或DRR。取消可从任一未ACCEPTED状态进入ABORTED，失败进入DEAD。每次PENDING→CLAIMED同时令dispatchAttempt+1，从独立`INCR dispatch-claim-fence`取得永不复用的claimToken；只有当前attempt/token可写SENT/ACCEPTED。Worker必须在network publish前调用`mark_dispatch_sent`令CLAIMED→SENT；标记后崩溃仍由过期reclaim恢复。`CLAIMED/SENT && claimExpiresMs<=now`只能由`reclaim_expired_dispatch`原子回PENDING，清owner/inbox、按稳定dispatchId退避并使旧token永久失效；deadline/cancel走DEAD/ABORTED。只有`select_admission_for_dispatch`可把**normal** BLOCKED_ADMISSION→PENDING；recovery直接PENDING的唯一writer是同一§6.19 CAS。`claim_message`只创建INITIAL_START immutable command，Worker不得改payloadRef/hash/generation/mode。

INITIAL_START accept把Task置WORKING、RECOVERY_RESUME accept把新provisional owner正式化时，都必须在各自同一CAS把taskId写入目标Agent recovery due ZSET；`renew_lease`只在owner/fence匹配时同时更新lease和ZSET score，任何终态、确定cancel或安全释放都在同一CAS移除。ZSET只提供候选，State仍逐项重验Task/lease/effect/admission；Supervisor不得直接读取Redis或自行选择任意taskId。

### 5.17 DATA-PLAN-001：ExecutionPlan 与 Step

| Key | 类型 | 内容 |
|---|---|---|
| `...:plan:<planId>` | HASH | rootTaskId,revision,state,configGeneration,policySnapshotHash,planJson,planDigest,ownerInstanceId,leaseUntilMs,fencingToken,recoveryState,recoveryEpoch,recoveryRevision,recoveryCursorStepId,recoveryStartedMs,recoveryUpdatedMs,createdMs,updatedMs |
| `...:plan:<planId>:steps` | ZSET | member=stepId, score=stableOrder |
| `...:plan:<planId>:step:<stepId>` | HASH | objective,dependencies,status,childTaskId,attempt,requiredSkills,preferredRuntime,targetAgentId,provider,workspaceAlias,allowedTools,effectClass,retryPolicy,resultContractJson,resultContractHash,timeoutMs,lastRecoveryEpoch,lastRecoveryRevision,lastError |
| `...:task:<rootTaskId>:plan` | STRING | planId |
| `...:task:<childTaskId>:root` | HASH | rootTaskId,planId,stepId；与 child Task 同周期 |
| `...:plan:<planId>:fence` | STRING counter | 单调 owner fencing token，永不回退/复用 |
| `...:plan:due` | ZSET | member=planId，score=leaseUntilMs；仅RUNNING非终态Plan | 仅在终态CAS或State确认逻辑lease expiry后由recover writer原子移除 |
| `...:plan:recovery-scan:<scanOperationId>` | HASH | requestDigest,expectedCursor,candidatesJson,resultJson,resultDigest,createdMs | 至少与Plan保留同周期；每个candidate含随机token；同ID不可覆盖 |
| `...:plan:<planId>:operation:<planOperationId>` | HASH | operation,requestDigest,resultJson,resultDigest,createdMs | 与Plan同周期；同ID不可覆盖 |

`planJson` 是通过 validator 的 immutable canonical 完整 Plan；Step HASH 是查询/迁移投影。Plan 业务状态 `DRAFT→VALIDATED→RUNNING→COMPLETED/FAILED/CANCELED`；独立持久恢复 gate 固定为 `recoveryState=NONE|RECONCILING`，不是临时进程内标志，也不改写业务 state。Step 状态 `PENDING→READY→DISPATCHED→RUNNING→SUCCEEDED/FAILED/CANCELED/SKIPPED`。dependency 全成功才 READY；失败按 retryPolicy 生成新 attempt，否则下游按 resultContract 显式 FAILED 或 SKIPPED。所有迁移用 Plan revision + fencing CAS，并原子维护 root/child 双向映射；Tracker/Aggregator 不得把进程内状态当唯一事实。新 Plan 初始化 `recoveryState=NONE,recoveryEpoch=0,recoveryRevision=0,recoveryCursorStepId=null`。

### 5.18 DATA-AUTH-REPLAY-001 与 DATA-WORKSPACE-LEASE-001

| Key | 类型 | 内容 | TTL |
|---|---|---|---|
| `...:auth:replay:<signerHash>:<requestIdHash>` | STRING | authProof digest/configGeneration | `expiresAt + clockSkew` |
| `...:workspace:lease:<workspaceAlias>` | STRING | ownerAgentId,instanceId,taskId,fencingToken,mode | 默认 30 秒 |
| `...:workspace:fence:<workspaceAlias>` | STRING counter | 单调 fencing token | 无短 TTL |

AuthProof 的签名和时间验证在 State Service 受信入口完成，Redis 原子 claim 只接受该入口传入的已验证 digest，并用 replay Key 跨实例拒绝重复 requestId。共享 workspace 的写任务必须同时持有 Task lease 和 workspace lease；只读任务可按 policy 并行。

### 5.19 DATA-AUDIT-001：可靠审计投递

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:audit:event:<sourceId>:<eventId>` | HASH | eventId,sourceId,sourceSeq,canonicalEvent,eventDigest,configGeneration,status,nextAttemptMs,claimOwner,claimToken,claimExpiresMs,sinkGlobalSeq,sinkReceipt,lastError | WORM receipt 后按热窗口删除 |
| `...:audit:due` | ZSET | member=`<sourceId>:<eventId>`，score=nextAttemptMs | DELIVERED 后移除 |
| `...:audit:dead` | ZSET | 未持久确认事件 | 人工恢复前不得删除 |
| `...:audit:source:<sourceId>` | HASH | nextSourceSeq,ackedSourceSeq,lastEventDigest | 活跃期间/恢复清单覆盖 |
| `...:audit:sink:checkpoint` | HASH | globalSeq,sealedSegmentId,segmentDigest,segmentJwsDigest,immutableUri,wormReceiptDigest,receiptAt | 可从 WORM manifest/receipt 重建 |
| `...:audit:operation:<operationId>` | HASH | operation,relayPrincipalHash,requestDigest,resultJson,resultDigest,committedMs | 与审计热索引同周期；同ID不可覆盖 |

State mutation 在同一 Redis Function 内写 audit event；认证拒绝、OAuth、Runtime/Tool、审计读取等没有 State mutation 的来源，必须先写本机 fsync WAL 或调用 durable Audit Ingress 取得 receipt，再宣布受保护动作成功。配置/对账/恢复/副作用启动等特权操作在 durable audit 不可用时 fail closed。sourceSeq 单调且重投不变；Audit Relay 使用 claim lease，WORM sink 分配全局 `sinkGlobalSeq`，重复 eventId 返回原 receipt。

公共 wire 唯一为 `AuditEnvelopeV1`（camelCase）：`schemaVersion,eventId,eventDigest,occurredAt,meshId,sourceId,sourceSeq,previousEventDigest,configGeneration,requestId,traceId,taskId,contextId,messageId,actorPrincipal,actorAgentId,targetAgentId,credentialId,authMethod,issuerHash,aliasGeneration,instanceId,runtime,operation,action,result,errorClass,beforeSummary,afterSummary,sourceIpMasked,payload`。领域字段只能放入 `payload`，Config/Reconciliation/Runtime 不得另造顶层 casing。先对不含 `eventId/eventDigest` 的对象做 RFC 8785，`eventDigest=SHA-256(bytes)`，再令 `eventId=sourceId:sourceSeq:eventDigest` 并得到最终 canonicalEvent。受限 WORM 正文保存 `actorPrincipal`；导出投影必须删除该字段并写 `actorPrincipalPseudonym=base64url(HMAC-SHA256(deploymentPseudonymKey[keyVersion],UTF8(actorPrincipal)))` 与 `pseudonymKeyVersion`，不得同时包含 raw principal；Metrics 不含两者。

Sink 按 sinkGlobalSeq 严格递增排序，使用唯一 `AuditRecordV1` 二进制 framing：`record = u32be(frameLen) || u64be(sinkGlobalSeq) || u32be(eventLen) || eventBytes`；`eventBytes` 是最终 `AuditEnvelopeV1` 的 RFC 8785 UTF-8 canonicalEvent（无 BOM/换行），`eventLen=len(eventBytes)`，`frameLen=8+4+eventLen`，两个长度和序号均为 unsigned network byte order。eventLen 上限固定 16,777,216；超限、零长度、序号溢出、非连续/重复 sinkGlobalSeq 拒绝。segment records 是按序 record 的无分隔直接拼接，`recordsDigest=SHA-256(concat(record...))`；空拼接 digest 只用于 codec fixture，实际 segment 至少一条。禁止 decimal ASCII 序号、varint、little-endian、JSON array、分隔符或实现相关长度。

固定 codec fixtures（hex 小写；其中简化 JSON/string 仅测试 framing codec，生产 seal 前仍必须先通过完整 AuditEnvelopeV1 schema/canonical 校验）：

| Case | exact bytes/source | bytes | SHA-256 |
|---|---|---:|---|
| zero records（seal 必须拒绝） | empty bytes | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| seq=1，event=`{}` | `0000000e0000000000000001000000027b7d` | 18 | `ed780321ec239257ecb98632b879d0f27cb9f7c6f0fac2e5bdab95a99103c0f1` |
| seq=258，event=`{"a":1}` | `000000130000000000000102000000077b2261223a317d` | 23 | `a5a5e1a8fd68993fdbb9190ffc8736f71c66681beceecc82bce92cf6eea7653d` |
| 上述两 record 依次拼接 | `0000000e0000000000000001000000027b7d000000130000000000000102000000077b2261223a317d` | 41 | `a92fa621f485563098b23a59ce0e4ba061363f2adc333585e7dfac542e200e17` |
| seq=2^64-1，eventBytes=`concat(0x22, 0x61×254, 0x22)` | prefix=`0000010cffffffffffffffff00000100`，suffix=`61616161616161616161616161616122`，中间恰 254 个 `61` | 272 | `e0800391b96103d8d2f4a8f4a229731f9365073011e44a87814cd794faa382c3` |
| eventLen=16,777,216 上限，seq=7，eventBytes=`concat(0x22, 0x61×16,777,214, 0x22)` | prefix=`0100000c000000000000000701000000`，suffix 同上一行；总 eventBytes 恰 16 MiB | 16,777,232 | `d99f19ecccb3dc9dd85c6983f13b38f3ee9a6fb14032826372be5b247af4a5e3` |

`AuditSegmentPayloadV1` 仅包含 `schemaVersion,segmentId,firstGlobalSeq,lastGlobalSeq,previousSegmentDigest,recordsDigest,createdAt,retentionUntil,signingPolicyVersion`；`segmentDigest=SHA-256(RFC8785(payload))`。签名 wire **只允许 JWS General JSON、payload 使用 base64url 编码且不 detached**：顶层恰为 `payload/signatures[]`；每个 protected header 恰含 `alg=EdDSA,kid,typ=a2amesh-audit-segment+jws,schemaVersion=1`，禁止 unprotected `header`。`protected=base64url(RFC8785(protectedHeader))`、`payload=base64url(RFC8785(payloadObject))`，EdDSA 输入固定为 ASCII(`protected.payload`)；每个signature entry恰含`protected,signature`，`signatures[]`按解码后protected header的`kid` UTF-8字节严格升序，重复kid或非升序envelope拒绝；最终 General JSON envelope 再按 RFC 8785 序列化，`segmentJwsDigest=SHA-256(exact envelope bytes)`。普通 segment 要求 active audit signer set 中恰1个有效签名；密钥轮换 segment 要求 previousKid 与 newKid 各1个、共2个不同 kid 的签名，并在 payload 提升 signingPolicyVersion。`kid/signature/previousKid/newKid/WORMReceipt` 不进入 payload；immutable URI、segmentJwsDigest 和 WORM receipt 是存储回执。跨日第一个 segment 链接前一日最后 segmentDigest；迟到事件只进入更高全局序号的新 segment，不重写已密封 segment。canonical payload、protected header、base64url payload、两个签名及 exact JWS bytes 必须有固定 fixture。

### 5.20 DATA-RECOVERY-001：共同恢复清单

Redis 仅保存可重建索引；签名 Manifest 正文权威副本必须写入独立 WORM/异机恢复目录，不能只存在 Redis：

| Key | 类型 | 内容 |
|---|---|---|
| `...:recovery:manifest:<recoveryPointId>` | HASH | manifestDigest,manifestJwsDigest,summaryRootUri,summaryRootDigest,indexRootDigest,immutableUri,state,producerId,createdAt,verificationReceiptUri,verificationReceiptDigest,verifiedAt,restoreReceiptUri,restoreReceiptDigest,restoredAt,restoredVerificationDigest,releaseApproval1Uri,releaseApproval1Digest,releaseApproval2Uri,releaseApproval2Digest,releaseReceiptUri,releaseReceiptDigest,releasedAt |
| `...:recovery:verify:due` | ZSET | member=recoveryPointId，score=verifyDueAtMs；仅SEALED且未产生有效VerificationReceipt；VERIFIED/REJECTED后移除 |
| `...:recovery:verify:scan:<verifierPrincipalHash>:<scanOperationIdHash>` | HASH | requestJson,requestDigest,resultJson,resultDigest,createdMs；与manifest恢复热窗口同周期；exact replay |
| `...:recovery:summary-node:<recoveryPointId>:<nodeDigest>` | HASH | nodeUri,nodeDigest,nodeType,sourceType,level,firstKey,lastKey,entryCount,parentDigest,status | 由WORM summary node重建；不得用listing发现或覆盖 |
| `...:recovery:archive-transition:<transitionId>` | HASH | transitionId,recoveryPointId,sourceType,fromWatermark,toWatermark,preIndexRootDigest,expectedSourceWatermark,archiveUri,archiveDigest,archiveRecordCount,rangeDigest,receiptUri,receiptDigest,newRecoveryPointId,newManifestDigest,newIndexRootDigest,state,leaseId,leaseOwnerInstanceId,leaseFencingToken,requestDigest,resultJson,resultDigest,createdAt,updatedAt | WORM receipt/manifest覆盖前不得删除hot range；同tuple不可覆盖 |
| `...:recovery:compaction:due` | ZSET | member=transitionId，score=dueAtMs | VERIFIED/HOT_DELETED/FAILED_CLOSED后移除 |
| `...:recovery:compaction:scan:<compactorPrincipalHash>:<scanOperationIdHash>` | HASH | requestJson,requestDigest,resultJson,resultDigest,createdMs | compaction热窗口同周期；exact replay |
| `...:recovery:compaction:lease:<sourceType>` | HASH | leaseId,ownerInstanceId,fencingToken,leaseUntilMs,transitionId | source-specific；释放后保留last fence审计 |
| `...:recovery:compaction:fence:<sourceType>` | STRING counter | 永不复用的source-specific fencing token | 永久/随部署 |
| `...:recovery:compaction:idempotency:<compactorPrincipalHash>:<transitionId>:<idempotencyKeyHash>` | HASH | requestDigest,resultJson,resultDigest,committedMs | 与transition同周期 |
| `...:recovery:latest-verified` | STRING | 最后 VERIFIED recoveryPointId |
| `...:recovery:latest-released` | STRING | 最后 RELEASED recoveryPointId |
| `...:artifact:delete-journal:<sequence>` | HASH | artifactIdHash,objectVersionIdHash,action,occurredAt,eventDigest,previousDigest |
| `...:artifact:delete-journal:watermark` | HASH | firstSeq,lastSeq,lastDigest,sealedSegmentUri |

Manifest 签名 wire **只允许 JWS General JSON、payload 使用 base64url 编码且不 detached**，状态机固定：

```text
DRAFT → SEALED → VERIFIED → RESTORED → RELEASED
            └────────────── verification failure ─→ REJECTED
```

每个 source 必须包含：

```text
sourceType, immutableBackupUri, backupId, digest,
startWatermark, endWatermark, startedAt, completedAt, encryptionKeyVersion,
producer, verificationMethod
```

必选source恰为`CONFIG_TRUST_ROOT/REDIS_AOF_OR_SNAPSHOT/JETSTREAM_SNAPSHOT/OBJECT_VERSION_SNAPSHOT/ARTIFACT_METADATA/AUDIT_SEALED_SEGMENT/DELETE_JOURNAL`各1项；V1不接受额外/重复source。`sources[]`必须按上述枚举顺序排列，每项恰含上一段11个字段；非该顺序、字段缺失/额外均在digest前拒绝。Object source 必须指向实际不可变 version snapshot/backup，不得仅写 inventory ID；delete journal 必须覆盖上一个 RELEASED object watermark 到本点的连续 sequence/digest；Audit source 必须指向 sealed segment manifest/global sequence。

Manifest的`configGeneration`所指signed bundle必须含由`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`生成的`deliveryProfile.requiredSlots[]`与其exact `recoveryPolicy.requiredComponents[]`投影；Restore required set只能从该signed bundle按同一算法重算，caller不得自报或删减。`componentVerificationDigests[]`必须覆盖该stable slot集合，READY实例的`instanceId/requestId`只作为attestation证据字段，不改变required slot身份；任何算法输入、排序、slot集合或expectedDigest漂移均使bundle/Manifest/Restore拒绝。

`RecoveryManifestPayloadV1`仅包含`schemaVersion,recoveryPointId,meshId,configGeneration,trustRootDigest,createdAt,previousRecoveryPointId,producerId,signingPolicyVersion,sources[],summary,indexRootDigest`，不含contentDigest/signatures。`manifestDigest=SHA-256(RFC8785(payload))`；JWS protected header恰含`alg=EdDSA,kid,typ=a2amesh-recovery-manifest+jws,schemaVersion=1`，禁止unprotected header；`protected=base64url(RFC8785(protectedHeader))`、`payload=base64url(RFC8785(payloadObject))`、签名输入固定ASCII(`protected.payload`)，顶层恰含`payload,signatures[]`并按RFC8785得到exact envelope。seal要求signed config的recoveryManifestSignerSet中恰2个不同kid；signature entry恰含`protected,signature`且`signatures[]`按kid UTF-8字节严格升序，重复/非升序拒绝；`manifestJwsDigest=SHA-256(exact envelope bytes)`。canonical payload、protected headers、双签和exact JWS bytes必须有固定fixture。

`summary`恰含`schemaVersion,rootNodeUri,rootNodeDigest,nodeCount,leafCount,entryCount,sourceRoots,archiveTransitions`；`sourceRoots[]`按固定七个sourceType顺序排列，每项恰含`sourceType,rootNodeUri,rootNodeDigest,entryCount,firstKey,lastKey,sourceDigest,startWatermark,endWatermark`，并逐字节绑定对应`sources[]`。`archiveTransitions[]`按`(sourceType,fromWatermark,toWatermark)`UTF-8严格升序且不得重叠/留缺口，每项恰含`sourceType,fromWatermark,toWatermark,preIndexRootDigest,archiveUri,archiveDigest,archiveRecordCount,rangeDigest,receiptUri,receiptDigest`。Archive receipt只绑定preIndexRootDigest和归档范围，不绑定post index root；新summary单向引用receiptDigest并由自身产生新的root/indexRootDigest，禁止receipt↔summary循环。`indexRootDigest=SHA-256(RFC8785({schemaVersion:"1.0",recoveryPointId,rootNodeDigest,sourceRoots,archiveTransitions}))`；root node digest、summary字段和manifestDigest形成单向依赖，任何node/summary/archive修改都必须生成新recoveryPoint，禁止原地修订。

每个WORM summary node使用canonical JSON `RecoverySummaryNodePayloadV1`，字段恰为`schemaVersion,nodeId,nodeType,sourceType,level,firstKey,lastKey,entryCount,children,entries`，`nodeDigest=SHA-256(RFC8785(payload))`，外部URI固定为内容寻址的`a2amesh://recovery-summary/<recoveryPointId>/<nodeDigest>.json`，不得靠object listing发现。`nodeType`只允许`ROOT|INNER|LEAF`：ROOT的sourceType为null且children恰为七个source root；INNER只含children；LEAF只含entries。child恰含`sourceType,nodeUri,nodeDigest,firstKey,lastKey,entryCount`，按`(firstKey,lastKey,nodeDigest)`升序；entry恰含`key,sourceType,recordIdDigest,recordVersion,recordDigest,immutableUri,status`，按`key` UTF-8升序且不得重复。所有child range必须连续、无重叠，parent计数等于children/entries递归计数，root深度≤32、每个INNER最多1024 child、每个LEAF最多4096 entry；循环、空节点、sourceType错配、URI/digest不符、计数/range不符均拒绝。`indexRootDigest`重算时必须递归GET每个exact URI并验证整个DAG，禁止只验root摘要。

archive object是WORM不可变exact bytes，`archiveDigest=SHA-256(exact bytes)`；`rangeDigest=SHA-256(RFC8785({schemaVersion:"1.0",sourceType,fromWatermark,toWatermark,recordDigests}))`，recordDigests按source key升序。archive transition receipt和新summary root必须在删除hot range前存在并可GET/read-back；任何hot compaction只能覆盖`(fromWatermark,toWatermark]`且由expected preIndexRootDigest/source watermark CAS保护，不能跨越未归档缺口。

每个状态跃迁都先生成独立不可变WORM JWS General JSON receipt，再用URI/digest幂等更新Redis索引。所有receipt payload都不含本receipt自身的digest/signatures；只允许下列字段显式绑定前序对象digest，并使用同一protected/payload/signing input/RFC8785 envelope构造；顶层固定`payload/signatures[]`、非detached、无unprotected header，signature entry恰含`protected,signature`。protected header固定`alg=EdDSA,kid,schemaVersion=1,typ`，其中typ分别为`a2amesh-recovery-verification+jws`、`a2amesh-recovery-restore+jws`、`a2amesh-recovery-archive-transition+jws`、`a2amesh-recovery-approval+jws`、`a2amesh-recovery-release+jws`。五类receipt/approval均恰1个对应role签名；Redis的`*ReceiptDigest/*ApprovalDigest`一律指exact envelope bytes的SHA-256，不是未签payload digest：

- `ArchiveTransitionReceiptPayloadV1`恰含`schemaVersion,receiptType,recoveryPointId,sourceType,fromWatermark,toWatermark,preIndexRootDigest,archiveUri,archiveDigest,archiveRecordCount,rangeDigest,transitionId,completedAt,result`且`receiptType=ARCHIVE_TRANSITION,result=ARCHIVED`；`archiveDigest`必须等于WORM archive exact bytes，`rangeDigest`必须按前一段公式重算，from/to范围必须与source snapshot连续；由Recovery Compactor签署，独立Verifier在允许删除hot range前必须读回exact receipt/archive及随后生成的summary node，并在新的VERIFIED Manifest中引用，缺任一不得compaction；receipt不含post index/root字段以保持哈希DAG无环；
- `VerificationReceiptPayloadV1`恰含`schemaVersion,receiptType,recoveryPointId,manifestDigest,manifestJwsDigest,summaryRootDigest,indexRootDigest,verifiedNodeCount,verifiedLeafCount,verifiedEntryCount,archiveTransitionDigests,summaryVerificationDigest,verifiedAt,verifierPrincipal,sourceVerificationDigests,restoreProbeDigest,result`且`receiptType=VERIFICATION,result=VERIFIED`。Verifier必须递归GET并校验root及全部summary node/child range/entry digest/archive transition exact bytes，计数逐项等于payload；`archiveTransitionDigests[]`按Manifest顺序排列。`summaryVerificationDigest=SHA-256(RFC8785({schemaVersion:"1.0",recoveryPointId,manifestDigest,indexRootDigest,summaryRootDigest,verifiedNodeCount,verifiedLeafCount,verifiedEntryCount,archiveTransitionDigests,result:"VERIFIED"}))`。`sourceVerificationDigests[]`必须非空并与Manifest七个`sources[]`同长度、同固定sourceType顺序逐项一一对应；每项恰含`sourceType,immutableBackupUri,sourceDigest,observedDigest,verificationMethod,verificationDigest,result`，其中前三项和verificationMethod逐字节等于对应Manifest source、`observedDigest=sourceDigest,result=VERIFIED`，`verificationDigest=SHA-256(RFC8785(该item排除verificationDigest))`。`restoreProbeDigest=SHA-256(RFC8785({schemaVersion:"1.0",recoveryPointId,manifestDigest,indexRootDigest,summaryVerificationDigest,sourceVerificationDigests,result:"VERIFIED"}))`。由与producer不同的verifier signer签署；任一failed/unknown/空结果只能写失败审计并使Manifest REJECTED，不能生成可推进此类型receipt；
- `RestoreReceiptPayloadV1`恰含`schemaVersion,receiptType,recoveryPointId,manifestDigest,verificationReceiptDigest,indexRootDigest,summaryVerificationDigest,restoredAt,componentVerificationDigests,restoredVerificationDigest,result`且`receiptType=RESTORE,result=RESTORED`。`componentVerificationDigests[]`必须非空且slot集合恰等于signed bundle按`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`重算的`deliveryProfile.requiredSlots[]`/`recoveryPolicy.requiredComponents[]`稳定集合；每项恰含`componentType,componentPrincipal,nodeId,verificationMethod,expectedDigest,observedDigest,verificationDigest,result`，按`(componentType,componentPrincipal,nodeId)`UTF-8字节严格升序且不得重复，其中requirement四项/expectedDigest逐字节等于signed policy、`observedDigest=expectedDigest,result=PASSED`，`verificationDigest=SHA-256(RFC8785(该item排除verificationDigest))`。`restoredVerificationDigest=SHA-256(RFC8785({schemaVersion:"1.0",recoveryPointId,manifestDigest,indexRootDigest,summaryVerificationDigest,componentVerificationDigests完整有序数组}))`。由Recovery Orchestrator恢复身份签署；任一missing/extra/FAILED/UNKNOWN/空数组或digest不等只产生失败审计并拒绝RESTORED；
- 两份独立`ReleaseApprovalPayloadV1`各恰含`schemaVersion,receiptType,recoveryPointId,manifestDigest,indexRootDigest,summaryVerificationDigest,restoredVerificationDigest,decision,operatorPrincipal,operatorKid,approvedAt`且`receiptType=APPROVAL,decision=APPROVE`；每份protected header的kid必须等于payload `operatorKid`，两份operatorPrincipal和operatorKid都不同；
- `ReleaseReceiptPayloadV1`恰含`schemaVersion,receiptType,recoveryPointId,manifestDigest,indexRootDigest,summaryVerificationDigest,restoredVerificationDigest,approval1Uri,approval1Digest,approval2Uri,approval2Digest,releasedAt,result`且`receiptType=RELEASE,result=RELEASED`。两份approval先按`(operatorPrincipal,operatorKid,approvalDigest)`UTF-8字节严格升序规范化，较小者固定为approval1，较大者为approval2；由release controller签署。

`recovery-orchestrator`是唯一Manifest payload producer与`seal_recovery_manifest`/`release_recovery_point` writer，使用独立manifest/release signer slot；`recovery-compactor`是唯一Summary Builder与ArchiveTransition writer，使用独立archive-transition signer slot；`recovery-verifier`是唯一`verify_recovery_manifest`/`mark_restored` writer，使用独立verification/restore signer slot且其Principal必须与producer不同；两份Release Approval只由不同外部operatorPrincipal/operatorKid签署，不能由任一Recovery component自签。所有`createdAt/startedAt/completedAt/verifiedAt/restoredAt/approvedAt/releasedAt`均只接受UTC RFC3339恰3位毫秒大写Z且拒绝leap second。独立verifier校验manifest双签、summary DAG每个exact URI/child/range/count、archive transition receipt/archive bytes、URI不可变性、digest、时间、连续水位、可读性和实际restore probe，只有重算全部七个source成功谓词、`indexRootDigest`、`summaryVerificationDigest`、每项`verificationDigest`和`restoreProbeDigest`后，持久化result=VERIFIED receipt才能`SEALED→VERIFIED`。恢复只能选择有有效receipt的VERIFIED，按trust/config→Object/JetStream→Redis→delete journal→全量inventory/audit对账顺序执行；只有从Manifest config重算required set、逐项PASSED并把`indexRootDigest+summaryVerificationDigest`绑定到`restoredVerificationDigest`后，result=RESTORED receipt才能RESTORED。两个不同稳定运维Principal的approval JWS均验证通过，且ReleaseReceipt已持久化，才可`RESTORED→RELEASED`并开放新Task/effect/upload。任一source/component/summary node/archive/receipt缺失、重复、额外、空集合、失败结果、范围断裂、签名阈值或digest不符进入REJECTED/fail closed。RPO以所有source `completedAt/endWatermark`的最小共同恢复点计算；summary/archive compaction不得把缺失节点或digest-only索引解释成已恢复。

Redis 全损时，Orchestrator 以部署信任根从外部恢复目录验证Manifest JWS、summary root及全部节点、archive transition receipt/archive bytes、五类transition receipt与两份approval，按immutable URI/digest重建DRAFT后的状态、source index和`indexRootDigest`；不能依赖待恢复Redis自证、object listing或只有timestamp/digest而无exact node/archive/receipt的记录推断VERIFIED/RESTORED/RELEASED。

### 5.20.1 Summary Builder与Compactor操作合同

`a2a.v1.state.recovery.compact`是唯一入口，只接受signed `recovery-compactor:<instanceId>` NKey。wire是closed `SCAN|ACQUIRE|RENEW|ADVANCE|RELEASE` union。SCAN的`RecoveryCompactionScanRequestV1`字段恰为`schemaVersion,operation,scanOperationId,expectedCursor,nowMs,limit,requestDigest,authProof`；`RecoveryCompactionScanResultV1`字段恰为`schemaVersion,operation,scanOperationId,nextCursor,candidates,resultDigest`，每个candidate恰含`transitionId,sourceType,dueAtMs,recoveryPointId,fromWatermark,toWatermark,expectedPreIndexRootDigest,expectedSourceWatermark,recoveryCandidateToken`并按`(dueAtMs,transitionId)`排序，token为State CSPRNG 32字节base64url无padding且只存scan ledger。ACQUIRE的`RecoveryCompactionLeaseRequestV1`字段恰为`schemaVersion,operation,scanOperationId,transitionId,sourceType,recoveryCandidateToken,observedDueAtMs,ownerInstanceId,leaseOperationId,leaseMs,requestDigest,idempotencyKey,authProof`；RENEW字段恰为`schemaVersion,operation,transitionId,sourceType,ownerInstanceId,leaseOperationId,leaseId,fencingToken,leaseMs,requestDigest,idempotencyKey,authProof`；RELEASE字段恰为`schemaVersion,operation,transitionId,sourceType,ownerInstanceId,leaseOperationId,leaseId,fencingToken,requestDigest,idempotencyKey,authProof`。ADVANCE使用`RecoveryCompactionRequestV1`，字段恰为`schemaVersion,operation,transitionId,recoveryPointId,sourceType,fromWatermark,toWatermark,expectedPreIndexRootDigest,expectedSourceWatermark,expectedTransitionState,nextTransitionState,leaseId,ownerInstanceId,fencingToken,archiveUri,archiveDigest,archiveRecordCount,rangeDigest,archiveReceiptUri,archiveReceiptDigest,newRecoveryPointId,newManifestDigest,newIndexRootDigest,verificationReceiptUri,verificationReceiptDigest,requestDigest,idempotencyKey,authProof`。sourceType只允许DATA-RECOVERY-001固定七枚举；按state不适用的archive/new/verification字段必须显式null，额外/缺失字段拒绝。

所有variant的`requestDigest=lowerhex(SHA-256(RFC8785(request排除requestDigest,idempotencyKey,authProof)))`；SCAN scope固定`compactorPrincipalHash+scanOperationId`，其他scope固定`compactorPrincipalHash+transitionId+idempotencyKey`。同scope同digest逐字节返回首次resultJson，异digest零写入409。`RecoveryCompactionResultV1`用于ACQUIRE/RENEW/ADVANCE/RELEASE，字段恰为`schemaVersion,operation,transitionId,recoveryPointId,sourceType,fromWatermark,toWatermark,leaseId,fencingToken,leaseUntilMs,archiveDigest,archiveReceiptDigest,newManifestDigest,newIndexRootDigest,state,resultDigest`，不适用字段显式null，resultDigest排除自身后RFC8785 SHA-256。transition state只允许`PREPARING_ARCHIVE→ARCHIVED→INDEX_SEALED→VERIFIED→HOT_DELETED`，错误或范围/receipt不确定进入持久`FAILED_CLOSED`。

执行顺序固定为：

1. `verify_recovery_manifest`成功CAS根据signed retention/compaction policy、该source最后HOT_DELETED watermark和新VERIFIED source watermark，为每个满足阈值的连续范围构造唯一intent并加入`recovery:compaction:due`；`transitionId=lowerhex(SHA-256(ASCII("a2amesh-recovery-compaction-v1")||0x00||RFC8785_UTF8({schemaVersion:"1",sourceType,fromWatermark,toWatermark,preIndexRootDigest})))`。Compactor只能先SCAN取得State选择的candidate/token，再ACQUIRE source-specific lease/fence；caller不能自选未due transition。取得lease后读取expected source watermark和已验证pre-index root，只冻结`(fromWatermark,toWatermark]`，不能读未来写入或跨越缺口。
2. 按source key升序生成archive exact bytes，使用WORM create-if-absent写入`archiveUri`，随后GET/HEAD读回并逐字节计算`archiveDigest`、`recordCount`、`rangeDigest`；对象写入或读回失败时不改hot数据。
3. 生成并签署`ArchiveTransitionReceiptPayloadV1`，写WORM receipt后再次GET exact envelope/digest；未拿到可验证receipt时禁止推进`ARCHIVED`以后的状态，禁止删除任何hot key。
4. Summary Builder读取receipt及source快照，按确定性fanout构造新的summary DAG，令新的leaf/root引用`receiptDigest`，生成新的`summaryRootDigest/indexRootDigest`和新的`RecoveryManifestPayloadV1`（`previousRecoveryPointId`指向旧点）；由于archive receipt不含post-root，整个依赖图保持单向无环。
5. 独立Verifier从WORM URI递归验证新Manifest、所有summary node、archive exact bytes、transition receipt、七个source和连续watermark，签署`VerificationReceiptPayloadV1`并将其exact URI/digest写入新manifest索引；任何缺失/漂移/计数/range错误都保持`FAILED_CLOSED`。
6. 每次ADVANCE必须在一个Redis Function内先校验active leaseId/ownerInstanceId/fencingToken、lease未过期、expected state及exact source/range/pre-root tuple，再按`ARCHIVED→INDEX_SEALED→VERIFIED`逐阶段保存read-back URI/digest和exact result；Function不做外部I/O。只有VERIFIED receipt、new Manifest/summary read-back和expected pre-root/source watermark仍匹配时，`nextTransitionState=HOT_DELETED`的同一CAS才删除或标记`(fromWatermark,toWatermark]`对应的**State热恢复索引/缓存记录**，同时写transition/audit/outbox/delete journal、移除due并保存幂等result；它不得删除JetStream、Object Store、Audit WORM或config source的权威对象，其各自生命周期只由对应source writer按已验证receipt执行。CAS前崩溃只重放archive/receipt/DAG，CAS后崩溃逐字节返回ledger结果，绝不把“删除请求已提交”当作已归档。
7. RENEW只允许同owner/lease/fence并单调延长；未过期lease的其他owner ACQUIRE返回BUSY，过期后ACQUIRE从source counter取得更高fence并绑定同transition，旧实例后续任一ADVANCE/RELEASE永久零写入。RELEASE只释放匹配lease且不改变transition业务state；HOT_DELETED后的Redis索引只保留transition/manifest/summary root可重建字段。错误pre-root、重复/重叠范围、缺receipt、只存digest不存exact archive、对象listing替代URI GET均零写入。

上述顺序还必须在due intent、SCAN result、ACQUIRE CAS、archive写入/读回、receipt写入、每次ADVANCE、summary seal、Verification receipt、hot-delete CAS和commit-before-reply后分别注入崩溃；以两个Compactor并发、旧fence和lease过期接管验证每个点重试不得产生第二archive/receipt、重复delete journal、范围缺口或改变已签manifest，同幂等scope必须逐字节返回。`Recovery Manifest`本身不可原地compaction；每次归档/摘要变化都生成新的recoveryPoint和`previousRecoveryPointId`链。

### 5.21 DATA-STREAM-SESSION-001：NATS 流会话

| Key | 类型 | 内容 | TTL/清理 |
|---|---|---|---|
| `...:stream-session:<streamSessionId>` | HASH | streamSessionId,streamOpenId,requestDigest,operation,taskId,callerPrincipalHash,callerScope,callerInstanceId,responseCorePrincipalHash,state,configGeneration,consumerConfigJson,consumerConfigDigest,brokerOperationLeaseMs,brokerApiApplyMaxMs,consumerName,filterSubject,controllerDeliverySubject,callerDeliverySubject,initialTaskVersion,initialFrameJson,initialFrameDigest,openedResponseJson,openedResponseDigest,openedResponseFlushedMs,flushObservationId,flushOperationId,flushResultJson,flushResultDigest,snapshotEventSeq,snapshotCoveredAckedStreamSeq,snapshotCoveredAckedEventSeq,snapshotCoveredAckedPayloadDigest,lastDeliveredStreamSeq,lastDeliveredEventSeq,lastDeliveredSequence,lastAckedStreamSeq,lastAckedEventSeq,lastAckedSequence,lastAckedPayloadDigest,lastBrokerAckedEventSeq,lastBrokerAckedStreamSeq,pendingStreamSeq,pendingPayloadDigest,finalStreamSeq,finalEventSeq,finalSequence,finalBrokerAckConfirmed,brokerOpEpoch,brokerOpInvalidatedThroughEpoch,maxUnquiescedBrokerApplyUntilMs,brokerOpQuiesceUntilMs,createConfirmedEpoch,cleanupEpochLowerBound,deleteConfirmedEpoch,closeReason,consumerDeletedAt,controllerInstanceId,controllerFence,controllerLeaseUntilMs,expiresAt,lastError | CLOSED/EXPIRED后24小时 |
| `...:stream-session:<streamSessionId>:broker-op:<epoch>` | HASH | controllerFence,brokerOpEpoch,brokerOpKind,brokerChallenge,brokerOpRequestDigest,issuedAt,expiresAt,executionState,executionAttempt,executorInstanceId,executionLeaseUntilMs,attemptMaxApplyUntilMs,completedAt,responseJson,responseDigest,consumedAt | 与session同周期；epoch记录不可覆盖/删除，attempt与apply上界只单调推进 |
| `...:stream-session:open:<callerPrincipalHash>:<streamOpenId>` | STRING | streamSessionId + requestDigest | 与 session 同周期 |
| `...:stream-session:due` | ZSET | member=streamSessionId，score=`min(controllerLeaseUntilMs,expiresAtMs)` | 任一时间变化同CAS重排；CLOSED/EXPIRED 后移除 |
| `...:stream-session:fence:<streamSessionId>` | STRING counter | 单调 controller fencing | 与 session 同周期 |
| `...:stream-session:recovery-scan:<controllerPrincipalHash>:<scanOperationIdHash>` | HASH | requestJson,requestDigest,resultJson,resultDigest,createdMs | 与session恢复热窗口同周期 |
| `...:stream-session:reclaim-operation:<reclaimOperationId>` | HASH | scanOperationId,streamSessionId,observedControllerFence,observedLeaseUntilMs,recoveryCandidateTokenHash,requestDigest,resultJson,resultDigest,committedMs | 与session同周期；exact result replay |
| `...:stream-session:renew-operation:<renewOperationId>` | HASH | streamSessionId,ownerInstanceId,observedControllerFence,observedLeaseUntilMs,leaseDurationMs,requestDigest,resultJson,resultDigest,committedMs | 与session同周期；exact result replay |

状态固定为正常路径 `OPENING→ACTIVE→DRAINING_FINAL→CLOSING→CLOSED`，caller 主动关闭可 `ACTIVE→CLOSING`；deadline/slow-consumer/不可恢复错误走 `OPENING|ACTIVE|DRAINING_FINAL→EXPIRING→EXPIRED`。CLOSED/EXPIRED 只有在 consumer delete 成功或 INFO 确认不存在后才能写入。NATS Binding 的 `streamOpenId` 是 caller/Gateway 为一次逻辑流生成并在 transport retry 间保持稳定的安全 ULID；`requestId/AuthProof` 每次重试必须更新。相同 caller+streamOpenId+requestDigest 返回同一 session，异 digest 冲突。`consumerName=ss_ + lowerBase32NoPad(SHA-256(UTF8(meshId)+0x00+UTF8(streamSessionId)))[0:26]`，必须是单个 NATS token；`filterSubject=a2a.v1.events.<taskId>`；`controllerDeliverySubject=_DELIVER.a2amesh.controller.<meshId>.<consumerName>`，不绑定易失 Controller instance；`callerDeliverySubject=_DELIVER.a2amesh.stream.<authenticatedCallerScope>.<verifiedCallerInstanceId>.<streamOpenId>`。两者均由 State 模板推导，payload 自选 Subject 拒绝。State 在 OPENING 创建时还从当时 active signed bundle持久化configGeneration和完整RFC8785 `consumerConfigJson`；`consumerConfigDigest=SHA-256(UTF8(consumerConfigJson))`。此后generation切换不得改写会话配置，紧急撤销只能使会话进入EXPIRING，不能静默重绑新generation。

Session Controller 必须先创建 OPENING 记录并取得 controller fence，再请求 JS Provisioner 以确定性 consumerName 创建 filtered consumer；只有 consumer配置和实际 INFO 完全匹配后才写 ACTIVE、读取 committed Task snapshot/eventSeq并返回唯一 `StreamSessionOpened`。Consumer使用不绑定易失 instance 的稳定 controller delivery subject；Caller没有 `$JS.API.*`/`$JS.ACK.*` 权限。Controller 根据State session把 frame发布到 caller私有 delivery subject；caller通过固定 `a2a.v1.stream.ack` 请求ACK，Controller经State permit后才发真实JS ACK。崩溃接管重用同一consumerName，并从snapshot-covered/pending/acked/final watermarks恢复；不得创建第二consumer或把其他Task/Principal事件转发。

---

### 5.22 DATA-STREAM-CONFIG-001：固定Task Event Stream状态

| Key | 类型 | 内容 | 保留 |
|---|---|---|---|
| `...:stream-config:A2AMESH_TASK_EVENTS` | HASH | confirmedGeneration,desiredConfigJson,desiredConfigDigest,observedConfigJson,observedConfigDigest,state,currentStreamOperationId,streamOpFence,confirmedAt,lastError | 永久；新generation只追加/推进，不删除历史digest |
| `...:stream-config:operation:<streamOperationId>` | HASH | generation,rolloutLeaseId,rolloutFencingToken,requestDigest,desiredConfigDigest,expectedObservedConfigDigest,state,currentEpoch,resultJson,resultDigest,createdAt,updatedAt | 至少与generation/rollout tombstone同周期；同ID不可覆盖 |
| `...:stream-config:operation:<streamOperationId>:epoch:<streamOpEpoch>` | HASH | streamOpKind,challenge,brokerRequestDigest,executionState,executorInstanceId,executionFence,executionLeaseUntilMs,responseJson,responseDigest,completedAt | 永久随operation保留；epoch事实不可覆盖 |

状态固定为`PENDING_INFO→INFO_EXECUTING→PENDING_CREATE|PENDING_UPDATE|CONFIRMED|FAILED_CLOSED`，CREATE/UPDATE后只能进入`PENDING_VERIFY_INFO→INFO_EXECUTING→CONFIRMED|FAILED_CLOSED`。State不得由CREATE/UPDATE success直接写CONFIRMED；只有fresh INFO重建的完整config digest相等才可确认。streamName/storage或需破坏性重建的漂移固定FAILED_CLOSED，禁止自动DELETE/PURGE/recreate。

---

## 6. 原子函数

### 6.1 `claim_message`

输入：

```text
verifiedCredentialIdentity,assertedPrincipal,aliasGenerationObserved,authRequestClaim,
targetAgentId,operation,skill,toolRisk,workspaceAlias,messageId,payloadHash,canonicalCommandJson,
configGeneration,policySnapshotHash,requestSize,requestDeadlineMs,queueDeadlineMs,nowMs,retentionMs,taskTemplate
```

行为：

1. State Service 在进入本函数前已通过通用 `claim_auth_request` 完成 AuthProof 验证/replay claim；本函数调用 `resolve_principal` 复核 credential/alias，并把最终 Principal、principalType、credentialId、aliasGeneration、configGeneration 与 policySnapshotHash 固化进 Task。
2. dedupe 已存在且 hash 相同：返回原 taskId 与当前 Task，`DUPLICATE_SAME`，不重复占用 admission。
3. dedupe 已存在且 hash 不同：返回 `DUPLICATE_CONFLICT`，不修改任何状态。
4. 新请求调用与 `authorize_capability`、`admit_task` 相同的规则，原子复核 grant generation、大小、全局/Principal 上限和 queue deadline。
5. 通过后由 State Service 生成 taskId/contextId，创建 dedupe、immutable canonical command、Task、QUEUED admission reservation、状态/时间/Context/Principal/Agent 索引、`eventSeq=1` 的 `TASK_SUBMITTED` outbox和 `BLOCKED_ADMISSION` durable dispatch intent，返回 `CREATED`；若operation是`SendStreamingMessage`，同一CAS额外生成canonical `SUBMITTED` StreamResponse首帧并写`stream-claim-initial`及其digest，后续Stream Session只能复用该bytes；不得在本函数直接令 dispatch due。
6. Redis Function 原子执行不等于通用 rollback：实现必须先完成全部可失败校验，再执行不会失败的有界写命令；禁止依赖脚本异常撤销已经发生的写入。

#### 6.1.1 `resolve_principal`

输入为已验证的 `authMethod + credentialId/NKey/issuer+clientId` 和配置 generation。函数读取 Credential、检查 status/expiry，再至多解析一个显式 alias；不存在、禁用、过期、alias 环或 generation 冲突均 fail closed。返回：

```text
principalId
principalType
credentialId（非 secret）
aliasGeneration
```

业务请求不得直接传 principalId 作为该函数输入。

### 6.2 `transition_task`

`a2a.v1.state.task.transition`是以`operation`判别的closed tagged union；未知operation或variant字段混用拒绝。普通业务迁移使用`operation=STATE_TRANSITION`，输入恰为：

```text
operation,taskId,expectedVersion,allowedFromStates,toState,
newTaskJson,nowMs,fencingToken,phase/progress,eventType
```

校验：

- Task 存在；
- version 匹配；
- from→to 合法；
- owner 写入时 fencing token 匹配；
- 若Task存在`rootTaskId`对应的Plan映射，`STATE_TRANSITION`不得把root Task写入任一终态；root终态只能由`a2a.v1.state.plan.transition(operation=FINALIZE_BUSINESS)`在同一CAS重算Plan事实后同步写入。普通transition即使携带有效owner/fence、正确version和合法from→to也必须零写入拒绝；
- 函数内部为外部可见 mutation 分配 `eventSeq=current+1`；调用方不能提供、跳号或复用序列；
- 终态不可迁出。

同一函数在完成全部预校验后更新 Task HASH、state ZSET、updated ZSET 和 Context/Agent 索引，递增 Task version/eventSeq，并写入 `<taskId>:<eventSeq>` outbox 与 Task outbox head。写阶段只使用已验证参数和有界、不会因业务条件失败的命令；不得以运行时异常模拟回滚，也不得先发布 JetStream 再补写 Task。

#### 6.2.1 `get_task_command`

该只读State RPC只接受`task-supervisor:<targetAgentId>:<instanceId>`。输入固定为`taskId,dispatchId,dispatchAttempt,claimToken,expectedPayloadDigest`；State要求dispatch仍为当前CLAIMED或SENT、claim token/attempt/targetAgentId全匹配、Task未终态且canonical command digest未变，才返回创建Task时冻结的exact `canonicalCommandJson,payloadDigest,requestHash,configGeneration,policySnapshotHash`。不得按当前config或DispatchTask payload重建command；其他Agent/Supervisor、过期claim和旧recovery dispatch统一拒绝并审计。

#### 6.2.2 `register_containment_attestation`

`a2a.v1.state.task.transition`的containment operation是closed `REGISTER_CONTAINMENT|READ_CONTAINMENT` union。REGISTER request字段恰为`schemaVersion,operation,taskId,executionAttempt,expectedVersion,ownerInstanceId,fencingToken,attestationJwsJson,attestationJwsDigest,authProof`。只接受当前Task Supervisor NKey；State Service在进入Redis Function前验证Runtime §8.5.1 exact General JWS bytes/digest、Supervisor signer及active component NKey，Function再要求Task为WORKING、owner/instance/attempt/fence/version匹配，且payload内configGeneration/policySnapshotHash等于Task冻结值、agentId等于ownerAgentId、workspaceAttemptId/mount/network/profile/binary与active signed profile和本次launcher观察一致。REGISTER成功返回`ContainmentRegistrationResultV1`，字段恰为`schemaVersion,taskId,executionAttempt,containmentAttestationRef,attestationJwsDigest,registeredTaskVersion,resultDigest`；resultDigest为排除自身后RFC8785 SHA-256。

READ request字段恰为`schemaVersion,operation,taskId,executionAttempt,ownerInstanceId,fencingToken,expectedContainmentAttestationRef,expectedAttestationJwsDigest,authProof`。State只接受同一current owner/attempt/fence且Task仍WORKING，要求Task字段和attempt containment key的ref/digest均等于expected值；`ContainmentReadResultV1`字段恰为`schemaVersion,taskId,executionAttempt,containmentAttestationRef,attestationJwsJson,attestationJwsDigest,registeredTaskVersion,resultDigest`，从不可变key读取exact ASCII JWS bytes，resultDigest同样排除自身后计算。READ不递增Task version/eventSeq、不写第二个audit/outbox；同存储状态确定性返回逐字节相同result，错ref/digest/owner/fence/attempt统一零写入拒绝。

attempt containment key不存在时，同一Function写exact JWS、Task最新ref/digest、递增version/eventSeq并追加`CONTAINMENT_ATTESTED` outbox/AuditEnvelope及exact RegistrationResult；`containmentAttestationRef=state:v1:task:<taskId>:attempt:<attempt>:containment`。已存在且attestationId+digest+bytes均相同则逐字节返回首次RegistrationResult且不重复event/audit；同ID异digest、同digest异bytes、已有该attempt其他attestation或任一绑定漂移均409/零写入。只有REGISTER Function成功、durable audit已入队且READ逐字节返回同一ref/digest/JWS后Supervisor才可启动Runtime/effect；Redis Function不做外部网络或OS检查，只消费State Service已验证且与request exact bytes绑定的launcher observation。

### 6.3 `acquire_lease`

`a2a.v1.state.lease.acquire` request是closed `INITIAL|RECOVERY_PROVISIONAL` union，共有字段恰为`schemaVersion,operation,taskId,dispatchId,dispatchAttempt,claimToken,targetAgentId,supervisorInstanceId,commandDigest,leaseOperationId,requestDigest,authProof`；RECOVERY_PROVISIONAL额外且仅额外含`recoveryOperationId`。`leaseOperationId=lowerhex(SHA-256(ASCII("a2amesh-lease-acquire-v1")||0x00||RFC8785_UTF8({schemaVersion,operation,taskId,dispatchId,dispatchAttempt,claimToken,targetAgentId,supervisorInstanceId,commandDigest,recoveryOperationId|null})))`；`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`，业务幂等scope固定为`targetAgentId+0x00+taskId+0x00+operation+0x00+leaseOperationId`，不含transport requestId/AuthProof。固定identity `{"claimToken":"claim-01","commandDigest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","dispatchAttempt":1,"dispatchId":"dispatch-01","operation":"INITIAL","recoveryOperationId":null,"schemaVersion":"1","supervisorInstanceId":"sup-01","targetAgentId":"agent-01","taskId":"task-01"}`必须得到`00d0883a369fb1fce14905b55931bc713a72e6666e73331f3dc7a6f3e86c84ee`。INITIAL要求Task=SUBMITTED、admission=SELECTED、当前SENT dispatch/slot/target匹配、无owner lease；RECOVERY_PROVISIONAL要求Task=WORKING、admission=RUNNING、当前SENT dispatch的`dispatchMode=RECOVERY_RESUME`、recoveryOperation记录绑定同一过期old fence/fromAttempt/toAttempt、旧lease已过期且无另一个recovery provisional owner。两者都要求cancelRequested=0、未终态、未过hard deadline、command/config/policy digest匹配。

成功CAS从同一Task fence counter取得永不复用的新token，写有短TTL的provisional owner/lease及operation绑定，并在`...:task:<taskId>:lease-operation:<leaseOperationId>`持久保存`operation,scope,requestDigest,resultJson,resultDigest,committedMs`后返回`ExecutionLeaseV1`；在匹配的dispatch accept成功前不得启动Runtime、创建effect或发布progress。INITIAL不提前改变Task/admission；RECOVERY_PROVISIONAL也保持Task=WORKING、admission=RUNNING及全部queued/reserved/running计数不变。失败、超时或cancel只撤销本provisional lease并推进tombstone fence；不得释放既有RUNNING admission。相同leaseOperationId+requestDigest在State提交后回复丢失时逐字节返回首次lease/result且不发新fence、不增attempt；同ID异digest零写入409。

### 6.4 `renew_lease`

仅当ownerAgentId、ownerInstanceId、fencingToken全匹配时`PEXPIRE`。`a2a.v1.state.task.heartbeat`映射的独立`heartbeat_task`只接受当前Task Supervisor，在同一受控调用中更新`lastHeartbeatMs`与独立`freshnessVersion`，读取`cancelRequested`；纯heartbeat不递增Task `version/eventSeq/status.timestamp/ETag`，仅phase/progress变化或30秒采样点才作为普通Task mutation写外部outbox。失败后旧Supervisor必须立即停止副作用。

### 6.5 `request_cancel`

- `CANCELED`：返回当前 Task；`COMPLETED/FAILED/REJECTED`：固定 `TaskNotCancelableError`；
- 若为 SUBMITTED、dispatch 尚未 ACCEPTED、无 effect attempt：**不论是否已有 provisional owner/lease**，同一原子函数设置 `cancelRequested=1`，撤销 provisional lease/owner并推进 fence，Task→CANCELED，dispatch→ABORTED，释放 QUEUED/SELECTED admission reservation，写唯一终态 outbox并返回终态；该 CAS 与 `accept_dispatch_and_start` 竞争，只有一个成功，旧 owner/token 此后全部拒绝；
- 其他非终态：设置`cancelRequested=1`、version+1并写`TASK_CANCEL_REQUESTED` outbox；若存在尚未accept的RECOVERY_PROVISIONAL lease，同一CAS撤销该provisional owner并推进fence，但保留既有RUNNING admission直到当前/接管Supervisor证明进程与effect安全后提交终态；
- 返回 owner instance，Core 可发 control message 加速；Supervisor heartbeat 与任何新 owner/recovery claim 必须以 Redis cancel fact 为准；
- 有 owner 时真正 canceled 由 Supervisor 确认进程退出和 effect 汇总后 transition。

### 6.6 `upsert_card`

- 校验 active config generation、bundle 允许候选、publisher lease 和最新 fencing token；
- card generation 小于等于当前时拒绝/幂等；
- 删除旧 skill/binding 索引；
- 写 Card、meta 和新索引；
- Card JSON 已由官方 SDK 验证；
- heartbeat 不调用本函数。

### 6.7 `prepare_effect_intent` / `begin_effect_attempt` / `start_effect` / `complete_effect`

- `prepare_effect_intent` 由 State 分配 effectIntentId，按 `taskId+stepId+logicalEffectKey` 唯一 CAS；同 requestHash 返回 intent，不同 hash 冲突；
- `BeginEffectAttemptRequestV1`字段恰为`schemaVersion,effectIntentId,taskId,executionAttempt,ownerInstanceId,fencingToken,containmentAttestationRef,containmentAttestationDigest,expectedIntentVersion,expectedPreviousAttemptId,requestHash,authProof`，首次expectedPreviousAttemptId显式null。`begin_effect_attempt`只接受当前Task Supervisor；除不存在attempt或前一attempt由可信evidence证明`FAILED_BEFORE_CALL/NOT_APPLIED`且retry policy允许外，还必须在同一CAS要求Task=WORKING、当前owner/attempt/lease/fence有效、Task当前containment ref/digest与request完全相等、attempt containment key存在且exact JWS仍绑定同Task/attempt/config/policy，且payload containmentLevel=MEDIATED。满足后才原子创建唯一PREPARED attempt并把ref/digest复制进attempt；`UNKNOWN/APPLIED`、SANDBOXED_READ_ONLY/UNMEDIATED、缺/错/旧ref或digest全部零写入拒绝；
- `StartEffectRequestV1`字段恰为`schemaVersion,effectAttemptId,effectIntentId,taskId,executionAttempt,ownerInstanceId,fencingToken,containmentAttestationRef,containmentAttestationDigest,expectedAttemptState,expectedAttemptVersion,staleAfterMs,authProof`。Adapter在PREPARED后仍须校验workspace lease、active emergency revocation和policy snapshot；`start_effect`在写APPLYING前再次以同一CAS重验Task current attempt/lease/fence、Task ref/digest、不可变containment key、effect attempt已存ref/digest、MEDIATED level和全部请求字段，随后才写APPLYING/applyingMs/staleAfterMs。只有该成功响应后才能调用provider；直接调用RPC、REGISTER/READ前调用、旧attempt/fence/ref/digest或本地伪造ContainmentBinding全部零写入且零provider调用；
- `complete_effect` 只能由持有有效 fencing token 的 owner 写 `APPLIED/FAILED/UNKNOWN` 和脱敏回执摘要；超时、连接断开或无法证明 provider 未执行时必须写 `UNKNOWN`；
- `complete_effect` 写 `UNKNOWN` 时原子创建唯一 reconciliation case 和告警 outbox；
- `resolve_reconciliation_case` 只由具备运维 capability、有效 claim fencing 和证据引用的 Reconciliation Service 调用，把 `UNKNOWN` 转为 `APPLIED/COMPENSATED/FAILED`；
- effect、case、Task `reconciliationRequired` 汇总、Task version/eventSequence、audit 和 outbox 必须原子更新；
- `UNKNOWN` 存在时，Task 不得自动开启新 attempt，也不得写 `CANCELED`。
- 已终态 Task 只增加 version 和对账结果，不改变标准 terminal state。
- `start_effect` 在同一CAS写`APPLYING/staleAfterMs`并把`effectAttemptId`加入`...:effect:stale-due`；`complete_effect`在同一CAS从该索引移除，旧attempt/terminal状态不得重新入索引。
- `scan_stale_effects`只接受`effect-reconciler`经`a2a.v1.state.effect.scan-stale`提交closed `EffectStaleScanRequestV1`。`operation=ACQUIRE_SCANNER`时字段恰为`schemaVersion,operation,scanOperationId,scannerInstanceId,leaseMs,nowMs,authProof`，State以一个CAS取得/接管`effect:scanner-lease`并返回单调scannerFencingToken/leaseUntilMs；未过期的其他owner返回BUSY。`operation=SCAN_ONE`时字段恰为`schemaVersion,operation,scanOperationId,scannerInstanceId,scannerFencingToken,effectAttemptId,expectedStaleAfterMs,nowMs,authProof`；State先校验scanner lease/fence、持久due成员、owner lease已失效、staleAfterMs<=now和operation policy，再在一个CAS中写`UNKNOWN`、移除due、创建唯一reconciliation case/告警outbox，并把exact result写`effect:scan:<scanOperationId>`。两种response共用`EffectStaleScanResultV1` closed variant：ACQUIRE恰含`schemaVersion,operation,scanOperationId,scannerInstanceId,scannerFencingToken,leaseUntilMs,resultDigest`；SCAN_ONE恰含`schemaVersion,operation,scanOperationId,effectAttemptId,previousState,newState,caseId,removedDue,resultDigest`；resultDigest排除自身后RFC8785 SHA-256。同ID同digest逐字节返回原结果，异digest/旧scanner fence冲突；State重启、多scanner、claim后/UNKNOWN后丢响应均不得重复provider调用或case。

### 6.8 `authorize_capability`

输入为服务端解析后的 Principal、targetAgentId、operation、skill、toolRisk、workspaceAlias 和 grantGeneration。函数读取有效 grant 并要求全部维度匹配；generation 漂移、过期、禁用或无匹配项统一拒绝，不产生 Task/outbox/队列副作用。

### 6.9 `admit_task` / `select_admission_for_dispatch` / `release_admission`

- 原子检查全局与 Principal 的 queued 上限、`reserved+running` 容量上限和 queue deadline；
- 成功时写 admission task、计数和公平队列位置；失败不创建执行 attempt；
- `select_admission_for_dispatch` 严格执行 DATA-ADMISSION-001 的持久 DRR，从 Principal FIFO head 选择；只有同时满足全局和 Principal 的 `reserved+running<maxRunning` 才可原子 `QUEUED→SELECTED`、`queued--/reserved++`、生成 slotToken，并把匹配 dispatch `BLOCKED_ADMISSION→PENDING` 加入 due；
- `accept_dispatch_and_start` 才把 SELECTED→RUNNING并原子 `reserved--/running++`，不二次增加占用总量；若 dispatch/cancel 在此前失败，按 slotToken 幂等 `reserved--` 并 RELEASED；
- queued 超限返回 caller-scoped overload，Runtime/State 不可用返回 service unavailable；
- Task 出队、取消、终态或超时必须按当前 admission state/slotToken 幂等释放唯一一个 queued/reserved/running 计数；任一计数不得为负。

`claim_message` 对新 Task 内联执行同一套授权和 admission 规则；独立 `authorize_capability/admit_task` 只用于预检、已有 Task 的新 attempt 或编排子步骤，不能替代最终提交时的原子复核。

State Service内置唯一`Admission Scheduler`循环，不是任意Dispatch Worker私自调用：实例先以`...:admission:scheduler-lease`取得短租约/fence，再由定时器读取持久Principal ring/FIFO。其内部Redis Function request恰含`schemaVersion,selectorOperationId,expectedCursor,nowMs,principalVisitLimit,authProof`；同一`selectorOperationId`记录requestDigest/result，重试逐字节返回，异digest拒绝。`AdmissionSelectionResultV1`字段恰为`schemaVersion,selectorOperationId,selected,taskId,dispatchId,principalHash,slotToken,round,nextCursor,resultDigest`；无可选项时selected=false且task/dispatch/principal/slot均显式null。Function在一个CAS内复核cursor/租约、执行DRR、`QUEUED→SELECTED`计数、生成slotToken并令对应dispatch进入due；租约失效、cursor冲突或并发selector只返回BUSY/冲突且零写入。`TEST-DRR-001`必须以两个State实例/丢响应覆盖该内部触发；不存在未声明的NATS admission subject，Dispatch Worker只能claim已进入due的dispatch。

### 6.10 `project_derived_view`

- 以 taskId/eventSeq 去重，只更新可重建的消费水位、通知状态或统计视图；
- `taskVersion <= Redis Task.version` 时不得覆盖 Task JSON、state、phase、Artifact 或 terminalReason；
- 任何事件都不得把终态改回非终态；
- 发现 JetStream 事件领先 Redis committed eventSeq 时进入一致性告警和人工恢复，不能把事件当作无条件写回权威快照的命令。

### 6.11 `create_upload` / `finalize_artifact` / Artifact hold / `commit_typed_source_and_refs` / `request_artifact_delete`

- `create_upload` 原子校验 Task owner/capability、大小/MIME/配额和 active policy generation，写上传会话及 owner snapshot reference；
- `finalize_artifact`只接受Artifact Broker已验证的object key、size、SHA-256、media type、objectVersionId和scan receipt；原子重新校验expected Task/Artifact version、Task state/cancel、owner/fencing、policy generation、retention lock，再调用同一`commit_typed_source_and_refs` helper，在一个CAS写AVAILABLE metadata、TASK source canonical snapshot、Task Artifact/forward ref、dependent reverse index、version/eventSequence、audit和outbox；对象在CAS前不可成为可见Task source，State失败只留下受控orphan；
- completion 的 idempotency key + uploadId 重入返回原 Artifact，不重复事件；
- `create_artifact_hold` 只允许 `ops.artifact.hold` 或受信 Reconciliation/Security service Principal；校验 expected Artifact version、ACTIVE policy、sourceCase/sourceTask 存在和 `artifactId+Idempotency-Key` requestDigest，State 分配 holdId，同一 CAS 写 append-only hold、active hold set、retention lock/legalHoldIds、Artifact version和 AuditEnvelopeV1；同 digest 返回原 hold，异 digest 409；
- `renew_artifact_hold` 要求同 capability、expected Artifact/hold digest、ACTIVE hold；expiresMs 只能延长（legal hold 可为 null），同一幂等键相同请求返回现状。`release_artifact_hold` 只把 hold 置 RELEASED/写 releasedMs，不删记录，并在同 CAS 从 active set/retention lock 移除；过期 sweeper 使用相同 expected digest/version CAS；
- NATS唯一入口`a2a.v1.state.artifact.source.commit`只允许Artifact adapter NKey，且State仍必须校验嵌入AuthProof的TASK/CASE/EVIDENCE source owner或`ops.artifact.ref` capability；transport身份不能按某个目标Artifact替caller授权。request恰为Artifact §5.5的`TypedSourceCommitRequestV1`，sourceType/sourceId/sourceVersion必须来自source-centric HTTP path并进入requestDigest；
- `commit_typed_source_and_refs`是唯一ref mutation writer；不存在独立增删ref或按Artifact target path的writer。Function先从source current pointer/forward set取得oldRefs，以old/new ref的artifactId并集为touched set，要求expectedArtifactVersions恰覆盖该集合；再验证source canonical bytes/version/owner、五字段ref tuple逐项绑定path且digest等于目标contentSha256、所有new Artifact non-DELETING。一个CAS写source VISIBLE/current、old/new差集的`artifact:<id>:refs`与`artifact:ref-source:<type>:<id>`、active*Refs/retention lock、每个touched Artifact恰一次version、source-commit exact result及audit/outbox；空newRefs是通过新source version移除全部引用，不是DELETE ref调用；
- 同commitId/scope/requestDigest/exact bytes逐字节返回`TypedSourceCommitResultV1`，异digest、乱序/重复/四字段ref、path/ref漂移、expected集合少报被移除目标/多报无关目标或Artifact版本漂移均零写入。Function commit-before-reply重试不得重复Artifact version/audit/outbox；DELETE/Reaper先赢则source不可见/原version不变，source commit先赢则DELETE读取retention ref后409/423；
- `request_artifact_delete`原子读取`artifact:<artifactId>:refs`、全部active ArtifactHold、最低保留时间和policy；有任一锁返回409/423，不改状态；无锁才把状态置为DELETING并入队，不能在对象仍存在时写DELETED。Reaper在物理删除前再次以expected Artifact version读取refs/holds；与source-commit/create-hold竞争时只有一个CAS成功，若锁先提交则删除拒绝，若DELETING先提交则后续source commit/hold拒绝且source保持不可见或必须重试到新Artifact。

### 6.12 `prepare_genesis` / `commit_genesis` / `recover_genesis` / `stage_config` / `stage_gate_evidence` / `activate_config`

- `prepare_genesis`只接受空State、无accepted generation、双签g1和bootstrap固定deployment/mesh/node/nonce/genesisWormOrigin；先按Config §7.1 canonical origin/nonce/NFC/base64url/lowerhex公式重算exact intentUri/commitUri，再验证`GenesisIntentPayloadV1` exact字段、UTC三位毫秒Z createdAt、Config Controller signer、protected header、General JWS bytes及`intentJwsDigest`。只对exact intentUri执行WORM create-if-absent，再校验绑定同URI/digest的fsync主机PREPARED marker后CAS Redis PREPARED；同URI+exact bytes/digest幂等，备用URI或任一异digest冲突/P0；
- `commit_genesis`只在WORM intent/主机/Redis PREPARED三方intentUri/intentJwsDigest一致时验证Config §7.1 exact `GenesisCommitReceiptPayloadV1`、UTC三位毫秒Z committedAt与State signer，并只对同公式exact commitUri执行create-if-absent；该WORM成功是唯一accepted线性化点。随后主机与Redis COMMITTED可分步幂等物化，任一未完成时业务fail closed；
- `recover_genesis`只从bootstrap重算两个URI，先GET exact commitUri：存在则重验receipt/intent exact bytes、signer、time、result、URI/digest并补齐COMMITTED；明确404才GET exact intentUri并按同node+digest补齐PREPARED/继续commit；认证失败、超时、非404错误均UNKNOWN/fail closed。它不得依赖object listing、本机marker、Redis或bundle来发现URI；主机/Redis COMMITTED而无有效WORM commit立即P0且不激活；
- `stage_config`对非genesis只接受已验证签名/hash/schema/deploymentId字段的单调generation；bundle只能含requiredGateTestIds，出现gateEvidenceRefs/report URI/hash即拒绝；g1必须已完成上述COMMITTED saga。函数先完整物化generation前缀的immutable index tree/indexRootDigest，再写不可变bundle和STAGED audit；
- `stage_gate_evidence`的State Service预验证器只接受已STAGED bundle和有效GateEvidenceRecord JWS；先验证General JSON `signatures[]`每项恰含protected/signature、解码kid唯一且UTF-8严格升序、threshold满足，再读取content-addressed/WORM reportUri的exact bytes并校验SHA-256/PASS/0 skip/bundle+ACL+environment绑定，确定性重算aclDigest；它逐项从record的readyAttestations读取内嵌`attestationJwsJson` ASCII bytes，校验canonical envelope、digest、State attestation signer、payload全字段/TTL/required slot、authProofDigest，并从signed bundle/deployment descriptor重算`RequiredSlotSetV1`，要求stable slot集合与`deliveryProfile.requiredSlots[]`及readyAttestations一一相等，并由包含exact JWS string的数组重算readySetDigest。热receipt key存在时必须与内嵌bytes/digest相同；不存在不阻止基于State签名的验证，但current pointer仍须证明本次stage无NACK且receipt未过期。`evidencePurpose=CANDIDATE_TEST`时CANDIDATE tuple只能写隔离staged evidence且rollout字段显式null；`evidencePurpose=PRODUCTION_ACTIVATION`时每个slot必须是PRODUCTION_GATED，且rollout记录必须`state=MAINTENANCE,trafficGate=CLOSED`、lease未过期，current pointer的rollout/fence/deployed ACL/stream/environment全等；未知purpose或混合plane拒绝。`StageGateEvidenceRequestV1`字段恰为`schemaVersion,evidencePurpose,generation,evidenceRecordJws,evidenceSha256,expectedBundleContentSha256,expectedAclDigest,rolloutLeaseId,rolloutFencingToken,expectedRevision,requestDigest,authProof`；CANDIDATE_TEST时三个rollout字段显式null，PRODUCTION_ACTIVATION时三者必须等于current维护rollout。requestDigest排除自身/authProof后按RFC8785 SHA-256；不得把只有authProofDigest/digest而无内嵌valid receipt bytes的记录视为READY。随后Redis Function只对随请求提供的canonical record bytes/digests和本地current pointers做比较，以generation:evidenceSha256不可变写入并审计，不在Lua/Function内执行网络I/O。同digest同bytes幂等、异bytes冲突；
- NATS `a2a.v1.state.config.activate`只接受signed Config Controller NKey，request必须逐字段符合Config §4.3的closed `RolloutControlRequestV1`；operation固定为`PREPARE_ROLLOUT,RENEW_ROLLOUT,ENTER_MAINTENANCE,TAKEOVER_ROLLOUT,ACTIVATE,RESTORE_ACTIVE_ACL,FINISH_ROLLOUT,MARK_FAILED_CLOSED`。PREPARE重验显式candidateEvidenceSha256已stage、purpose=CANDIDATE_TEST、bundle/ACL/report/environment/expiry匹配后，才创建唯一lease/fence/revision并把该SHA写rollout；它拒绝productionEvidenceSha256。RENEW只允许未过期current owner；ENTER同CAS关闭traffic；TAKEOVER还必须具有独立`ops.config.recover`机器Credential、旧lease已过期或valid signed handoff，并在不打开traffic的前提下递增fence/revision。State以active pointer判向：old generation只允许继续pre-CAS步骤或RESTORE，candidate generation只允许FINISH，其他组合MARK_FAILED_CLOSED；旧owner/fence从TAKEOVER CAS后永久拒绝。ACTIVATE额外重验已stage productionEvidenceSha256/ACL/stream config digests并写rollout；RESTORE重验old ACL+stream且active pointer未切换；FINISH重验candidate ACL+stream/health；只有后二者可同CAS打开对应流量门。任一阶段的operation ledger/result/audit/outbox与状态同CAS；同scope/key/digest逐字节返回，异body、跳阶段、过期owner/fence零写入。active CAS成功后RESTORE_ACTIVE_ACL永久拒绝。
- `activate_config`的State Service在CAS前完成同样的本地缓存/不可变URI验证并传入verified digests；Redis Function除previousGeneration、所选READY内嵌receipt全字段/TTL/attestationJwsDigest、对应component-current无NACK、indexRootDigest外，还要求显式evidenceSha256/deployedAclDigest/deployedStreamConfigDigest/rolloutLeaseId/rolloutFencingToken/expectedRevision，重验GateEvidence record签名/expiry且`evidencePurpose=PRODUCTION_ACTIVATION`、每份内嵌State签名READY receipt exact bytes/digest及其所绑定authProofDigest、stored report digests、`deployedAclDigest=record.aclDigest=rollout.candidateAclDigest`、`deployedStreamConfigDigest=record.deliveryProfile.taskEventStreamConfigDigest=rollout.candidateStreamConfigDigest=DATA-STREAM-CONFIG-001 CONFIRMED digest`、current rollout owner/lease/fence/revision且`state=MAINTENANCE,trafficGate=CLOSED`，以及由包含exact JWS string的readyAttestations重算的readySetDigest；每个所选receipt还必须`readinessPlane=PRODUCTION_GATED`、rolloutLeaseId/fence/deployed ACL/stream/environment与current rollout/component-current完全相等，组件已在生产GATED_PASSIVE且只开放health/config.ready、不承接业务流量。production READY缺失/过期、candidate-only evidence、wrong environment/digest、current NACK或旧fence均零写入。热receipt key缺失不替代或否定record内签名，但receipt过期/current NACK仍拒绝本次activate。后续renew写新receipt/current pointer不使尚未过期的所选READY失效。CAS只替换含bundle/evidence/ACL/stream config/READY digest/rolloutLeaseId的active root pointer、令rollout→ACTIVATED并写operation result/activation audit/outbox，不在激活脚本内批量重写运行时索引；开流量是CAS后的独立受审步骤。CAS前失败可由未过期current owner或过期后受信TAKEOVER owner重验old ACL/stream并RESTORE；CAS后只允许TAKEOVER/FINISH，任一路径证据不足都保持FAILED_CLOSED；
- 回滚也是更高 generation 的普通激活，禁止 active pointer 降序；
- Credential/Alias/Grant/Card publisher/Artifact/Runtime/Tool policy 不得跨 generation 混合写入。

### 6.13 `acquire_reconciliation_claim` / `renew_reconciliation_claim` / `release_reconciliation_claim` / `expire_reconciliation_claim` / `escalate_reconciliation_case` / `scan_reconciliation_due` / evidence与resolution writers

- NATS `recon.claim`只接受Reconciliation §5 exact `ReconciliationClaimControlRequestV1`并按closed operation映射五个具名writer；`recon.scan-due`只接受exact `ReconciliationDueScanRequestV1`并映射scanner acquire/renew/scan。State ingress先校验signed recon NKey/AuthProof/replay、closed字段/reasonCode/operationId/requestDigest，Redis Function只接收已验证actorPrincipalHash和server time；
- `scan_reconciliation_due`的ACQUIRE_SCANNER在无未过期lease时从scanner-fence取得更高token并创建lease；RENEW_SCANNER只接受current owner/fence/revision且未过期；SCAN_DUE只读取`recon:due`中score<=serverNowMs的最多limit项，在返回前逐项读取case并固化observed tuple、deterministic dueOperationId和32字节随机candidateToken到due-scan ledger。ZSET不是事实：漂移项由同CAS清理且不进入candidates；同scanOperationId/digest逐字节返回相同candidate bytes。接管只发更高scanner fence，旧scanner的scan及EXPIRE/ESCALATE全部拒绝；
- `acquire_reconciliation_claim`要求OPEN/revision匹配且无未过期owner；无owner时直接从claim-fence发更高token，旧owner逻辑过期时同CAS先产生ClaimExpired再发新token。两者都写owner/instance/lease、claimedInCurrentOpenCycle=true、重排CLAIM_EXPIRE due、移除本cycle ESCALATE due并只令case revision+1；`renew_reconciliation_claim`要求current Principal/instance/token/revision且未过期，发更高token并重排due；`release_reconciliation_claim`要求current且未过期，发更高tombstone token、清owner/expiry并移除due，不改workflow/escalation；
- `expire_reconciliation_claim`与`escalate_reconciliation_case`还必须校验current scanner lease/fence、持久scan candidate token及observed tuple。EXPIRE要求serverNow>=current observed expiry，发更高claim tombstone、清owner/expiry/expire due；ESCALATE要求OPEN、current cycle从未claim、未escalated且serverNow>=current SLA due，只提高priority/escalated并移除escalate due。二者revision+1且分别只写一次稳定audit/outbox；
- create case与reopen必须同CAS递增claimSlaCycle、设置claimedInCurrentOpenCycle=false/claimSlaDueMs并插入ESCALATE due；首次ACQUIRE移除该due且release/expiry不重新插入。resolve/close/reopen清除claim expiry due并从claim-fence推进tombstone，任何evidence/resolve即使scanner未处理也必须以serverNow拒绝逻辑过期token；
- 五个claim writer都在同一CAS写case/counter/due、`claim-operation` exact result、audit/outbox；scanner lease/scan也各自写持久operation result。相同operationId/scope/requestDigest逐字节返回原result且不增加revision/token/audit，异digest、旧revision/fence/candidate tuple零写入；
- `append_reconciliation_evidence`必须把完整canonical EvidenceRecord、`sourceVersion/sourceDigest`和完整typed ref集合一次提交给`commit_typed_source_and_refs`；Redis Function先验证case/revision/claim，再同CAS写evidence VISIBLE、`artifact:<id>:refs`、`artifact:ref-source:EVIDENCE:<evidenceId>`、case evidence index、retention lock、source-commit ledger、case revision和audit/outbox。只带artifactId而未带canonical source/ref集合、或先让Evidence VISIBLE再补ref，均拒绝；Function提交前crash保持Evidence不可见，提交后crash不产生缺ref；
- 旧 revision、逻辑过期/旧 fencing、重复 idempotency key不同payload均冲突；
- `resolve` 创建 immutable ResolutionRecord 并按 case revision 只追加一次，OPEN→RESOLVED，设置 currentResolutionId、清 claim/due、从counter推进claim tombstone并递增 claimGeneration/revision，保留 escalation；
- `close` 只允许 RESOLVED→CLOSED、无 active claim，按 policy 校验独立 reviewer，保留 current/history，不创建新 resolution；
- `reopen` 只允许 RESOLVED/CLOSED→OPEN，要求新 evidence/reason，清 current/claim、推进claim tombstone、递增claimGeneration/claimSlaCycle/revision、设置新SLA due并插入ESCALATE member，保留 history/escalation；
- 三个操作先检查 `caseId+Idempotency-Key` requestDigest；相同返回原 resultRef，异同冲突，旧 claim fencing 永久失效。详细职责分离见《人工对账与运维操作设计》。

### 6.14 `claim_dispatch` / `mark_dispatch_sent` / `reclaim_expired_dispatch` / `accept_dispatch_and_start` / `expire_dispatch`

- `claim_dispatch`只认领PENDING/due intent。`dispatchMode=INITIAL_START`要求admission=SELECTED且slotToken匹配；`dispatchMode=RECOVERY_RESUME`要求Task=WORKING、admission=RUNNING、slotToken仍为原running slot且recoveryOperationId/attempt绑定未漂移。两者都原子令dispatchAttempt+1，从独立fence counter发放单调claimToken，写CLAIMED/owner/claimedMs/claimExpiresMs并返回含dispatchMode/recoveryOperationId的immutable `DispatchTask`；recovery分支不改admission计数。
- `mark_dispatch_sent` 必须在调用 NATS request 前执行，只允许当前 CLAIMED attempt/token，校验 reply inbox 属于 Worker 后写 SENT/sentMs/replyInboxDigest 并把 claimExpiresMs 延长到本次 request timeout；若函数失败，禁止 publish；
- `reclaim_expired_dispatch` 只允许当前 CLAIMED/SENT 且 `claimExpiresMs<=now`。若 Task 已 cancel/SUBMITTED cancelRequested，则撤销 provisional lease/fence并原子写 CANCELED/ABORTED/release/outbox；若 deadline 未过，则写 PENDING/nextAttemptMs、清 claim/reply 字段并从 fence counter 写入新的 tombstone token，确保旧 SENT reply 和旧 Worker 永久失败；若 deadline 已过则按 `expire_dispatch`；
- `accept_dispatch_and_start` request是closed `INITIAL_START|RECOVERY_RESUME` union，只接受SENT并校验dispatchId/attempt/claimToken、匹配模式的provisional lease/fencing、target/config/payload digest、admission slot、cancel/deadline/effect。INITIAL_START同一CAS写dispatch ACCEPTED、Task SUBMITTED→WORKING、admission SELECTED→RUNNING、reserved--/running++和唯一`TASK_WORKING` outbox。RECOVERY_RESUME额外要求recoveryOperationId与Task当前from/to attempt、过期旧fence完全匹配，Task保持WORKING且admission保持RUNNING；同一CAS只写dispatch ACCEPTED、正式化新owner/attempt/fence、重排recovery due、operation acceptedResult，并写`TASK_OWNER_RECOVERED` outbox，queued/reserved/running均不变且禁止第二个`TASK_WORKING`。任一路径中途均无可观察半状态；
- `expire_dispatch` 仅在 deadline 已过且不存在有效已接受 owner、无 UNKNOWN/APPLYING effect 时执行；若 `cancelRequested=1` 或 Task 已 CANCELED，结果必须是 CANCELED/ABORTED而非 FAILED，否则 intent→DEAD、Task→FAILED并写 outbox；
- Worker 崩溃、NATS timeout 或客户端永久断开均不删除 intent。

### 6.15 `claim_outbox` / `reclaim_expired_outbox` / `mark_outbox_published` / `reschedule_outbox` / `recover_dead_outbox`

- `claim_outbox` 读取 per-Task head/published watermark，只返回 `publishedSeq+1` 且状态 PENDING/due 的 event，原子从 outbox fence counter 取得永不复用的 claimToken并写 claimOwner/token/expires；不同 Task 可并行；
- `reclaim_expired_outbox` 只允许 head event 为 CLAIMED 且 `claimExpiresMs<=now`、payload digest 未变；原子写回 PENDING/due、清 owner、写新的 tombstone token。旧 Relay 的 mark/reschedule 永久因状态/token 不匹配失败；
- `mark_outbox_published` 校验 token、event payload digest 与 PubAck 后写 PUBLISHED/publishedSeq/可选 JetStream streamSeq，并将下一 head 加入 due；
- `reschedule_outbox` 只接受当前 CLAIMED token，保留同一 eventId 并退避到 PENDING，同时推进 tombstone token；event `n` 未完成时 `n+1` 不可被 claim；
- dead outbox写`blockedByDeadSeq`，不得被后续事件越过或按TTL删除。唯一恢复操作`recover_dead_outbox(RecoverDeadOutboxRequestV1,verifiedOperatorContext)`映射`a2a.v1.state.outbox.recover`；request payload恰含`schemaVersion,taskId,eventId,expectedHeadSeq,expectedEventDigest,repairEvidenceUri,repairEvidenceSha256,reasonCode,idempotencyKey,authProof`，其中`taskId/eventId`还必须分别等于Ops API path变量，额外字段拒绝。State ingress先claim AuthProof/replay并只接受独立`ops-recovery` NKey及已验证机器Principal的`ops.outbox.recover` capability，再从content-addressed/WORM URI读取exact evidence bytes，复验SHA-256、JWS signer/expiry/语义绑定；Redis Function不做网络I/O。

  `requestDigest=SHA-256(RFC8785({taskId,eventId,expectedHeadSeq,expectedEventDigest,repairEvidenceUri,repairEvidenceSha256,reasonCode}))`，幂等scope为`taskId+operatorPrincipalHash+Idempotency-Key`。Function必须先读幂等记录：同key同requestDigest逐字节返回原`resultJson`，同key异digest返回409且不读写业务状态。首次请求在一个CAS/Function内同时验证并提交以下谓词：

  1. `outbox:task:<taskId>.blockedByDeadSeq == expectedHeadSeq`且`expectedHeadSeq == publishedSeq + 1`；
  2. `(taskId,expectedHeadSeq)`定位的`outbox:event:<taskId>:<expectedHeadSeq>`存在且`status=DEAD`，`outbox:dead`也含该record的eventId；
  3. record的`taskId == request.taskId`、`eventSeq == expectedHeadSeq`且`eventId == request.eventId`；
  4. record的`payloadDigest == expectedEventDigest`；
  5. 已预验证`OutboxRepairEvidenceV1`绑定相同taskId/eventId/eventSeq/payloadDigest，且`repairAction=RETRY_SAME_EVENT,verificationResult=SAFE_TO_RETRY`。

  全部成立才原子执行`DEAD→PENDING`、从dead移除同eventId、以`nextAttemptMs=now`加入due、清当前claim字段和`blockedByDeadSeq`、递增`recoveryCount`、保存lastDead/lastRecovery evidence/principal/time并追加durable AuditEnvelope/outbox恢复审计。`recoveryId=base64url(SHA-256(UTF8("a2amesh-outbox-recovery-v1")||0x00||UTF8(taskId)||0x00||UTF8(eventId)||0x00||UTF8(operatorPrincipalHash)||0x00||UTF8(idempotencyKeyHash)))`；result payload恰含`schemaVersion,recoveryId,taskId,eventId,eventSeq,status,recoveryCount,repairEvidenceSha256,recoveredAt,resultDigest`，`status=RECOVERED_TO_PENDING`且`resultDigest=SHA-256(RFC8785(result排除resultDigest))`。同一CAS最后写exact `resultJson/resultDigest`幂等记录；任一步失败零写入。State提交后/reply前丢响应时，同key重试必须逐字节返回原result，不能重复递增或重复审计。明确skip事件必须创建新的受审计replacement event，不能删除原dead后静默跨越；原DEAD事实由`lastDead*`和WORM audit永久保留。

### 6.16 `save_plan` / `acquire_plan_lease` / `renew_plan_lease` / `recover_plan_lease` / `reconcile_plan_recovery_step` / `finalize_plan_recovery` / `transition_step` / `finalize_plan_business` / `acquire_workspace_lease`

`save_plan`的请求operation只允许`CREATE_DRAFT|VALIDATE`。`CREATE_DRAFT`要求planId不存在、调用Orchestrator有rootTask授权，写immutable canonical `planJson/planDigest`、完整Step投影、`state=DRAFT,revision=1,recoveryState=NONE`；相同planId+digest幂等返回，异digest冲突。`VALIDATE`要求state=DRAFT、expected revision/digest匹配，在同一受信validator中复核DAG/resultContract/tool/effect/policy并只写`state=VALIDATED,revision+1`及audit/outbox；不得跳过validator或修改planJson。`acquire_plan_lease` **只允许从未拥有者的初始 Plan**：state=VALIDATED、ownerInstanceId为空、plan fence counter=0、所有Step仅PENDING/READY且expected revision匹配；成功的同一CAS取得首个fencing/owner/lease，写`state=RUNNING,revision+1,recoveryState=NONE`并追加`PLAN_RUNNING` audit/outbox。它不能接管lease过期、state=RUNNING、fence>0或显式释放后的Plan。`renew_plan_lease`只接受state=RUNNING且ownerInstanceId+fencingToken+revision全匹配。

`recover_plan_lease`是既有**RUNNING且非终态**Plan的唯一接管入口：除`state=RUNNING`外，还要求owner lease已过期。V1不提供显式release/handoff operation；owner清除、due移除和更高fence只能由State确认逻辑expiry的`recover_plan_lease`或终态CAS完成。`COMPLETED/FAILED/CANCELED`无论是否残留PENDING/READY Step、owner/lease是否过期、recoveryState为何值都永久拒绝takeover。函数是一个有界原子begin/takeover：取得更高fencing、写新owner/lease；若recoveryState=NONE，则令`RECONCILING,recoveryEpoch+1,recoveryRevision+1,recoveryCursorStepId=<stableOrder首Step>,recoveryStartedMs=now`；若已经RECONCILING，则保留epoch/cursor、仅令recoveryRevision+1并接管。任一begin/takeover都写AuditEnvelopeV1。`renew_plan_lease`只接受state=RUNNING；在RECONCILING中只允许当前owner/fence续租，不解除gate。

`acquire_plan_lease`成功进入RUNNING、`renew_plan_lease`成功续租、`recover_plan_lease`接管以及`finalize_plan_recovery`/`finalize_plan_business`任一终态提交，都必须在同一CAS维护`...:plan:due`（RUNNING保留当前leaseUntilMs，终态移除）。Orchestrator的`a2a.v1.state.plan.recovery.scan` request恰含`schemaVersion,scanOperationId,expectedCursor,nowMs,limit,authProof`；State以自身持久due索引按`(leaseUntilMs,planId)`确定性返回候选并写scan ledger，不能接受caller任意planId或把内存列表当发现依据。`PlanRecoveryScanResultV1`字段恰为`schemaVersion,scanOperationId,nextCursor,candidates,resultDigest`；每个candidate恰含`planId,observedLeaseUntilMs,observedOwnerInstanceId,observedFencingToken,recoveryCandidateToken`，按`(observedLeaseUntilMs,planId)`排序，token为State生成的32随机字节base64url无padding并只存在该scan ledger。同ID同digest返回原candidate/result，异digest冲突；Orchestrator随后逐项调用`recover_plan_lease`并携带scanOperationId/recoveryCandidateToken，State再次在CAS确认candidate exact tuple仍due。多scanner、重启、scan响应丢失和终态残留due均有`TEST-PLAN-RECOVERY-001`负例。

`reconcile_plan_recovery_step`每次只处理recoveryCursor指向的一个Step，并在同一Redis Function内校验`state=RUNNING`、owner lease/fence、`recoveryState=RECONCILING`、expectedRecoveryEpoch/recoveryRevision/cursor：PENDING/READY保留；DISPATCHED/RUNNING有childTaskId时读取权威Task，非终态child继续关联且不得重建，terminal child按唯一映射写SUCCEEDED/FAILED/CANCELED，childTaskId指向缺失Task时固定FAILED/`CHILD_TASK_MISSING`并审计；写Step的lastRecoveryEpoch/revision、传播确定的dependency/retryPolicy结果、将cursor推进到下一stableOrder（末尾为`END`），并令recoveryRevision+1。Step更新与cursor推进原子，重放expected revision只能返回既有结果，不能重复创建child；Plan已终态时即使旧expected revision仍匹配也拒绝。

`finalize_plan_recovery`只在`state=RUNNING`、cursor=END、当前owner lease/fence和expectedRecoveryEpoch/recoveryRevision匹配且每个Step的lastRecoveryEpoch=当前epoch（初始PENDING/READY可由本轮no-op标记）时执行；它原子重算Plan aggregate、令recoveryState=NONE/cursor=null、Plan revision+1/recoveryRevision+1并审计。若恢复owner在任一步崩溃，下一owner经`recover_plan_lease`取得更高fence并从持久cursor继续同一epoch。

`a2a.v1.state.plan.transition`唯一request为`PlanTransitionRequestV1`，恰含`schemaVersion,operation,planId,planOperationId,requestDigest,expectedRevision,ownerInstanceId,fencingToken,reasonCode`以及按operation出现的字段。`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`，scope固定为`planId+ownerPrincipalHash+planOperationId`，结果写`...:plan:<planId>:operation:<planOperationId>`并与业务CAS/outbox同原子；同ID同digest逐字节返回，异digest零写入冲突。`operation=TRANSITION_STEP`时额外恰含`stepId,expectedStepStatus,nextStepStatus,childTaskId,resultContractHash`并映射`transition_step`；`operation=FINALIZE_BUSINESS`时额外恰含`desiredTerminalState,expectedStepSetDigest`并映射`finalize_plan_business`。`expectedStepSetDigest=SHA-256(RFC8785(按DATA-PLAN-001 stableOrder升序的Step tuple数组))`，tuple恰含`stepId,stableOrder,status,childTaskId,attempt,resultContractHash,lastRecoveryEpoch,lastRecoveryRevision`，缺省/nullable按显式null编码；State从权威Step HASH重算并回显，caller不得自算替代。业务finalize只允许当前owner、有效lease/fence、`state=RUNNING,recoveryState=NONE`、全部Step终态且权威Step集合digest匹配；State按resultContract/effect/root cancel事实重算唯一结果：全部required Step成功或允许SKIPPED才可COMPLETED；存在不可恢复required FAILED/missing结果为FAILED；仅root cancel已提交、全部active child已停止且无UNKNOWN/不安全APPLIED effect时才可CANCELED。caller提交的desiredTerminalState与重算结果不一致必须拒绝。成功的同一CAS写Plan终态，并同步写其`rootTaskId`的唯一终态、Task version/eventSeq、`TASK_COMPLETED/TASK_FAILED/TASK_CANCELED` outbox、清owner/lease、移除plan due、推进plan fence tombstone并追加`PLAN_TERMINAL` audit/outbox；root Task若已是同一终态只走幂等结果，其他状态/plan映射不匹配零写入。两者不得存在第二个root-terminal writer。

所有Step transition、create/retry child、aggregate/finalize business、workspace/effect派生操作都必须同时要求`state=RUNNING`且recoveryState=NONE；RECONCILING时只有renew、recover、reconcile、finalize-recovery可写，旧fencing永久拒绝。终态后只有query/audit/reconciliation投影可写，不得创建/重试child或迁移Step。

`acquire_workspace_lease`只由State按workspace alias/Task lease/active generation/policy snapshot发放单调`workspaceFencingToken`并维护lease/fence；该token本身绝不授予Runtime、Tool、Task Supervisor或Peer Binding文件系统写权。共享根的唯一writer是Application Core内嵌Merge Broker：它只能由Core-owned受保护本地接口接收typed merge request，向State重新核验lease/fence后，以handle-relative路径同时校验`baseRevision,expectedDiffDigest,activeGeneration,policySnapshotHash`并在一个本地临界区产生唯一受审计commit；Core外部caller不能指定绝对路径、直接打开shared root、代签Core身份或把Redis lease当作已完成文件写。Merge request的精确frame、caller component、replay ledger、五元组CAS与crash时序由NATS §16.9/`TEST-WORKSPACE-FENCE-001`闭合。

### 6.17 `append_task_message` / `claim_input_delivery` / `ack_input_and_resume`

继续现有 Task 不调用 `claim_message`。`append_task_message` 输入 `taskId/contextId/messageId/payloadHash/canonicalMessage/authRequestClaim/expectedVersion`，并原子：

1. 校验 caller ownership、context 匹配、状态仅 INPUT_REQUIRED/AUTH_REQUIRED、未 cancel/terminal/deadline；
2. 使用 `...:task:<taskId>:input-dedupe:<messageIdHash>` 保存 payloadHash/inputIntentId；同 hash 返回既有结果，异 hash 冲突；
3. 追加 immutable Message history 和 `...:task:<taskId>:input:<inputIntentId>`（PENDING/CLAIMED/ACKED、owner fencing、delivery attempt）；
4. 写 `TASK_INPUT_RECEIVED` outbox，但 Task 仍保持等待态。

当前或接管 owner 通过 `claim_input_delivery` 至少一次取得输入；处理器持有有效 Task lease 后调用 `ack_input_and_resume`，由一个 CAS 同时将 input→ACKED、Task waiting→WORKING、version/eventSeq/outbox 更新。重复 ack 返回现状；旧 owner fencing、错误 context 或 cancel 竞争失败。input claim 超时可接管，但同 `messageId/inputIntentId` 不重复追加历史。

### 6.18 `claim_auth_request`

所有受保护 State RPC 在业务函数前调用。输入为 `signer/requestId/authProofDigest/configGeneration/expiresAt/operation/target/replySubject` 及已通过密码学验证的标记；函数创建 `...:auth:replay:<signerHash>:<requestIdHash>`。Key 已存在时无论 digest 相同或不同均返回仅供受信适配层/审计使用的内部 reason `AUTH_PROOF_REPLAYED`，不进入业务函数；所有 HTTP/gRPC/JSON-RPC/NATS wire 必须统一脱敏映射为 `AUTH_PROOF_INVALID`，不得向 caller 暴露“签名错误还是 replay”。合法 transport retry 必须换 requestId/AuthProof，同时保持 messageId、dispatchId 或 operation idempotency key。READY、Get/Cancel、Artifact/Config/Reconciliation、dispatch/outbox 操作均不得绕过。

### 6.19 `claim_recovery_attempt`

`a2a.v1.state.task.recover`只允许目标Agent的Task Supervisor调用。`TaskRecoveryScanRequestV1`字段恰为`schemaVersion,targetAgentId,recoveryScanId,expectedConfigGeneration,requestDigest,authProof`；recoveryScanId为26字符安全ULID，requestDigest=`SHA-256(RFC8785(request排除requestDigest/authProof))`。`recoveryScanIdHash=lowerhex(SHA-256(ASCII("a2amesh-task-recovery-scan-v1")||0x00||UTF8(targetAgentId)||0x00||UTF8(recoveryScanId)))`。State时间是唯一due判据；State按`(leaseUntilMs,taskId)`选择首个due候选，caller不能提交taskId。`TaskRecoveryScanResultV1`字段恰为`schemaVersion,recoveryScanId,requestDigest,status,taskId,recoveryOperationId,recoveryDispatchId,fromAttempt,toAttempt,resultDigest`，status只允许`NO_DUE|RECOVERY_DISPATCH_CREATED|RECONCILIATION_REQUIRED`；NO_DUE时五个业务nullable字段`taskId,recoveryOperationId,recoveryDispatchId,fromAttempt,toAttempt`显式null。resultDigest排除自身后RFC8785 SHA-256；scan key保存request/result exact JSON，同scan ID同digest逐字节返回，异digest冲突。

选中候选后，State构造`TaskRecoveryOperationIdentityV1`，字段恰为`schemaVersion,taskId,expiredFencingToken,fromAttempt`，schemaVersion固定字符串`"1"`，taskId为1..128字节NFC UTF-8且不得含NUL，两个整数分别限制在`0..2^53-1`并按RFC8785 JSON number编码。`recoveryOperationId=lowerhex(SHA-256(ASCII("a2amesh-task-recovery-operation-v1")||0x00||RFC8785_UTF8(identity)))`；固定identity `{"expiredFencingToken":7,"fromAttempt":1,"schemaVersion":"1","taskId":"task-01"}`必须得到`f077d339a655daa6cd8da29e5992a3238178b9c3c53f9c051ad73f705fa819fe`。`operationRequestV1`字段恰为`schemaVersion,identity,canonicalCommandDigest,configGeneration,policySnapshotHash,retryPolicyHash`，其requestDigest为该对象RFC8785 SHA-256，**不含recoveryScanId/scan requestDigest/scanner身份**；因此同一过期lease tuple经不同scan仍命中同一operation且逐字节返回原result，scan ledger只引用该operation结果。

Function只处理lease丢失后的非终态WORKING Task，原子校验targetAgentId、retryPolicy/maxAttempts/nextRetryMs、hard deadline、cancel、旧lease已过期、admission=RUNNING及既有slotToken、私有worktree和全部effect。UNKNOWN/APPLYING/不安全APPLIED不进入RECOVERY_RESUME：在旧lease已过期且无有效owner、effect事实已重验后，执行唯一原子`RECONCILIATION_REQUIRED`分支，创建或复用case并同时将Task写为`FAILED`且`reconciliationRequired=true`，清除owner/lease，按既有RUNNING slotToken恰好释放一次running admission，移除Task recovery due及当前recovery dispatch due，推进fence/tombstone，并写入唯一operation result、audit和outbox。该分支不得创建新dispatch、不得改变queued/reserved计数、不得自动重试effect；同一recoveryOperationId/requestDigest在提交后回复丢失时逐字节返回原结果，异digest零写入。

### 6.20 `expire_queue` / deadline 优先级

期限字段分别为 request、queue、dispatch、softExecution、hardExecution。request 过期在 claim 前拒绝且不创建 Task；queue 从 claim commit 起算，过期且未选中时原子 Task→CANCELED、dispatch→ABORTED并释放 queued slot；dispatch 从 SELECTED 起算，过期由 `expire_dispatch` 写 FAILED；soft execution 只写提示；hard execution 进入 `request_cancel`，其终态取决于进程终止/effect 汇总。所有实际 deadline 取 signed policy 与 caller 更短值的最小值，不能以通用 `deadlineMs` 在不同阶段重新起算。

### 6.21 `append_audit_event` / `claim_audit` / `ack_audit_receipt`

State mutation在同一原子函数调用内部helper `append_audit_event`，按sourceSeq分配eventId/digest并写DATA-AUDIT-001；非State来源必须先取得fsync WAL/Audit Ingress receipt。`claim_audit`与`ack_audit_receipt`只接受active signed `audit-relay:<instanceId>` NKey；Event Relay、State、Recovery、Runtime及外部`AUDIT_SINK`身份均不得调用。

`a2a.v1.state.audit.claim`的`AuditClaimRequestV1`字段恰为`schemaVersion,claimOperationId,relayInstanceId,expectedCursor,nowMs,limit,leaseDurationMs,requestDigest,authProof`；`claimOperationId`是每次逻辑scan由Relay生成并在transport retry间保持稳定的安全ULID，幂等scope为`relayPrincipalHash+claimOperationId`；`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`。State使用server time校验受控时钟偏差和signed lease上限，按`(nextAttemptMs,sourceId,sourceSeq,eventId)`从`audit:due`选择最多limit条，并保持同source head-of-line；一个CAS写claimOwner/随机claimToken/单调claim fencing/claimExpiresMs及`audit:operation:<claimOperationId>` exact result。`AuditClaimResultV1`字段恰为`schemaVersion,claimOperationId,nextCursor,claims,resultDigest`；每个claim恰含`eventId,sourceId,sourceSeq,eventDigest,canonicalEvent,claimToken,claimFencingToken,claimExpiresAt`。同ID同digest逐字节返回原result，异digest零写入；caller不得自选eventId。

`a2a.v1.state.audit.ack`的`AuditAckRequestV1`字段恰为`schemaVersion,ackOperationId,eventId,sourceId,sourceSeq,eventDigest,claimToken,claimFencingToken,sinkGlobalSeq,segmentId,segmentDigest,segmentJwsDigest,immutableUri,wormReceiptJson,wormReceiptDigest,requestDigest,authProof`。`ackOperationId=lowerhex(SHA-256(ASCII("a2amesh-audit-ack-v1")||0x00||RFC8785_UTF8({eventId,sourceId,sourceSeq,eventDigest,claimFencingToken,sinkGlobalSeq,segmentDigest,segmentJwsDigest,wormReceiptDigest})))`，`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`。固定identity `{"claimFencingToken":2,"eventDigest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","eventId":"source-a:1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","segmentDigest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","segmentJwsDigest":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","sinkGlobalSeq":3,"sourceId":"source-a","sourceSeq":1,"wormReceiptDigest":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}`必须得到`aa7448ec7152b56e7dadbeedee764f6130d56368a5b426591f4c80b0b4dc51d8`。State重验当前未过期claim、source head、event exact bytes/digest、全局sequence连续性、segment链/JWS signer policy，并通过受信ingress GET/read-back确认immutableUri exact envelope和WORM receipt；一个CAS写DELIVERED、移除due、推进source ack watermark与sink checkpoint、持久化operation exact result和audit。只有该CAS成功后Audit Relay才可丢弃本地WAL/claim；同ID同digest逐字节重放，异digest、旧claim/fence、错URI/digest、缺receipt均零写入。dead事件阻塞该source checkpoint，只有带运维capability的原event重投可恢复，不得生成新eventId掩盖缺口。

### 6.22 `seal_recovery_manifest` / `verify_recovery_manifest` / `claim_recovery_compaction` / `advance_recovery_compaction` / `mark_restored` / `release_recovery_point`

Manifest四个操作严格实现DATA-RECOVERY-001状态机和expected state/digest CAS，并以`recoveryPointId+receiptType+payloadDigest`幂等；compaction操作另按§5.20.1 exact scope幂等。`a2a.v1.state.recovery.verify`是closed `SCAN|VERIFY` union：`SCAN`只接受signed `recovery-verifier` NKey，从持久`recovery:verify:due`按`(verifyDueAtMs,recoveryPointId)`选择SEALED candidate并返回CSPRNG `verificationCandidateToken`；request恰含`schemaVersion,operation,scanOperationId,expectedCursor,nowMs,limit,requestDigest,authProof`，result恰含`schemaVersion,operation,scanOperationId,nextCursor,candidates,resultDigest`，同scan ID同digest逐字节重放、异digest零写入。`VERIFY`必须携带scanId/token、observed manifestDigest/state和稳定`verificationOperationId`，State在一个CAS重验candidate exact tuple、signed verifier NKey、producer-different signer和全部URI/read-back谓词后才写VerificationReceipt、移除verify due并保存exact result；Verifier随后只能以该receipt digest调用`recovery.restore`。`recovery-orchestrator`的seal/release由签名Recovery workflow intent或受控Ops API触发，`recovery-verifier`的发现只走上述SCAN，`recovery-compactor`只走§5.20.1 SCAN；不存在任意caller自选recoveryPointId或Object listing旁路。

- `seal_recovery_manifest`只接受signed `recovery-orchestrator` NKey与manifest signer kid，并要求完整连续 sources、RFC 8785 payload与满足两个不同kid阈值的exact JWS General JSON；外部WORM返回immutableUri/manifestJwsDigest/receipt后才写SEALED，并在同一CAS加入`recovery:verify:due`；
- `verify_recovery_manifest`只接受signed `recovery-verifier` NKey与verification signer kid，且其Principal必须与Manifest producer不同；实际读取全部URI/restore probe并先持久化`VerificationReceiptV1`；State在单一CAS前从SEALED manifest重算七个source exact顺序/URI/sourceDigest/verificationMethod，逐项要求observedDigest相等、result=VERIFIED、verificationDigest公式相等，并重算restoreProbeDigest和receipt result=VERIFIED。只有全部谓词、receipt URI/exact envelope digest/manifest digest/signer/time同时成立才写VERIFIED/latest-verified；空数组、缺失/重复/额外/错位、FAILED/UNKNOWN或任一digest错均零写入并进入REJECTED审计；
- `claim_recovery_compaction`实现§5.20.1 SCAN/ACQUIRE/RENEW/RELEASE：SCAN只从持久due按`(dueAtMs,transitionId)`返回有界candidate与CSPRNG token并保存exact scan result；ACQUIRE在同一CAS重验candidate/token/due tuple、source lease到期和transition state，再发放随机leaseId及单调source fencingToken；RENEW/RELEASE只接受exact owner/lease/fence。State提交后reply前崩溃由scan/leaseOperation幂等记录逐字节恢复；
- `advance_recovery_compaction`只实现ADVANCE。它在单一CAS重验signed compactor NKey、active source lease/fence、transition/range/pre-root/source watermark/expected state以及State ingress已GET/read-back验证的archive/receipt/Manifest/Verification exact URI+digest，随后只推进一条合法边；HOT_DELETED边还原子删除/标记hot range、写delete journal/audit/outbox、移除due并保存exact result。Redis Function不执行WORM/Object网络I/O；坏URI/digest、旧fence、越级state、同ID异body全部零写入；
- `mark_restored`只接受signed `recovery-verifier` NKey与verification/restore signer kid，且要求上述valid VerificationReceipt；State逐项要求Restore array与该集合exact相等、排序/字段/expected=observed/result=PASSED/verificationDigest全部成立，再从完整数组重算restoredVerificationDigest并要求receipt result=RESTORED。只有WORM RestoreReceipt exact URI/envelope digest/signer/time也有效时，一个CAS保存URI/digest/restoredVerificationDigest并写RESTORED；caller自报required set、空/少/多/重复component或失败结果均零写入；
- `release_recovery_point`只接受signed `recovery-orchestrator` NKey与release signer kid，且要求两个不同 operatorPrincipal 且不同 kid 的独立 Approval JWS、相同 manifest/restoredVerification digest与APPROVE decision；先写 ReleaseReceipt，再原子写两份 approval 与 release receipt 的全部 URI/digest、RELEASED、latest-released 和 AuditEnvelopeV1。相同 approvals 重试返回原结果，任一异 digest/同 Principal/同 kid 冲突。

除带当前compaction lease/fence的HOT_DELETED CAS外，任何步骤都不批量改写source事实，也不能用Redis内索引或timestamp代替外部签名正文；Redis全损重建必须逐份重验manifest、receipts、compaction transitions和approvals。

### 6.23 `open_stream_session` / `renew_stream_session` / `begin_stream_broker_operation` / `claim_stream_broker_operation` / `complete_stream_broker_operation` / `consume_stream_broker_operation` / `activate_stream_session` / `claim_stream_frame` / `ack_stream_session` / `confirm_stream_broker_ack` / `close_stream_session` / `expire_stream_session` / `finish_stream_session_cleanup` / `reclaim_stream_session`

`open_stream_session`原子校验AuthProof claim、caller对Task的归属/operation、streamOpenId幂等、deadline和active config，创建DATA-STREAM-SESSION-001 OPENING记录、确定性subjects、controller lease/fence，并持久写入configGeneration、consumerConfigJson/digest及signed brokerOperationLeaseMs/brokerApiApplyMaxMs。Controller必须把收到`a2a.v1.stream.open`时已验证的origin Application Core component Principal作为受信调用上下文传入，State从signed components映射并保存`responseCorePrincipalHash`；该值不来自业务payload，Gateway/Peer/Controller不得自报或替换。同digest重试返回原记录，异digest拒绝。`requestDigest=SHA-256(RFC8785({schemaVersion,streamOpenId,operation,taskId,callerScope,callerInstanceId,responseCorePrincipalHash,configGeneration,expiresAt,consumerConfigDigest}))`，排除requestDigest/AuthProof/transport requestId；时间按UTC三位毫秒Z canonical string，nullable显式null。consumerConfigJson字段恰为`schemaVersion,configGeneration,streamName,consumerName,filterSubject,controllerDeliverySubject,deliverPolicy,ackPolicy,replayPolicy,maxAckPending,ackWaitMs`；`deliverGroup`和`inactiveThresholdMs`都不得出现，broker语义固定为无queue group且不按inactive自动删除。任何Controller/Provisioner不得按当前active generation重新推导旧session。

`activate_stream_session`只接受当前controller fence以及Provisioner返回的exact consumerConfigDigest/INFO。对`SendStreamingMessage`，它必须读取claim CAS时写入的`streamClaimInitialFrameJson/Digest`，不得以当前Task快照替代；对`SubscribeToTask`才读取订阅时snapshot。它以expected Task version/eventSeq/snapshotDigest做同一Redis Function CAS，生成并持久化canonical `initialFrameJson/initialFrameDigest`与完整`openedResponseJson/openedResponseDigest`，再写ACTIVE；若Task在读取与CAS间变化则重读重试，consumer继续缓冲。`initialFrameJson`恰为sequence=0的StreamSessionFrameV1 canonical bytes，`openedResponseJson`恰为StreamSessionOpenedV1 canonical bytes并内嵌该initial frame。相同streamOpenId的任何重试（包括session已CLOSED/EXPIRED、Task终态后version/审计/引用继续变化）都返回已存openedResponseJson/currentState，不得重读当前Task重建历史响应。

`confirm_opened_response_flushed`是Application Core唯一的flush事实writer，request恰含`schemaVersion,flushOperationId,streamSessionId,openedResponseDigest,flushedAt,requestDigest,authProof`；core Principal只取认证NKey，不得出现在payload。`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`，幂等scope为`responseCorePrincipalHash+streamSessionId+flushOperationId`。State只接受与session已存`responseCorePrincipalHash`匹配的active signed Application Core NKey、OPENING/ACTIVE session及完全相等的openedResponseDigest；该事实不属于Controller ownership，因此请求**不含且不校验controllerFence**，Controller failover不能使已返回response的Core失去flush可达性。`StreamFlushResultV1`字段恰为`schemaVersion,flushOperationId,streamSessionId,openedResponseDigest,acceptedAt,resultDigest`并把exact bytes/digest存入session。同operation ID同digest逐字节返回原结果，异digest冲突；一个CAS写`openedResponseFlushedMs,flushObservationId,flushOperationId,flushResultJson,flushResultDigest`及audit/outbox。`close_stream_session`从ACTIVE/终态snapshot关闭前必须看到该字段，Controller不得凭本地socket flush猜测；flush前/后丢响应、错误Core Principal或错误digest均零写入。`a2a.v1.state.stream.flush`是唯一外部入口。

每次Create/Info/Delete前，State Service先以CSPRNG生成256-bit base64url challenge并调用`begin_stream_broker_operation(kind)`。Function校验当前controller fence、允许状态及`brokerOpInvalidatedThroughEpoch`，单调递增session `brokerOpEpoch`并新建不可变epoch HASH，持久写kind/challenge、由State按已存session字段构造的exact brokerOpRequestDigest、issued/expires、executionState=PENDING、consumedAt=null；session只保存current epoch指针，不用单槽覆盖旧epoch执行事实。返回的`BrokerOperationTicketV1`只使用字段名`brokerOpRequestDigest`。kind只允许`CREATE,CREATE_INFO,FINAL_ACK_INFO,DELETE,CLEANUP_INFO`；同fence/kind的current未过期未消费重试返回同一ticket，过期或更高fence签发新epoch并令旧ticket不可再claim/complete/consume，但旧epoch ledger保留至session清理。

Provisioner在调用任何裸JS API前必须以自身NKey调用`claim_stream_broker_operation(ticket,provisionerInstanceId)`：只有session current epoch、epoch大于invalidated-through且PENDING的ledger可原子变EXECUTING并按session内brokerOperationLeaseMs发放lease/attempt；EXECUTING未过期返回BUSY，同executor幂等重试也不得并发执行；lease过期可由更高attempt接管；COMPLETED只能返回该epoch已存responseJson，绝不再次执行broker API。对CREATE/DELETE每次成功claim的同一CAS还计算`attemptApplyUntilMs=executionLeaseUntilMs+brokerApiApplyMaxMs`，单调推进epoch `attemptMaxApplyUntilMs`和session `maxUnquiescedBrokerApplyUntilMs=max(old,attemptApplyUntilMs)`；后续BEGIN新epoch不得清零或回退。Provisioner只有在`now+requestTimeoutMs+brokerApiApplyMaxMs<=executionLeaseUntilMs`时才可发送JS API，否则放弃claim等待接管。调用成功后Provisioner对canonical result签名并调用`complete_stream_broker_operation`，Function校验current ticket/executor/attempt/lease，把exact responseJson/digest写入同epoch ledger并置COMPLETED；若broker API成功后Provisioner崩溃，接管者按同一确定性Create/Delete/Info请求重执行，操作本身必须幂等。Controller收到response后调用`consume_stream_broker_operation`；Function只接受与该epoch已存response逐字节/digest相同、当前fence/epoch/kind/challenge/requestDigest、未过期且consumedAt为空的结果，再单次写consumedAt。CREATE成功后写createConfirmedEpoch，只有它存在才可BEGIN CREATE_INFO；DELETE成功后仅当本operation epoch严格大于cleanupEpochLowerBound才写deleteConfirmedEpoch，也只有此值存在才可BEGIN CLEANUP_INFO。CREATE_INFO结果由activate原子消费，FINAL_ACK_INFO由confirm原子消费，CLEANUP_INFO由finish cleanup原子消费；exists=true的cleanup观察也消费本epoch并要求下次查询签发新epoch。

Consumer 固定 `max_ack_pending=1`。`claim_stream_frame` 只接受当前 controller fence、固定 consumer/task 与 exact JetStream streamSeq/eventSeq/event digest，并按互斥分支处理：

1. `eventSeq<=snapshotEventSeq`：不产生 caller frame；要求 streamSeq/eventSeq 大于 covered watermark，或与最后 covered tuple 完全相同。函数持久推进 `snapshotCoveredAckedStreamSeq/EventSeq/PayloadDigest` 并返回 broker-only ACK permit；State 写后/实际 JS ACK 前崩溃时，重投相同 tuple 返回同一 permit。异 task/digest、任一序列倒退或越过 snapshot 拒绝。Controller 必须逐条 ACK covered event，直到收到首个 eventSeq>snapshot，不能仅在内存丢弃。
2. 已有未 ACK pending tuple：只对相同 eventSeq/digest 返回原 sequence/frame digest。
3. `eventSeq<=lastAckedEventSeq`：只在与 lastAcked tuple 完全相等时返回 ACK_ONLY，异 digest 冲突并审计。
4. 新 caller frame：要求 streamSeq/eventSeq 同时大于对应 delivered/acked watermark且 eventSeq>snapshot，分配 `lastAckedSequence+1`并持久化 delivered/pending streamSeq+eventSeq+digest tuple后才允许 publish；若 canonical event 为 Task 终态，同一原子提交还写 `state=DRAINING_FINAL,finalStreamSeq,finalEventSeq,finalSequence`。State 写后/publish 前崩溃只能重发同一 frame。

`ack_stream_session` 只接受记录中的 caller Principal/scope、`ACTIVE|DRAINING_FINAL` 状态和与 pending tuple 完全相同的 `sequence,eventSeq,payloadDigest`，原子把 pendingStreamSeq一并推进到 lastAckedStreamSeq、推进其余 lastAcked tuple、清 pending并返回可幂等重取的 ACK permit；真实 `$JS.ACK.*` 只由 Controller 发布。若 State 已 ack 但实际 JS ACK 丢失，redelivery 经 exact ACK_ONLY 再发 broker ACK，不重新转发 caller。

非final frame在后续JS delivery到达时即可证明前一ACK已被broker接受。final frame没有后续消息，因此Controller发ACK后必须先BEGIN`FINAL_ACK_INFO`，再经JS Provisioner`consumer.info`回读`num_ack_pending=0`且ack floor/streamSeq至少达到该final消息，再调用`confirm_stream_broker_ack`；函数单次消费current epoch并校验signed INFO、consumerName、consumerConfigDigest/final tuple和当前fence，写`lastBrokerAckedEventSeq/StreamSeq,finalBrokerAckConfirmed=true`。只有此后`close_stream_session(reason=FINAL_ACKED)`才能DRAINING_FINAL→CLOSING。若持久initialFrame本身终态且无pending live tuple，Core发布/flush已存openedResponseJson后可调用`close_stream_session(reason=TERMINAL_SNAPSHOT_DELIVERED)`从ACTIVE→CLOSING；caller主动close也可ACTIVE→CLOSING并明确放弃未ACK frame。任何进入CLOSING的close transition与claim使用同一Redis CAS：若claim先提交，transition必读取其已推进的apply上界；若transition先提交，则原子令`brokerOpInvalidatedThroughEpoch=brokerOpEpoch`，后续旧ticket claim失败。transition令cleanupEpochLowerBound等于当时brokerOpEpoch、`brokerOpQuiesceUntilMs=max(now,maxUnquiescedBrokerApplyUntilMs)`并清deleteConfirmedEpoch；不得从current单槽execution lease重算或回退该值。

`renew_stream_session`是健康Controller维持owner的唯一续租writer。request是closed `RENEW` variant，字段恰为`schemaVersion,operation,renewOperationId,streamSessionId,ownerInstanceId,observedControllerFence,observedControllerLeaseUntilMs,leaseDurationMs,requestDigest,authProof`；`renewOperationId=lowerhex(SHA-256(ASCII("a2amesh-stream-renew-v1")||0x00||RFC8785_UTF8({streamSessionId,ownerInstanceId,observedControllerFence,observedControllerLeaseUntilMs,leaseDurationMs})))`，`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`。State只接受当前Stream Session Controller NKey、OPENING/ACTIVE/DRAINING_FINAL状态、ownerInstanceId/fence/leaseUntil与observed tuple完全相等且server time仍未过期的请求；`leaseDurationMs`必须落在signed policy范围，新的`controllerLeaseUntilMs=min(serverNowMs+leaseDurationMs,expiresAtMs)`且严格晚于当前值。一个CAS写新lease、按`min(controllerLeaseUntilMs,expiresAtMs)`重排`stream-session:due`及`renew-operation:<renewOperationId>` exact result，不改变controller fence、broker epoch或consumer状态；同ID同digest在提交后回复丢失时逐字节返回原结果，异digest、旧owner/fence、已到期lease、CLOSING/EXPIRING/终态均零写入。RENEW与SCAN/RECLAIM竞争时只有先提交的CAS生效，接管不得因旧renew回复再回退lease。

`a2a.v1.state.stream.reclaim` request是closed `SCAN|RENEW|RECLAIM` union。SCAN的`StreamSessionRecoveryScanRequestV1`字段恰为`schemaVersion,operation,scanOperationId,expectedCursor,nowMs,limit,requestDigest,authProof`；只接受Stream Session Controller NKey，limit为1..100，State要求nowMs在受控时钟偏差内但以State时间选取`stream-session:due`中score=`min(controllerLeaseUntilMs,expiresAtMs)`且score<=now的候选。State不会因存在短暂due评分而跳过一个未过期的controller lease；`StreamSessionRecoveryScanResultV1`字段恰为`schemaVersion,operation,scanOperationId,nextCursor,candidates,resultDigest`；candidate恰含`streamSessionId,observedControllerFence,observedControllerLeaseUntilMs,observedExpiresAt,recoveryCandidateToken`，按`(dueScore,streamSessionId)`排序，token为State CSPRNG 32字节base64url无padding并仅存scan ledger。request/result digest均排除自身/authProof后RFC8785 SHA-256，同scan ID同digest逐字节返回，异digest冲突；caller不能提交任意session ID。

RECLAIM的`StreamSessionReclaimRequestV1`字段恰为`schemaVersion,operation,reclaimOperationId,scanOperationId,streamSessionId,recoveryCandidateToken,observedControllerFence,observedControllerLeaseUntilMs,requestDigest,authProof`。`reclaimOperationId=lowerhex(SHA-256(ASCII("a2amesh-stream-reclaim-v1")||0x00||RFC8785_UTF8({scanOperationId,streamSessionId,observedControllerFence,observedControllerLeaseUntilMs})))`。State在一个CAS重验token/scan exact tuple、due成员、当前fence/lease仍等于观察值且lease已过期；随后递增fence、接管controller lease、重排due、令旧current broker epoch无效并把exact result写reclaim operation ledger。同ID同digest逐字节返回，异digest或旧candidate零写入。所有Controller进程和内存丢失后，新实例只通过该SCAN→RECLAIM路径发现/接管；consumerName或delivery subject不得作为反向索引。

`expire_stream_session`把OPENING/ACTIVE/DRAINING_FINAL原子写EXPIRING、closeReason和审计，不再接受caller ACK或新frame；它使用与close相同的claim竞态CAS，令brokerOpInvalidatedThroughEpoch/cleanupEpochLowerBound等于当时brokerOpEpoch、`brokerOpQuiesceUntilMs=max(now,maxUnquiescedBrokerApplyUntilMs)`并清deleteConfirmedEpoch。未确认final的caller必须以后用GetTask+Subscribe重连。CLOSING/EXPIRING只有在State时间达到brokerOpQuiesceUntilMs后才可BEGIN DELETE，随后单次consume成功结果→BEGIN CLEANUP_INFO→Info回读；该等待覆盖**全部已claim side-effect epoch/attempt**的最晚apply上界，保证旧CREATE最迟生效后仍被本轮DELETE覆盖。`finish_stream_session_cleanup`只在current未消费CLEANUP_INFO epoch的signed INFO=not-found且consumerConfigDigest匹配时写consumerDeletedAt，并分别CLOSING→CLOSED、EXPIRING→EXPIRED。exists=true只消费观察并保持原状态，随后必须新BEGIN，不得复用response。完成上述RECLAIM CAS后，接管者按状态恢复：所有状态都读取已存configGeneration/consumerConfigJson/digest和initial/opened response；OPENING按CREATE→consume→CREATE_INFO顺序查询/创建；ACTIVE/DRAINING_FINAL只在current challenge INFO exists=true且配置匹配时从watermark恢复，exists=false必须以`CONSUMER_LOST`调用expire进入EXPIRING，禁止按deliverPolicy=new重建；CLOSING/EXPIRING等待原quiesceUntil后继续全新的DELETE/CLEANUP_INFO序列。稳定controllerDeliverySubject无需更新。旧fence的broker ticket、activate/frame/deliver/ack/confirm/close/expire/cleanup全拒绝。

---

### 6.24 `begin_stream_config_reconcile` / `claim_stream_config_operation` / `complete_stream_config_operation`

`begin_stream_config_reconcile`只接受Config Controller经`a2a.v1.state.stream-config.begin`提交NATS §9.5 exact `StreamConfigBeginRequestV1`；同一Redis Function校验STAGED bundle内desiredConfig exact bytes/digest、有效rollout lease/fence和关闭的maintenance gate，再创建或逐字节重放operation ledger。它先签发INFO epoch；根据completed INFO在同一CAS选择CONFIRMED、PENDING_CREATE、PENDING_UPDATE或FAILED_CLOSED，CREATE/UPDATE完成后必须签发新的VERIFY INFO epoch。

`claim_stream_config_operation`只接受signed components内当前JS Provisioner NKey与current unexpired `StreamConfigOperationTicketV1`，在epoch HASH上以递增executionFence原子返回`EXECUTE|BUSY|REPLAY_STORED`。`complete_stream_config_operation`校验Provisioner signer、executor/fence/lease、kind/challenge/brokerRequestDigest、response exact bytes/digest后单次写COMPLETED；旧epoch/旧rollout fence即使broker调用成功也不能推进State。Config Controller以相同streamOperationId重取进度和exact result，不另设易失回调。

fresh INFO确认时，State从`observedConfigJson`重算RFC8785 digest并逐字段与signed bundle比较；只有全部相等才同时写DATA-STREAM-CONFIG-001 confirmed记录、operation CONFIRMED、AuditEnvelope/outbox。`activate_config`对启用NATS Binding的generation必须在同一CAS读取`confirmedGeneration=generation`及`desiredConfigDigest=bundle digest`，否则零写入拒绝。任一函数不执行外部JS API；kill point、claim接管、响应丢失与异body重放按持久epoch/result闭合。

---

## 7. ListTasks 与查询

### 7.1 过滤

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

外部 A2A/MCP `GetTask/ListTasks/CancelTask` 只允许 Task 的 `callerPrincipal`；不存在与无权访问统一 no-leak。target executor 和 `system:*` 仅通过独立内部 State 操作访问其正在执行或固定职责范围内的 Task，不能调用外部列表扩大可见面。这是资源归属检查，不建设 RBAC。

### 7.2 排序与游标

固定按 `(updatedMs DESC, taskId DESC)`。下一页使用最后一项的二元组；filtersHash 不一致时游标无效。pageSize 默认 50，最大 200。

### 7.3 一致性

列表直接读取 Redis 已提交权威快照，不依赖 Projector 才可见。JetStream/Projector 延迟只影响 SSE、Push、Observer 和派生统计，不得造成 Get/List 比已确认命令更旧；终态和 Artifact 元数据通过同步 State mutation 提交。

`a2a.v1.state.task.get`是closed tagged union：`operation=GET`恰含`taskId,callerPrincipal,historyLength,includeArtifacts,authProof`；`operation=LIST`恰含`filtersHash,contextId,state,targetAgentId,updatedAfter,updatedBefore,pageSize,pageToken,callerPrincipalInjected,authProof`，其中callerPrincipal只能由Application Core的verified credential observation注入，不能由body覆盖。State按`(updatedMs DESC,taskId DESC)`从权威索引分页，签名pageToken绑定完整filtersHash；同请求重试返回同cursor/result，非法或过期token零写入。Application Core/Gateway共用该subject，其他角色不得借LIST扩大可见范围。

`a2a.v1.state.push.config`是closed tagged union，request恰含`schemaVersion,operation,taskId,configId,expectedVersion,idempotencyKey,requestDigest,config,url,authProof`，按operation只允许：`CREATE`需`config`（url/authType/encryptedCredential），State验证Task owner、Push capability、HTTPS/SSRF/DNS/redirect和credential envelope后分配configId；`GET|LIST`只返回脱敏配置；`DELETE`幂等置REVOKED并阻止新delivery。`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`，scope为`taskId+callerPrincipalHash+idempotencyKey`；同key同digest逐字节返回result，异digest冲突。四个variant都在同一CAS维护`task:<taskId>:pushcfg`、config HASH、version/audit/outbox；delivery worker只使用已提交配置，不拥有CRUD权限。`TEST-LIST-001/TEST-SEC-001`必须覆盖每个variant的wrong owner、SSRF、credential明文、同key丢响应及delete-vs-delivery竞态。

---

## 8. Presence

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

## 9. 保留与清理

| 数据 | 默认热保留 | 后续 |
|---|---:|---|
| Task/Context 热快照 | 7 天 | 可归档；最小 owner/status/artifact 授权墓碑至少保留到最长关联 Artifact 过期 |
| dedupe | 与 Task 相同 | 到期删除 |
| Card | 直到 unregister | tombstone 30 天 |
| instance presence Key | 90 秒 | Key TTL 自动过期，SET 索引由清理器收敛 |
| Push config | Task 终态后 24 小时 | 删除凭据 |
| Push delivery/DLQ | 7 天 | 脱敏归档 |
| rate key | 1～10 分钟 | 自动过期 |
| delivered outbox | 0～1 小时 | PubAck 后删除或短留用于诊断 |
| dead outbox | 7 天 | 恢复/审计后删除 |
| side-effect ledger | 至少与 Task 审计同周期 | 脱敏冷归档 |
| capability grant | 配置有效期 | generation 替换后保留 24 小时回滚窗口 |
| admission task/counter | 排队/运行期间 | 终态后短 TTL 并对账清理 |
| Artifact upload session | 默认 24 小时 | 对象存储 Reaper 对账清理 |
| Artifact stable metadata（含 terminal） | 默认 30 天 | 与 Object Store policy 一致归档/删除 |
| trusted config bundle/audit | 至少 365 天 | 加密不可变归档 |
| reconciliation case/evidence/audit | 至少 365 天 | 不因 Task TTL 提前删除 |

清理使用扫描器和小批量 Lua，不在 Redis 主线程执行大范围阻塞操作。

---

## 10. Redis 配置

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

## 11. 故障与恢复

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

Redis 不可用时“继续接受新任务、稍后补写”会破坏幂等，必须 fail closed。恢复必须选择并验证 `DATA-RECOVERY-001` manifest；未完成 config、Redis、JetStream、Object inventory、audit 和 effect 风险对账前，不开放新副作用。

恢复目标：服务进程重启 RTO 15 分钟，完整单节点恢复 RTO 4 小时；受控进程/服务重启且持久卷完好时目标 State RPO 为 0，整机、磁盘或电源故障时 State/Event RPO 不超过 15 分钟。`appendfsync everysec` 不得被解释为突发掉电 RPO 0；Redis AOF/RDB、JetStream 持久目录和配置/Secret 元数据必须至少每 15 分钟形成异机加密恢复点，并定期演练跨组件一致性对账。

---

## 12. 数据迁移

Key schema 变更：

1. 新代码先支持读旧/写新；
2. 后台迁移带幂等 checkpoint；
3. 校验数量/hash/索引；
4. 切换读新；
5. 观察一个保留窗口；
6. 删除旧 Key。

禁止原地改变已发布字段语义。大版本使用 `a2am:v2:` 前缀。

---

## 13. 监控

至少暴露：

```text
redis_state_rpc_latency_seconds
redis_state_rpc_errors_total
redis_task_count{state}
redis_task_projection_lag_seconds
redis_outbox_due_count / redis_outbox_oldest_age_seconds
redis_outbox_publish_failures_total
redis_outbox_order_blocked_total / redis_outbox_claim_conflict_total
redis_dispatch_due_count / redis_dispatch_oldest_age_seconds / redis_dispatch_dead_total
redis_effect_count{state,risk}
redis_effect_stale_applying_total
redis_reconciliation_required_count
redis_capability_denied_total{reason}
redis_admission_queued / redis_admission_rejected_total{scope,reason}
redis_artifact_count{status} / redis_artifact_upload_due_count
redis_config_active_generation / redis_config_generation_mismatch_total
redis_config_component_ready{component_type}
redis_reconciliation_case_count{workflow_state,escalated,priority}
redis_reconciliation_claim_expired_total
redis_lease_renew_failures_total
redis_auth_replay_rejected_total
redis_workspace_lease_conflict_total
redis_recovery_manifest_age_seconds
redis_dedupe_hits_total{result}
redis_card_count
redis_presence_age_seconds{agent}
redis_push_due_count / redis_push_dlq_count
redis_used_memory_bytes / redis_evicted_keys / redis_aof_rewrite
```

`evicted_keys > 0` 为 P1 配置错误。

---

## 14. 验收用例

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
- **TEST-ARTIFACT-ATOMIC-001 / DATA-ARTIFACT-001**：完成上传只产生一个 AVAILABLE Artifact、Task 关联和 outbox，signed URL/字节不进入 Redis。
- **TEST-CONFIG-ATOMIC-001 / DATA-CONFIG-001**：bundle、READY、active pointer、运行时索引和 publisher fencing 无跨 generation 混用。
- **TEST-RECON-RESOLVE-001 / DATA-RECON-001**：case/effect/Task/audit/outbox 原子一致，旧 claim/revision 无法晚写。
- **TEST-DISPATCH-001 / DATA-DISPATCH-001**：claim后任意故障点不丢执行意图，Task Supervisor以command digest/provisional lease完成State ACCEPTED后才停止重投。
- **TEST-OUTBOX-ORDER-001 / DATA-OUTBOX-001**：多 Relay 下同 Task 严格 head-of-line，旧 claim token 无法完成或重排。
- **TEST-AUTH-REPLAY-001 / DATA-AUTH-REPLAY-001**：跨 State 实例重放同 requestId 被拒绝，过期 Key 可安全清理。
- **TEST-PLAN-RECOVERY-001 / DATA-PLAN-001**：编排进程重启后通过 acquire/renew/recover plan lease 与更高 fencing，从 Plan/Step/root-child/child Task 恢复且不重复创建 child；旧 owner 拒绝。
- **TEST-WORKSPACE-LEASE-001 / DATA-WORKSPACE-LEASE-001**：State lease 同时只有一个有效 fencing owner；基础 lease 测试不宣称能直接 fence 文件系统。
- **TEST-WORKSPACE-FENCE-001 / DATA-WORKSPACE-LEASE-001**：旧进程可写自己的私有 attempt worktree，但 Merge Broker 对旧 token 或 `baseRevision/expectedDiffDigest/activeGeneration/policySnapshotHash` 任一不匹配均拒绝，共享根只产生一次受审 commit。
- **TEST-AUDIT-SINK-001 / DATA-AUDIT-001**：State 与五类 non-State source 在所有 crash 点无 source/global sequence 缺口；AuditEnvelope/AuditSegment exact JWS fixture、普通/轮换签名阈值、WORM receipt、跨日链和 pseudonym 投影全部可验证。
- **TEST-DR-MANIFEST-001 / DATA-RECOVERY-001**：exact Manifest/Verification/Restore/Approval/Release JWS fixtures、实际backup URI、delete journal/audit水位、restore probe和双人release通过；七个source逐项URI/digest/method/VERIFIED及非空required component exact set/PASSED/RESTORED/restoredVerificationDigest都由State重算。空/缺/多/重复/乱序集合、FAILED/UNKNOWN、expected/observed漂移、坏聚合digest、篡改/单签/同人批准全部fail closed且零状态推进；Redis全损只凭外部证据重建同一状态。
- **TEST-DR-MANIFEST-DAG-001 / DATA-RECOVERY-001**：固定七source、ROOT/INNER/LEAF summary node和content-addressed URI fixture；递归校验nodeDigest、child/entry排序、连续range、递归count、sourceRoot绑定、archiveTransitionDigest和`indexRootDigest`。缺node、object listing替代GET、循环、重叠/缺口range、错误parent/leaf count、sourceType漂移、root-only/digest-only验证、summary↔receipt后向引用全部拒绝，Manifest不得推进VERIFIED。
- **TEST-DR-COMPACTION-001 / DATA-RECOVERY-001**：由两个独立Recovery Compactor只经`a2a.v1.state.recovery.compact`运行，覆盖VERIFIED manifest生成persistent due intent、SCAN exact replay、candidate/token ACQUIRE、RENEW/RELEASE、source lease过期接管、每个ADVANCE、archive写入/read-back、ArchiveTransitionReceipt、new summary/Manifest、独立Verification、State hot-index CAS删除及commit-before-reply各崩溃点。同scope/body逐字节重放且不重复升fence/archive/receipt/delete journal，异digest、伪造candidate、旧lease/fence、越级state、错误pre-root/重叠watermark零写入；普通Recovery Principal无compact权限，Compactor无其他Recovery权限。receipt/archive/summary未全部WORM可读前不得删除State hot index，且任何路径不得由该CAS删除外部source权威对象；Redis全损后仅凭Manifest、summary DAG、archive exact bytes和receipts恢复source index，缺任一保持FAILED_CLOSED。
---

## 15. G0 State 冻结合同

1. `claim_message` 单原子提交 Task、dedupe、admission、event outbox 和 durable dispatch intent；Task ID 由 State 生成。
2. AuthProof replay、ExecutionPlan/Step、workspace lease、dispatch、ordered outbox、effect attempt 和 Recovery Manifest 均有明确 Key 与原子函数。
3. cancelRequested 是 Redis 权威事实，control Subject 仅加速；acquire/heartbeat/接管必须读取该事实。
4. Event Relay 多实例使用 claim lease，且同 Task 仅 head event 可发布；PubAck 后才能完成。
5. effectIntentId 与 effectAttemptId 分离，陈旧 APPLYING 由 Reconciler 原子转 UNKNOWN 并创建唯一 case。
6. 脚本先完成全部校验再写，不依赖 Redis Lua 错误后的回滚；单 Mesh/单 hash-slot 是 V1 明示容量边界。
7. Task 热快照清理后保留与 Artifact/审计同周期的最小 owner/status 墓碑。

---

## 16. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [AgentCard与协议对象规范 V1.6](A2AMesh_AgentCard与协议对象规范_V1.6.md)
- [A2A协议与NATS集成适配设计 V1.6](A2AMesh_A2A协议与NATS集成适配设计_V1.6.md)
- [任务生命周期与长任务运行时设计 V1.6](A2AMesh_任务生命周期与长任务运行时设计_V1.6.md)
- [编排器 Runtime与工具适配设计 V1.6](A2AMesh_编排器_Runtime与工具适配设计_V1.6.md)
- [接口请求与响应标准 V1.6](A2AMesh_接口请求与响应标准_V1.6.md)
- [统计审计与运行监控规则 V1.6](A2AMesh_统计审计与运行监控规则_V1.6.md)
- [Artifact与对象存储设计 V1.2](A2AMesh_Artifact与对象存储设计_V1.2.md)
- [受信配置与变更治理设计 V1.2](A2AMesh_受信配置与变更治理设计_V1.2.md)
- [人工对账与运维操作设计 V1.2](A2AMesh_人工对账与运维操作设计_V1.2.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
- [A2A v1.0.1 canonical Proto](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)
- [A2A Agent Discovery](https://github.com/a2aproject/A2A/blob/v1.0.1/docs/topics/agent-discovery.md)
- [A2A Custom Protocol Bindings](https://github.com/a2aproject/A2A/blob/v1.0.1/docs/topics/custom-protocol-bindings.md)
