# A2AMesh 统计、审计与运行监控规则 V1.4
> 文档ID：`A2AM-OBS-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：指标、审计、Trace、健康、告警与保留
> 目标读者：运维、SRE、测试、安全、架构
> 评审状态：文档自检通过；监控/告警/恢复演练待完成
> 最后更新：2026-08-14
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 替代版本：V1.3
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh V1 的运行指标、任务统计、Agent/Runtime 健康、审计事件、日志与隐私、Trace、告警、数据保留和运行看板。平台只记录网格和任务可观测事实，不复制 Runtime 供应商内部遥测，也不保存模型原始思维链。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 Agent、Task、NATS、Redis、Runtime、SSE、Push、Observer 指标和告警规则 |
| V1.1 | 2026-08-14 | 补齐Registry/Gateway、gRPC/MCP指标、验收ID、文档控制和参考依据 |
| V1.2 | 2026-08-14 | 补齐Principal、Credential、OAuth、跨Binding幂等指标和审计 |
| V1.3 | 2026-08-14 | 增加outbox、副作用对账、授权、准入、RTO/RPO与兼容性观测门禁 |
| V1.4 | 2026-08-14 | 增加 Artifact、受信配置和 reconciliation case 的指标、告警、审计与运行手册门禁 |

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
| A2A gRPC | Gateway interceptor/channel/server metrics |
| MCP Client/Bridge | MCP connector/gateway/OAuth audit |
| Identity/Credential | Gateway interceptor、NATS AuthContext verifier、State Principal Registry |
| OAuth AS/JWKS | MCP OAuth middleware、metadata/JWKS cache |
| Redis 健康 | Redis INFO/latency/AOF |
| Artifact blob/metadata | Object Store metrics + Redis ArtifactRecord/Task reference |
| Trusted config | Config Controller、active generation、component READY、publisher lease |
| Reconciliation | Case Store、effect ledger、evidence/audit、provider collector |

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

Registry/Gateway 还必须暴露：

```text
a2amesh_registry_rpc_total{operation,result}
a2amesh_registry_query_duration_seconds{operation}
a2amesh_registry_result_count{operation}
a2amesh_gateway_virtual_route_total{result}
a2amesh_gateway_unknown_agent_total
a2amesh_gateway_auth_total{result}
a2amesh_grpc_requests_total{method,status}
a2amesh_grpc_streams_active{method}
a2amesh_grpc_duration_seconds{method,status}
a2amesh_mcp_requests_total{method,transport,result}
a2amesh_mcp_tool_calls_total{tool_class,result}
a2amesh_mcp_oauth_total{result}
a2amesh_mcp_origin_reject_total
a2amesh_identity_resolution_total{method,result}
a2amesh_auth_context_verify_total{method,result}
a2amesh_credential_auth_total{method,result}
a2amesh_principal_alias_total{result}
a2amesh_oauth_token_validation_total{result}
a2amesh_oauth_jwks_refresh_total{result}
a2amesh_oauth_jwks_cache_age_seconds
a2amesh_tenant_reject_total{binding}
```
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
| `a2amesh_mcp_submit_dedupe_total{result}` | MCP created/same/conflict/missing_message_id |

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
a2amesh_event_relay_publish_total{result}
a2amesh_event_relay_publish_duration_seconds{result}
a2amesh_event_relay_duplicate_total
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
a2amesh_state_outbox_due_count
a2amesh_state_outbox_oldest_age_seconds
a2amesh_state_outbox_dead_count
a2amesh_state_task_count{state}
a2amesh_state_card_count
a2amesh_state_dedupe_conflict_total
a2amesh_state_cursor_invalid_total
a2amesh_redis_used_memory_bytes
a2amesh_redis_connected_clients
a2amesh_redis_evicted_keys
a2amesh_redis_aof_rewrite_in_progress
a2amesh_redis_last_save_age_seconds
a2amesh_effect_count{state,risk}
a2amesh_effect_reconcile_total{result}
a2amesh_reconciliation_required_count
a2amesh_capability_denied_total{reason}
a2amesh_admission_queued{scope}
a2amesh_admission_rejected_total{scope,reason}
a2amesh_admission_wait_seconds{result}
a2amesh_artifact_count{status}
a2amesh_artifact_upload_total{result}
a2amesh_artifact_integrity_failure_total{kind}
a2amesh_artifact_orphan_count{kind}
a2amesh_artifact_reaper_backlog
a2amesh_object_store_request_duration_seconds{operation,result}
a2amesh_config_bundle_total{state,result}
a2amesh_config_active_generation
a2amesh_config_component_ready{component_type}
a2amesh_config_generation_mismatch_total{component_type}
a2amesh_card_publisher_fencing_reject_total
a2amesh_reconciliation_case_count{state,priority}
a2amesh_reconciliation_case_age_seconds{state,priority}
a2amesh_reconciliation_claim_expired_total
a2amesh_reconciliation_resolution_total{result}
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
| `a2amesh_side_effect_total{risk,state}` | 副作用账本状态变化 |
| `a2amesh_side_effect_reconcile_duration_seconds{result}` | provider/local 对账耗时 |

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
| Identity | credential create/rotate/disable/expire、principal resolve、alias create/disable/conflict |
| AuthContext | signature verify/replay/expired/signer mismatch |
| OAuth | metadata/JWKS refresh、token accepted/rejected、issuer/audience/scope/kid failure |
| Tenant | 非空 tenant 按 Binding 拒绝，只记录 `tenant_rejected=true`，不记录值/hash/长度 |
| Task | submit/dedupe/conflict、dispatch、lease acquire/lost、terminal |
| Runtime | start/timeout/cancel/kill failure |
| Tool | policy allow/reject、外部副作用尝试 |
| Card | public/extended 获取、签名验证失败 |
| Push | config create/get/list/delete、delivery/DLQ、SSRF reject |
| Observer | rule match、analysis、recommendation、intervention/blocked |
| Artifact | upload create/finalize/quarantine/download ticket/delete/orphan/integrity failure |
| Config | validate/stage/READY/NACK/activate/supersede/rollback/revoke/publisher lease/fencing reject |
| Reconciliation | case open/claim/expire/escalate/evidence/resolve/close/reopen/conflict |
| 运维 | 备份恢复、手工 retry/reassign/cancel |

审计字段：

```text
event_id / occurred_at
mesh_id
request_id / trace_id
task_id / context_id / message_id（可空）
actor_principal / actor_agent_id / target_agent_id
credential_id / auth_method / issuer_hash / alias_generation（可空、均非 secret）
instance_id / runtime / operation
action / result / error_class
before_summary / after_summary（脱敏）
source_ip（Gateway only，按策略脱敏）
```

V1 无 tenant/user/RBAC 字段；`actor_principal` 是机器/Agent Credential 的 Canonical Principal，不得伪造成用户身份。tenant 仅记录 `tenant_rejected=true`，不保存传入值。

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
  Identity.resolve_principal
  AuthContext.sign_or_verify
  State.claim_message
  Core.route
  NATS.request
  Peer.acquire_lease
  Runtime.execute
    Tool.call
  State.transition_and_outbox
  EventRelay.publish
  Projector.derived_view
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
| Redis delivered outbox | 0～1 小时 | PubAck 后删除或短留诊断 |
| Redis dead outbox | 7 天 | 修复/审计后删除 |
| Side-effect ledger | 与 Task 审计至少同周期 | 脱敏冷归档 |
| Artifact blob/metadata | 默认 30 天，受 policy/保留锁约束 | 对象删除与 tombstone 对账 |
| Trusted config bundle/audit | 至少 365 天 | 加密不可变归档 |
| Reconciliation case/evidence/audit | 至少 365 天 | 不因 Task TTL 提前删除 |

精确 Artifact、配置和对账保留分别以三份专项文档为准；监控系统不得擅自缩短业务/审计保留。

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
| Event Relay | 15 秒 | due outbox 可发布并收到 PubAck，oldest age 低于阈值 |
| State Service | 15 秒 | ping + 原子脚本版本正确 |
| Redis | 30 秒 | 连接/延迟/AOF/内存正常 |
| Agent presence | 5 秒输入 | lastSeen 阈值 |
| Runtime probe | 启动+5 分钟 | executable/version/smoke 正常 |
| TaskSupervisor | 5 秒输入 | heartbeat/lease 正常 |
| Push | 1 分钟 | due/DLQ/失败率低于阈值 |
| Backup | 每日 | 最近成功备份和恢复演练在期限内 |
| Identity Registry | 30 秒 | Credential/Alias 配置 generation 可读且脚本版本正确 |
| Capability/Admission | 30 秒 | grant generation、队列计数和运行计数可对账 |
| OAuth AS metadata/JWKS | 60 秒 | issuer metadata、已知 kid 和 cache age；不发送业务 Token |
| Config Controller | 30 秒 | active bundle 签名/expiry、generation 一致、必选组件 READY |
| Card publisher | 5 秒输入 | active generation、lease 未过期、fencing 单一 |
| Object Store/Artifact Broker | 30 秒 | 私有 bucket 可 HEAD/PUT probe、Reaper backlog 和 integrity 正常 |
| Reconciliation | 30 秒 | case 索引/ledger 可对账、collector 和 claim lease 正常 |

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
| OBS-ALERT-016 | AuthContext 签名/重放拒绝率异常升高 | P1 |
| OBS-ALERT-017 | OAuth issuer/JWKS 不可用且 cache 接近 15 分钟上限 | P1 |
| OBS-ALERT-018 | OAuth audience/scope/kid 拒绝率异常升高 | P2 |
| OBS-ALERT-019 | MCP dedupe conflict 或 missing messageId 持续出现 | P2 |
| OBS-ALERT-020 | 任一 Binding 收到非空 tenant | P2 |
| OBS-ALERT-021 | outbox oldest age > 30 秒或 dead outbox 非空 | P1 |
| OBS-ALERT-022 | effect `UNKNOWN` 超过 5 分钟或 reconciliation backlog 增长 | P1 |
| OBS-ALERT-023 | capability deny 异常升高或 grant generation 不一致 | P2 |
| OBS-ALERT-024 | Principal queue 超限持续、全局队列接近上限或计数对账失败 | P1/P2 |
| OBS-ALERT-025 | 最近异机恢复点超过 15 分钟或 RTO 演练失败 | P1 |
| OBS-ALERT-026 | active config 签名/expiry/generation 不一致或必选组件无 READY | P1 |
| OBS-ALERT-027 | Card publisher split brain、旧 fencing 写入或无可用候选 | P1 |
| OBS-ALERT-028 | AVAILABLE Artifact 缺失/hash 不符、Object Store 不可用或 Reaper backlog 超限 | P1 |
| OBS-ALERT-029 | UNKNOWN case 15 分钟未 claim、claim 反复过期或 resolution backlog 增长 | P1 |
| OBS-ALERT-030 | Artifact orphan/quarantine 比例异常或下载拒绝率异常 | P2 |

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
9. Identity/OAuth：Credential 状态、Principal resolve、AuthContext、JWKS cache、Token reject、MCP dedupe。
10. Delivery/Recovery：outbox age/dead、Relay PubAck、effect UNKNOWN/reconciliation、最近恢复点和演练 RTO。
11. Admission/AuthZ：grant generation、deny reason、全局/Principal queued/running、公平等待分布、429/503 比例。
12. Artifact：状态/字节、上传/finalize、完整性、orphan、Reaper、Object Store 延迟和恢复点。
13. Config：active/staged generation、签名/expiry、组件 READY/NACK、回滚撤销和 publisher lease/fencing。
14. Reconciliation：OPEN/CLAIMED/ESCALATED 年龄、claim expiry、evidence 类型、resolution 和 SLA。

不提供思维链查看页面。

---

# 18. 运行手册要求

每个 P1 告警必须链接 Runbook，至少包括：影响、快速判断、禁止操作、恢复步骤、数据一致性检查和升级联系人。重点 Runbook：NATS 不可用、Redis 不可写、Event Relay/outbox 堵塞、lease split-brain、Projector lag、UNKNOWN case 认领/证据/裁决、Windows orphan process、Push SSRF、OAuth AS/JWKS outage、AuthContext 签名异常、signed config 过期/回滚/撤销、Card publisher split brain、Artifact 完整性/orphan/Object Store outage、准入计数修复、跨 Redis/JetStream/Object Store/配置的备份恢复。

---

# 19. 验收标准

- **TEST-OBS-001**：成功/失败、Task phase/heartbeat/lease/recovery、Registry/Gateway/Push/Observer 均可按稳定低基数维度统计。
- **TEST-GRPC-001**：unary/streaming 的 method/status/deadline/cancel 指标和 Trace 可关联。
- **TEST-MCP-001**：MCP transport/tool/resource/OAuth/Origin/stdio process 指标与审计完整且无 secret。
- **TEST-IDENTITY-001**：NKey/Bearer/OAuth resolve、alias、Credential rotation、AuthContext 拒绝均有可对账审计。
- **TEST-MCP-IDEMP-001**：created/same/conflict/missing_message_id 指标与 Task/dedupe 状态一致。
- **TEST-OAUTH-001**：metadata/JWKS rotation/outage、cache age、Token reject 和 fail-closed 告警可演练。
- **TEST-TENANT-001**：非空 tenant 只记录 Binding/result，不保存值且不产生 Task。
- **TEST-PERF-001**：固定负载下采集 RPC、Registry、State、Runtime、Projector 的 P50/P95/P99 并验证 NFR。
- **TEST-PRESENCE-001**：Agent heartbeat、suspect/offline、多 instance 聚合和告警时序一致。
- **TEST-TRACE-001**：审计可从外部 request 追到 Gateway、State、NATS、Runtime、Tool 和终态。
- **TEST-SEC-001**：Metrics 无 taskId/messageId 高基数 label；日志不含 secret、思维链和未脱敏内容。
- **TEST-ALERT-001**：NATS/Redis/Peer/Gateway/lease/Projector/Push/Observer 故障触发对应告警与 Runbook。
- **TEST-HEALTH-001**：依赖故障时正确区分 UP/DEGRADED/DOWN。
- **TEST-BACKUP-001**：备份可在隔离环境恢复并通过 Task/Card/索引数据校验。
- **TEST-RECON-001**：看板数据与 Redis/JetStream 权威状态抽样对账一致。
- **TEST-OUTBOX-001**：Relay publish/PubAck/duplicate/dead 指标与 Redis outbox、JetStream 实际状态一致。
- **TEST-EFFECT-001**：effect 状态、UNKNOWN 时长、reconciliation 结果和补偿审计可对账。
- **TEST-AUTHZ-001**：授权拒绝按稳定低基数 reason 统计，不记录 Principal 原文或敏感 grant 内容。
- **TEST-ADMISSION-001**：429/503、全局/Principal 队列、等待分布、公平性和计数泄漏告警可演练。
- **TEST-DR-001**：最近恢复点、15 分钟服务 RTO、4 小时整机 RTO 和 15 分钟 RPO 门禁可观测。
- **TEST-ARTIFACT-OBS-001**：上传/finalize/下载/删除、完整性、orphan、Reaper、Object Store outage 和恢复指标/告警/审计一致。
- **TEST-CONFIG-OBS-001**：签名、expiry、generation、READY/NACK、激活/回滚/撤销和 publisher fencing 全程可审计告警。
- **TEST-RECON-SLA-001**：5 分钟 P1、15 分钟未 claim 升级、10 分钟 lease、evidence/resolution/conflict 和终态不改写可观测。
---

# 20. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.4](A2AMesh_业务与总体架构设计_V1.4.md)
- [AgentCard与协议对象规范 V1.4](A2AMesh_AgentCard与协议对象规范_V1.4.md)
- [A2A协议与NATS集成适配设计 V1.4](A2AMesh_A2A协议与NATS集成适配设计_V1.4.md)
- [Redis状态平面与数据设计 V1.4](A2AMesh_Redis状态平面与数据设计_V1.4.md)
- [任务生命周期与长任务运行时设计 V1.4](A2AMesh_任务生命周期与长任务运行时设计_V1.4.md)
- [编排器 Runtime与工具适配设计 V1.4](A2AMesh_编排器_Runtime与工具适配设计_V1.4.md)
- [接口请求与响应标准 V1.4](A2AMesh_接口请求与响应标准_V1.4.md)
- [Artifact与对象存储设计 V1.0](A2AMesh_Artifact与对象存储设计_V1.0.md)
- [受信配置与变更治理设计 V1.0](A2AMesh_受信配置与变更治理设计_V1.0.md)
- [人工对账与运维操作设计 V1.0](A2AMesh_人工对账与运维操作设计_V1.0.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
- [A2A v1.0.1 canonical Proto](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)
- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A Custom Protocol Bindings](https://a2a-protocol.org/latest/topics/custom-protocol-bindings/)
- [MCP Specification 2026-07-28 Release](https://github.com/modelcontextprotocol/modelcontextprotocol/releases/tag/2026-07-28)
- [RFC 9728 OAuth 2.0 Protected Resource Metadata](https://www.rfc-editor.org/rfc/rfc9728)
- [RFC 8414 OAuth 2.0 Authorization Server Metadata](https://www.rfc-editor.org/rfc/rfc8414)
- [RFC 8707 Resource Indicators for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc8707)
