# A2AMesh Redis 状态平面与数据设计 V1.1
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
> 替代版本：V1.0
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh V1 的 Redis 状态平面、State Service、Key Schema、索引、原子函数、租约、幂等、游标、Push 配置、保留、备份和故障恢复规则。

Redis 只保存可查询的热状态和控制数据。JetStream 是实时事件日志；大 Artifact 使用对象存储；Peer 不直接访问 Redis。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立单 Mesh Redis Key、Task/Card/幂等/租约/Push 数据契约和 Lua 原子规则 |
| V1.1 | 2026-08-14 | 落地DATA标识、Agent发现查询模型和对应验收ID |

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

---

# 3. 拓扑

```text
Gateway / Application Core ─┐
Peer / Projector / Push ─────┼─ NATS State RPC ─▶ State Service ─▶ Redis
Observer / Admin Probe ──────┘                         │
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
| Message 幂等 | 权威 | 不负责 | — |
| Task lease/fencing | 权威 | 不负责 | — |
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
eventSeq                latest projected event sequence
lastHeartbeatMs         TaskSupervisor heartbeat
phase                   A2AMesh progress phase
phaseSummary            redacted human summary
progressJson            compact structured progress
attempt                 execution attempt
leaseUntilMs            cached display field; lease key is authority
fencingToken            latest token
cancelRequested         internal boolean
terminalReason          redacted reason
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

---

# 6. 原子函数

## 6.1 `claim_message`

输入：

```text
callerPrincipal,targetAgentId,messageId,payloadHash,
newTaskId,newContextId,nowMs,retentionMs,taskJson
```

行为：

1. dedupe 不存在：创建 dedupe、Task、状态/时间/Context/Caller/Agent 索引，返回 `CREATED`。
2. dedupe 存在且 hash 相同：返回原 taskId 与当前 Task，`DUPLICATE_SAME`。
3. hash 不同：返回 `DUPLICATE_CONFLICT`，不修改任何状态。
4. newTaskId 必须由 State Service 生成，调用方不可指定。

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

同一函数更新 Task HASH、state ZSET、updated ZSET 和 Context/Agent 索引。

## 6.3 `acquire_lease`

- Task 必须为 submitted/working/recoverable；
- lease 不存在或已过期；
- `INCR fence` 获取新 token；
- `SET lease PX ttl NX`；
- 更新 owner/attempt/version；
- 返回 token 和新 version。

## 6.4 `renew_lease`

仅当 ownerAgentId、ownerInstanceId、fencingToken 全匹配时 `PEXPIRE`。失败后旧 Supervisor 必须立即停止副作用。

## 6.5 `request_cancel`

- 终态：返回当前 Task（幂等）或标准 not-cancelable 语义；
- 非终态：设置 `cancelRequested=1`、version+1；
- 返回 owner instance，Core 再发 control message；
- 真正 canceled 由 Supervisor 确认进程退出后 transition。

## 6.6 `upsert_card`

- generation 小于等于当前时拒绝/幂等；
- 删除旧 skill/binding 索引；
- 写 Card、meta 和新索引；
- Card JSON 已由官方 SDK 验证；
- heartbeat 不调用本函数。

## 6.7 `project_event`

- 以 taskId/eventSeq 去重；
- 只接受更大 eventSeq；
- 若 taskVersion 过旧则丢弃并计数；
- 更新 Task 快照/phase/progress/heartbeat；
- 阶段变化和终态立即写，高频输出允许合并。

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

列表是 Redis 最新投影，不保证包含尚未被 Projector 消费的瞬时事件，但 Task 快照必须在配置的投影延迟 SLO 内更新。终态写采用同步 State transition，不依赖延迟 Projector。

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
| Redis 短暂不可用 | 停止新提交/Card 更新/lease；已执行任务可继续写 JetStream，恢复后投影 |
| State Service 重启 | NATS queue group 接管；Lua 保证幂等 |
| Projector 重启 | durable consumer 从 ack 位点重放；eventSeq 去重 |
| AOF 丢失尾部 | 从备份/AOF 恢复，再用 JetStream 有限窗口补投影；无法恢复任务需人工判定 |
| maxmemory | noeviction 导致写失败并 P1 告警，不能静默丢 Task |
| 双 Supervisor | lease/fencing 只允许新 owner 写；旧 owner 停止副作用 |
| 清理器误删 | terminal/dedupe TTL 一致；存在 active lease 时禁止清理 |

Redis 不可用时“继续接受新任务、稍后补写”会破坏幂等，必须 fail closed。

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
- **TEST-LIST-001 / DATA-CURSOR-001**：ListTasks/Agents 在相同排序值下无重复或遗漏，filtersHash 不匹配时拒绝。
- **TEST-STREAM-001 / DATA-TASK-001**：Projector 重放同 eventSeq 不重复改变 Task version。
- **TEST-RECOVERY-001**：Redis 重启后 Task/Card/dedupe/lease 语义符合预期。
- **TEST-SEC-001**：Redis 从公网和 Windows 不可达；凭据不以明文保存。
- **TEST-CAPACITY-001**：内存满时写失败并告警，不发生静默逐出。
---

# 15. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.1](A2AMesh_业务与总体架构设计_V1.1.md)
- [AgentCard与协议对象规范 V1.1](A2AMesh_AgentCard与协议对象规范_V1.1.md)
- [A2A协议与NATS集成适配设计 V1.1](A2AMesh_A2A协议与NATS集成适配设计_V1.1.md)
- [任务生命周期与长任务运行时设计 V1.1](A2AMesh_任务生命周期与长任务运行时设计_V1.1.md)
- [编排器 Runtime与工具适配设计 V1.1](A2AMesh_编排器_Runtime与工具适配设计_V1.1.md)
- [接口请求与响应标准 V1.1](A2AMesh_接口请求与响应标准_V1.1.md)
- [统计审计与运行监控规则 V1.1](A2AMesh_统计审计与运行监控规则_V1.1.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
- [A2A v1.0.1 canonical Proto](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)
- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A Custom Protocol Bindings](https://a2a-protocol.org/latest/topics/custom-protocol-bindings/)
