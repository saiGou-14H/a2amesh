# A2AMesh 统计、审计与运行监控规则 V1.0

---

# 1. 文档目的

本文档定义 A2AMesh V1 的运行指标、任务统计、Agent/Runtime 健康、审计事件、日志与隐私、Trace、告警、数据保留和运行看板。平台只记录网格和任务可观测事实，不复制 Runtime 供应商内部遥测，也不保存模型原始思维链。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 Agent、Task、NATS、Redis、Runtime、SSE、Push、Observer 指标和告警规则 |

## 1.2 当前状态

当前项目有基础日志配置和测试，尚无完整 Prometheus/OpenTelemetry、审计存储、看板与告警。本文件为目标设计。

---

# 2. 数据来源

| 数据域 | 权威来源 |
|---|---|
| Agent Card/索引 | Redis State Service |
| Agent presence/runtime health | Redis presence + NATS connection events |
| Task 最新状态/phase/lease | Redis Task |
| Task 有序事件/consumer lag | JetStream |
| NATS 连接/路由/存储 | NATS server metrics |
| Runtime 进程/执行 | TaskSupervisor/ProcessExecutor |
| SSE 连接 | Gateway |
| Push delivery | Push Dispatcher/Redis |
| Observer 分析/干预 | Observer consumer/policy |
| 外部 A2A 请求 | Gateway access/trace |
| Redis 健康 | Redis INFO/latency/AOF |

统计默认使用已提交状态；瞬时消息不能代替 Task 快照事实。

---

# 3. 指标命名与标签

指标前缀：`a2amesh_`。Prometheus label 仅使用低基数维度：

```text
mesh_id
agent_id（Agent 数量受控）
runtime
operation
binding
state
phase
result
error_class
```

禁止把 `task_id`、`message_id`、`request_id`、文件路径、用户文本作为 metric label；这些只进日志/Trace。

---

# 4. Agent 与 Card 指标

| 指标 | 类型 | 口径 |
|---|---|---|
| `a2amesh_agent_registered` | Gauge | 当前已注册 Card 数 |
| `a2amesh_agent_online` | Gauge | presence 未超过 offline 阈值 |
| `a2amesh_agent_presence_age_seconds` | Gauge | 当前时间-lastSeen |
| `a2amesh_agent_card_update_total` | Counter | Card generation 更新 |
| `a2amesh_agent_card_validation_failure_total` | Counter | 官方 SDK/Card 校验失败 |
| `a2amesh_runtime_available` | Gauge | Agent/runtime probe 状态 |
| `a2amesh_agent_running_tasks` | Gauge | 每 Agent 活动 Task |
| `a2amesh_agent_capacity` | Gauge | 配置执行容量 |

Card heartbeat 不计作 Card update。

---

# 5. Task 指标

| 指标 | 口径 |
|---|---|
| `a2amesh_task_submitted_total` | 受理新 Task，不含 dedupe hit |
| `a2amesh_task_terminal_total{state}` | completed/failed/canceled/rejected |
| `a2amesh_task_active{state,phase}` | 当前热 Task |
| `a2amesh_task_duration_seconds` | created→terminal Histogram |
| `a2amesh_task_queue_seconds` | submitted→working |
| `a2amesh_task_phase_seconds{phase}` | phase 停留时间 |
| `a2amesh_task_heartbeat_age_seconds` | 最近 Task heartbeat age |
| `a2amesh_task_stalled_total` | 超 phase/heartbeat 阈值 |
| `a2amesh_task_lease_acquire_total{result}` | lease 竞争结果 |
| `a2amesh_task_lease_renew_failure_total` | 续租失败 |
| `a2amesh_task_recovery_total{result}` | 接管/人工/失败 |
| `a2amesh_task_retry_total{reason}` | attempt 增加 |
| `a2amesh_task_cancel_total{result}` | 请求/成功/kill失败 |
| `a2amesh_task_dedupe_total{result}` | created/same/conflict |

Task duration 按 runtime/target/result 聚合，不把 taskId 当 label。

---

# 6. NATS 与 JetStream 指标

```text
a2amesh_nats_connected
a2amesh_nats_reconnect_total
a2amesh_nats_rpc_duration_seconds{operation,result}
a2amesh_nats_rpc_timeout_total{operation}
a2amesh_nats_pending_bytes
a2amesh_jetstream_events_published_total{kind}
a2amesh_jetstream_consumer_lag{consumer}
a2amesh_jetstream_redelivery_total{consumer}
a2amesh_jetstream_storage_bytes
a2amesh_stream_sequence_gap_total
```

NATS 服务端还监控连接数、慢消费者、内存、磁盘、JetStream storage 和 route health。普通 `$SRV.PING` 结果不能作为 Agent 总数权威指标。

---

# 7. Redis/State 指标

```text
a2amesh_state_rpc_duration_seconds{operation,result}
a2amesh_state_rpc_error_total{operation,error_class}
a2amesh_state_projection_lag_seconds
a2amesh_state_task_count{state}
a2amesh_state_card_count
a2amesh_state_dedupe_conflict_total
a2amesh_state_cursor_invalid_total
a2amesh_redis_used_memory_bytes
a2amesh_redis_connected_clients
a2amesh_redis_evicted_keys
a2amesh_redis_aof_rewrite_in_progress
a2amesh_redis_last_save_age_seconds
```

`evicted_keys > 0` 在 noeviction 目标配置下视为 P1 配置/历史故障。

---

# 8. Runtime 与工具指标

| 指标 | 口径 |
|---|---|
| `a2amesh_runtime_start_total{runtime,result}` | 子进程启动 |
| `a2amesh_runtime_duration_seconds{runtime,result}` | 执行耗时 |
| `a2amesh_runtime_exit_total{runtime,exit_class}` | 正常/非零/信号/timeout |
| `a2amesh_runtime_silent_seconds{runtime}` | 最近结构化/输出事件年龄 |
| `a2amesh_runtime_output_bytes_total{stream}` | 脱敏前计数，不记录内容 |
| `a2amesh_tool_call_total{tool,risk,result}` | 工具调用 |
| `a2amesh_tool_duration_seconds{tool,result}` | 工具耗时 |
| `a2amesh_tool_policy_reject_total{risk}` | 策略拒绝 |
| `a2amesh_workspace_lock_wait_seconds` | workspace lock 等待 |

Tool 名来自受控 registry，可作为低基数 label；动态命令不能作为 label。

---

# 9. SSE 与 Push 指标

```text
a2amesh_sse_connections
a2amesh_sse_connection_duration_seconds
a2amesh_sse_reconnect_total
a2amesh_sse_slow_consumer_disconnect_total
a2amesh_sse_keepalive_total
a2amesh_push_config_count
a2amesh_push_delivery_total{result}
a2amesh_push_delivery_duration_seconds{result}
a2amesh_push_retry_total{error_class}
a2amesh_push_due_count
a2amesh_push_dlq_count
a2amesh_push_ssrf_reject_total{reason}
```

SSE keepalive 数量不作为任务进度指标。

---

# 10. Observer 指标

```text
a2amesh_observer_rule_match_total{rule}
a2amesh_observer_analysis_total{result}
a2amesh_observer_recommendation_total{type}
a2amesh_observer_intervention_total{type,result}
a2amesh_observer_feedback_block_total{reason}
a2amesh_observer_tokens_total{model}
```

Observer 只对规则筛选后的事件调用 LLM。普通 heartbeat 导致分析调用视为缺陷并告警。

---

# 11. 审计事件

必须记录成功和失败：

| 分类 | 事件 |
|---|---|
| Agent | register/update/unregister、online/offline、runtime probe |
| Task | submit/dedupe/conflict、dispatch、lease acquire/lost、terminal |
| Runtime | start/timeout/cancel/kill failure |
| Tool | policy allow/reject、外部副作用尝试 |
| Card | public/extended 获取、签名验证失败 |
| Push | config create/get/list/delete、delivery/DLQ、SSRF reject |
| Observer | rule match、analysis、recommendation、intervention/blocked |
| 运维 | 配置变更、备份恢复、手工 retry/reassign/cancel |

审计字段：

```text
event_id / occurred_at
mesh_id
request_id / trace_id
task_id / context_id / message_id（可空）
actor_principal / actor_agent_id / target_agent_id
instance_id / runtime / operation
action / result / error_class
before_summary / after_summary（脱敏）
source_ip（Gateway only，按策略脱敏）
```

V1 无 tenant/user/RBAC 字段；不要伪造不存在的身份层级。

---

# 12. 日志与隐私

## 12.1 结构化日志

```json
{
  "timestamp": "2026-08-14T04:00:00Z",
  "level": "INFO",
  "component": "task-supervisor",
  "event": "task_phase_changed",
  "requestId": "req-...",
  "traceId": "...",
  "taskId": "task-...",
  "agentId": "windows-a",
  "phase": "tool_running",
  "attempt": 1
}
```

## 12.2 禁止记录

- NKey seed、Bearer、Webhook credential；
- 完整环境变量；
- 原始 Chain-of-Thought；
- 未脱敏 prompt/stdout/tool 参数；
- 文件正文和大 Artifact；
- Redis 密码/内部管理 URL；
- Windows 用户目录等不必要绝对路径。

## 12.3 内容级别

| 级别 | 保存 |
|---|---|
| 普通 | 高层摘要和 hash/length |
| 敏感 | hash、长度、分类，不保存文本 |
| 高敏 | 只保存计数/结果，不保存 hash |

---

# 13. Trace

使用 W3C Trace Context。建议 Span：

```text
Gateway.SendMessage
  State.claim_message
  Core.route
  NATS.request
  Peer.acquire_lease
  Runtime.execute
    Tool.call
  JetStream.publish
  Projector.transition
  Push.deliver / SSE.send
```

跨 NATS Envelope 传 trace context；不能信任任意外部 baggage。Span attribute 避免 prompt、输出和高基数内容。

---

# 14. 数据保留

| 数据 | 在线保留 | 后续 |
|---|---:|---|
| Gateway/组件日志 | 30 天 | 脱敏归档或删除 |
| Task 审计 | 180 天 | 冷归档摘要 |
| 高风险 Tool/运维审计 | 365 天 | 按部署治理策略 |
| Trace | 7～14 天 | 采样/删除 |
| Metrics 原始 | 30 天 | 降采样聚合 |
| 日/周/月聚合 | 长期 | 不可逆聚合 |
| Push delivery/DLQ | 7 天 | 删除 credential/响应内容 |
| JetStream Task event | 24 小时（默认） | 重要事实已投影/审计 |

Task/Artifact 业务保留由 Redis/对象存储文档规定。

---

# 15. 健康检查

## 15.1 状态

```text
UP        核心依赖可用，可接受新任务
DEGRADED  查询/管理可用，部分调度/Push/Observer 不可用
DOWN      Gateway/Core/State 不能安全处理请求
```

## 15.2 检查项

| 检查 | 频率 | 健康条件 |
|---|---:|---|
| Gateway event loop | 30 秒 | 路由可响应 |
| NATS | 15 秒 | 已连接，RPC probe 正常 |
| JetStream | 30 秒 | stream/consumer 可读写，lag 低于阈值 |
| State Service | 15 秒 | ping + 原子脚本版本正确 |
| Redis | 30 秒 | 连接/延迟/AOF/内存正常 |
| Agent presence | 5 秒输入 | lastSeen 阈值 |
| Runtime probe | 启动+5 分钟 | executable/version/smoke 正常 |
| TaskSupervisor | 5 秒输入 | heartbeat/lease 正常 |
| Push | 1 分钟 | due/DLQ/失败率低于阈值 |
| Backup | 每日 | 最近成功备份和恢复演练在期限内 |

Health 响应不得暴露内部地址、版本漏洞或 secret。

---

# 16. 告警

| OBS ID | 条件 | 级别 |
|---|---|---|
| OBS-ALERT-001 | NATS 连续 3 次 probe 失败 | P1 |
| OBS-ALERT-002 | Redis/State 不可写 | P1 |
| OBS-ALERT-003 | `evicted_keys > 0` 或磁盘 stop-writes | P1 |
| OBS-ALERT-004 | RUNNING Task lease 过期且恢复失败 | P1 |
| OBS-ALERT-005 | Agent offline 且持有活动 Task | P1 |
| OBS-ALERT-006 | JetStream Projector lag > 30 秒 | P1 |
| OBS-ALERT-007 | Task 最老 queued > 5 分钟 | P2 |
| OBS-ALERT-008 | 10 分钟 failed 比例 >20% 且样本≥20 | P2 |
| OBS-ALERT-009 | Runtime probe 失败 | P2 |
| OBS-ALERT-010 | Push DLQ 非空或 lag 持续增长 | P2 |
| OBS-ALERT-011 | SSE slow consumer 激增 | P3 |
| OBS-ALERT-012 | Observer 普通 heartbeat 分析调用 >0 | P2 |
| OBS-ALERT-013 | Observer 自动干预达到上限 | P2 |
| OBS-ALERT-014 | 备份超期或恢复演练失败 | P1 |
| OBS-ALERT-015 | Agent Card 官方校验失败 | P2 |

阈值为初始值，真机压测后校准。告警包含 agent/task/request/trace 标识，不附 prompt/output。

---

# 17. 看板

1. Mesh 总览：在线 Agent、活动/终态 Task、成功率、P95。
2. Agent：presence、Runtime、capacity、running、最近失败。
3. Task：状态/phase、queue/duration、stalled/recovery/cancel。
4. NATS/JetStream：连接、RPC、lag、redelivery、存储。
5. Redis/State：延迟、错误、内存、AOF、projection lag、lease。
6. Runtime/Tool：启动、退出、耗时、策略拒绝。
7. SSE/Push：连接、重连、投递、重试、DLQ、SSRF。
8. Observer：规则命中、分析、建议、干预、防环。

不提供思维链查看页面。

---

# 18. 运行手册要求

每个 P1 告警必须链接 Runbook，至少包括：影响、快速判断、禁止操作、恢复步骤、数据一致性检查和升级联系人。重点 Runbook：NATS 不可用、Redis 不可写、lease split-brain、Projector lag、Windows orphan process、Push SSRF、备份恢复。

---

# 19. 验收标准

1. 成功/失败请求均可按 operation/result 统计。
2. Task phase、heartbeat、lease、recovery 可观察且口径一致。
3. Metrics 无 taskId/messageId 高基数 label。
4. 审计可从外部 request 追到 Runtime/Tool/终态。
5. 日志不含 secret、思维链和未脱敏内容。
6. NATS/Redis/Peer/Gateway 故障可触发对应告警。
7. Push DLQ、SSRF、Observer 防环有指标和审计。
8. Health 在依赖故障时正确区分 DEGRADED/DOWN。
9. 备份可在隔离环境恢复并通过 Task/Card 数据校验。
10. 看板数据可与 Redis/JetStream 权威状态抽样对账。
