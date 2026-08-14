# A2AMesh 任务生命周期与长任务运行时设计 V1.3
> 文档ID：`A2AM-RUN-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：Task状态机、Supervisor、进度、SSE、Push与恢复
> 目标读者：Runtime、Gateway、前端、测试、运维
> 评审状态：文档自检通过；长任务故障注入待完成
> 最后更新：2026-08-14
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 替代版本：V1.2
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh Task 从受理、排队、执行、流式进度、工具运行、等待输入、取消、完成、失败到崩溃恢复的完整运行时，重点解决数分钟长任务中“仍在推理、正在执行工具、连接断开或进程崩溃无法区分”的问题。

Task 标准对象见《Agent Card 与协议对象规范》；Redis/lease 见《Redis 状态平面与数据设计》；SSE/Push 接口见《接口请求与响应标准》。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立异步 TaskSupervisor、四维状态、Progress、heartbeat、SSE/Push/GetTask、Observer 和恢复规则 |
| V1.1 | 2026-08-14 | 落地EVT/TEST标识并补齐长任务验收追踪 |
| V1.2 | 2026-08-14 | 补齐跨Binding Task所有权、取消、恢复和审计主体规则 |
| V1.3 | 2026-08-14 | 闭合状态提交、副作用回执、取消后对账、未知结果与灾难恢复语义 |

## 1.2 当前问题

当前 `runtime/executor.py` 主要依赖子进程 stdout `readline()` 触发 `on_stream`。Runtime 静默、工具阻塞或输出没有换行时，没有独立 heartbeat；任务最终状态主要由进程结束后产生。因此当前实现是“部分流式”，不是本文目标长任务运行时。

---

# 2. 设计决策

> 异步 TaskSupervisor + Redis 原子快照/outbox + Event Relay + JetStream 单一实时事件日志 + SSE 在线订阅 + A2A Push 离线通知 + GetTask 断线校准 + Observer 规则过滤。

```text
TaskSupervisor ─State mutation─▶ Redis Task + outbox ─▶ GetTask/ListTasks
                                             │
                                             ▼
                                        Event Relay
                                             │ PubAck
                                             ▼
                                        JetStream
                                      ┌──────┼────────┐
                                      ▼      ▼        ▼
                                     SSE    Push    Observer
```

Push、SSE、GetTask 不各自产生进度，只消费同一事件/快照。

---

# 3. 状态模型

## 3.1 标准 TaskState

```text
SUBMITTED → WORKING
WORKING ↔ INPUT_REQUIRED
WORKING ↔ AUTH_REQUIRED
WORKING → COMPLETED / FAILED / CANCELED / REJECTED
```

终态不可迁出。

## 3.2 内部执行 phase

```text
queued
planning
model_running
runtime_running
tool_start
tool_running
tool_progress
tool_end
waiting_external
retrying
finalizing
canceling
recovering
```

phase 通过 Progress Extension 表达，不增加官方 TaskState。

## 3.3 四维前端状态

| 维度 | 示例 | 权威来源 |
|---|---|---|
| Task 生命周期 | WORKING/FAILED | Redis Task |
| 执行 phase | tool_running | Progress 快照 |
| Agent 健康 | online/suspect/offline | Presence |
| 传输连接 | connected/reconnecting/disconnected | SSE/NATS 客户端 |

不能因 SSE 断开直接把 Task 标为 failed。

## 3.4 Task 所有权与 Canonical Principal

- Task 创建时把验证后的 `callerPrincipal`、`principalType`、`credentialId`（非 secret）和 `aliasGeneration` 固化到 Redis/审计；
- `callerPrincipal` 在 Task 生命周期内不可更改；Credential 轮换、禁用或 alias 配置变化不重写历史 Task；
- 外部 `GetTask/ListTasks/CancelTask` 只允许当前请求解析出的 Canonical Principal 与 Task caller 相同；不存在和不可访问统一 no-leak；
- target Agent/owner instance 可领取 lease、写执行事件和 Artifact，但不能因此读取该 caller 的其他 Task；
- `system:projector`、`system:push`、`system:observer` 只拥有固定组件操作，不等同业务 caller；
- MCP Task Resource 和 A2A JSON-RPC/gRPC 使用同一所有权判断，不按 Binding 建第二套访问规则；
- payload/metadata 中的 caller/owner 字段不参与判定。

---

# 4. TaskSupervisor

## 4.1 职责

每个活动 Task 一个异步 Supervisor，不创建永久 OS 线程。职责：

- 获取并续租 Task lease；
- 启动 Runtime 子进程；
- 独立发送 task heartbeat；
- 收集结构化 RuntimeEvent；
- 读取/聚合 stdout/stderr；
- 监听 cancel/deadline；
- 监测进程退出；
- 向 State Service 提交 canonical 状态/事件 mutation；
- 清理进程组和后台协程；
- 根据重试安全策略恢复。

## 4.2 结构

```python
class TaskSupervisor:
    async def run(self) -> Task:
        lease = await self.state.acquire_lease(self.task_id, self.instance_id)
        runtime = asyncio.create_task(self._run_runtime(lease))
        heartbeat = asyncio.create_task(self._heartbeat_loop(lease))
        renew = asyncio.create_task(self._lease_loop(lease))
        cancel = asyncio.create_task(self._cancel_loop(lease))
        events = asyncio.create_task(self._event_loop(lease))
        try:
            return await runtime
        finally:
            await self._stop_background(heartbeat, renew, cancel, events)
            await self._ensure_process_tree_stopped()
```

同步阻塞 SDK 通过 `asyncio.to_thread()` 或受控线程池；不为整个 Task 另建监听线程。

## 4.3 不变量

1. heartbeat 不依赖 stdout。
2. 每个副作用前校验 lease/fencing。
3. lease 续租失败立即停止新副作用。
4. Runtime 退出后所有协程和 pipe 被回收。
5. 最终状态只写一次。
6. 旧 attempt 的事件不能覆盖新 attempt。
7. 未能确认进程终止时不得宣称 canceled。
8. Task 状态/进度/Artifact 必须先由 State mutation 提交，再由 outbox Relay 发布；Supervisor 不直接声明未提交事实。
9. 外部副作用必须先写 ledger，`UNKNOWN` 未对账前不得自动重试、补偿成功或取消成功。

---

# 5. RuntimeEvent

Adapter 归一化事件：

```python
@dataclass(frozen=True)
class RuntimeEvent:
    kind: Literal[
        "runtime_started", "model_started", "output_chunk",
        "tool_started", "tool_progress", "tool_finished",
        "waiting_external", "heartbeat", "runtime_finished"
    ]
    occurred_at: datetime
    summary: str | None = None
    tool_name: str | None = None
    current: int | None = None
    total: int | None = None
    source: Literal["runtime_reported", "supervisor_inferred"] = "runtime_reported"
```

若 Runtime 只提供文本：

- 启动后 phase=`runtime_running`；
- stdout 作为脱敏 output preview；
- 不从“Running tool...”等自然语言猜精确阶段；
- process poll + heartbeat 证明存活，不声称正在推理。

---

# 6. EVT-HEARTBEAT-001：Heartbeat 与 Lease

| 信号 | 默认 | 用途 |
|---|---:|---|
| SSE comment keepalive | 10 秒 | 浏览器→Gateway 连接 |
| Agent presence | 5 秒 | Peer 进程健康 |
| Task heartbeat | 5 秒 | Supervisor 健康 |
| Task lease | TTL 30 秒，10 秒续租 | 执行所有权/fencing |
| stalled 阈值 | 15 秒无 task heartbeat 或 phase 超策略 | UI/Observer 提示 |
| agent suspect/offline | 15/30 秒 | 调度与恢复 |
| hard timeout | 每任务配置 | 强制停止上限 |

所有周期可配置并加 jitter。heartbeat 可以不进入外部 Push，但 TaskSupervisor 必须通过 State mutation 原子更新 Redis heartbeat/lease/outbox；Event Relay 再发布到 JetStream，监控同时核对 Redis 与 JetStream 水位。

---

# 7. 受理与执行

## 7.1 推荐客户端模式

```text
SendMessage(returnImmediately=true)
→ 立即得到 SUBMITTED/WORKING Task
→ SubscribeToTask
→ 断线时 GetTask
→ 未终态则重新 SubscribeToTask
```

不要让一个 HTTP 请求阻塞数分钟后才返回 Task ID。

## 7.2 执行步骤

1. Gateway/Core 验证凭据并完成协议对象、请求大小的静态校验。
2. State `claim_message` 原子解析 Canonical Principal、复核 capability/admission、生成/复用 Task、写 SUBMITTED 快照和 outbox。
3. Core NATS dispatch；Event Relay 独立发布已提交事件；Peer 领取 lease。
4. `transition_task` 原子写 WORKING/phase 和 outbox。
5. 启动 Runtime，收集结构化事件、stdout/stderr 和独立 heartbeat。
6. 每次可见进度、Artifact 元数据和状态变化先提交 Redis，再由 Relay 发布对应 eventSequence。
7. 外部副作用在执行前写 `PREPARED/APPLYING` ledger，完成后写 `APPLIED/FAILED/UNKNOWN`。
8. finalizing 校验 lease、退出码和所有 effect 状态。
9. 原子写 terminal Task、Artifact 元数据和终态 outbox。
10. 终态提交后即可释放 lease 和清理本地资源；Relay 独立发布终态并获 PubAck，SSE/Push 延迟不反向阻塞权威终态。

---

# 8. EVT-PROGRESS-001：Progress 事件

使用标准 `TaskStatusUpdateEvent`，扩展数据见对象规范。每条内部事件还带：

```text
eventSequence
taskVersion
attempt
ownerInstanceId
occurredAt
canonical StreamResponse
```

规则：

- phase 变化立即提交 State mutation，并由 Relay 发布；
- heartbeat 最多每 5 秒一条；
- token/stdout 以 100～500 ms 窗口合并，配置速率上限；
- 终态前 flush 缓冲；
- 最终输出使用 Artifact；
- 不发送原始 Chain-of-Thought。

---

# 9. SSE

## 9.1 响应

```http
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
Connection: keep-alive
```

每 10 秒：

```text
: keepalive

```

comment 不写 Redis/JetStream，不增加 eventSequence。

## 9.2 断线

- 浏览器 no-byte 超过 25 秒进入 reconnecting；
- 退避 1s、2s、5s、10s，最大 30s；
- 重连前 GetTask；
- Task 未终态再 Subscribe；
- 标准 A2A 不承诺补齐所有瞬时 status message；重要事实必须进入 Task/Artifact。

## 9.3 慢客户端

每订阅者设置缓冲上限。超过上限时关闭该订阅并返回可重连提示，不能阻塞 JetStream consumer 或 Runtime。

---

# 10. A2A Push

## 10.1 定位

Push 是离线/跨系统 HTTP Webhook，不是内部总线。payload 为单个标准 `StreamResponse`。

## 10.2 Dispatcher

```text
JetStream event
→ 判断 task push configs/事件等级
→ 创建 deliveryId
→ SSRF 校验/DNS 解析
→ HTTP POST（10 秒超时）
→ 2xx success；其他按策略重试
→ 超限进入 DLQ
```

默认推送：phase 变化、input/auth required、tool failure、recovering 和终态。普通 heartbeat 不推外部 Webhook。

## 10.3 安全

- 生产仅 HTTPS；
- 拒绝 loopback、RFC1918、link-local、metadata IP；
- 每次发送及重定向重新解析；
- 限制响应大小和跳转次数；
- credential 加密；
- payload 签名；
- 至少一次投递，客户端按 `taskId + eventSequence + deliveryId` 去重。

---

# 11. GetTask 轮询

GetTask 是权威校准和兜底，不是主实时通道。

- SSE 正常：不轮询，或 30～60 秒低频校准；
- SSE/Push 不可用：2s、5s、10s、30s 退避；
- 终态停止；
- 轮询读取 Redis，不直接请求运行 Peer；
- 可结合 task version/ETag 避免重复大响应。

---

# 12. Cancel 与 Timeout

## 12.1 Cancel

```text
CancelTask
→ State request_cancel CAS
→ control subject 发 owner instance
→ Supervisor phase=canceling
→ terminate/kill process tree
→ 确认退出
→ 检查 side-effect ledger
→ 无不可逆 effect 或补偿成功：TASK_STATE_CANCELED
→ 存在 APPLIED/UNKNOWN 且未完成对账：TASK_STATE_FAILED + reconciliation_required
```

Linux：新 process group + TERM grace + KILL。Windows：CREATE_NEW_PROCESS_GROUP/Job Object + grace + terminate tree。进程退出只证明本地执行已停止，不证明远端副作用未发生。

## 12.2 Timeout

- soft deadline：发布提示，允许 Runtime 收尾；
- hard deadline：执行取消；
- kill 失败：Task FAILED，错误为 runtime termination failure；
- timeout 是否重试由副作用策略决定。

---

# 13. 重试与副作用安全

## 13.1 重试分类

| 任务类型 | 自动重试 | 条件 |
|---|---|---|
| 只读分析 | 是 | 同 messageId、输入不变 |
| 测试/编译 | 通常是 | workspace 状态受控 |
| 生成未发布草稿 | 是 | 输出路径 attempt 隔离 |
| 文件修改 | 默认否 | 除非事务/快照/幂等补丁证明 |
| shell 任意命令 | 否 | 需人工确认 |
| 外部 API 写入 | 否 | 除非外部 idempotency key |
| 发布/部署/付款 | 否 | 必须人工或业务事务 |

人工重试创建新 attempt 并关联原 Task 事实；不覆盖失败审计。

## 13.2 Side-effect ledger

每个 `WORKSPACE_WRITE`、`SYSTEM_WRITE` 或 `EXTERNAL_SIDE_EFFECT` 步骤在真正执行前创建 effect 记录：

```text
PREPARED → APPLYING → APPLIED
                    ↘ UNKNOWN
PREPARED/APPLYING/APPLIED → COMPENSATED
PREPARED/APPLYING → FAILED
```

- provider 支持幂等时使用稳定 `effectId` 派生 idempotency key，并记录脱敏 provider reference；
- 收到明确成功回执才写 `APPLIED`，明确未执行/业务拒绝才写 `FAILED`；
- timeout、连接断开、进程崩溃或响应丢失且无法证明未执行时写 `UNKNOWN`；
- `UNKNOWN` 不得按“可能失败”自动重放，必须查询 provider、本地不可变回执或人工对账；
- 补偿是新的受审计动作，成功后写 `COMPENSATED`，不能删除原 `APPLIED` 事实；
- Task 终态必须汇总 effect：存在未解决 `UNKNOWN` 或不可逆 `APPLIED` 且请求取消时，使用 `FAILED + reconciliation_required`，而不是 `CANCELED`。

---

# 14. 崩溃恢复

## 14.1 Peer/Runtime 崩溃

1. task heartbeat 停止；
2. Agent presence suspect/offline；
3. lease 过期，内部 phase=recovering；
4. 查询 side-effect ledger、本地恢复记录和 provider 状态；
5. 只有全部 effect 为无副作用、明确 `FAILED` 或具有可证明的 provider 幂等结果时，retry-safe 任务才由新 attempt 接管；
6. 存在 `UNKNOWN` 时 Task 进入 `FAILED + reconciliation_required` 或受控等待，禁止自动重放；
7. 旧 owner 恢复也因 fencing token 失效不能写。

## 14.2 Gateway/SSE 崩溃

Task 继续。新 Gateway 从 Redis 取 Task，以 JetStream 水位建立 live stream。

## 14.3 Redis 暂不可用

Supervisor 不能把未提交状态直接发布为权威 JetStream 事件；不能安全续 lease/写 State 时应停止新副作用、进入受控等待，Core 停止新提交。Redis 恢复后按 ledger、进程状态和 provider 证据对账，无法确认的任务标记 reconciliation required。

## 14.4 灾难恢复目标

- State/Event 相关服务重启 RTO：15 分钟；
- 完整单节点恢复 RTO：4 小时；
- 受控进程/服务重启且 Redis/JetStream 持久卷完好时目标 State RPO：0；
- 整机、磁盘或电源故障时 State/Event RPO：不超过 15 分钟，异机备份频率必须匹配；
- 恢复后先对账 Redis committed eventSeq、outbox、JetStream sequence 和 effect ledger，再开放新副作用；不能仅凭进程健康就结束恢复。

---

# 15. Observer Agent

## 15.1 流程

```text
Task events
→ 规则过滤/窗口聚合
→ anomaly/milestone
→ Observer Agent 分析
→ 建议或受控 intervention
```

触发：heartbeat 过期、phase 超时、tool failed、retrying、lease expired、input/auth required、terminal failure。

## 15.2 权限与防环

V1 不建设 RBAC，但定义本地策略 scope：

```text
task.observe
task.message.send
task.cancel
task.retry
task.reassign
```

Observer 默认只有 observe。干预必须：

- 配置明确允许；
- 同 Task 冷却时间；
- 最大自动干预次数；
- 记录 causeEventSeq；
- 不响应自己产生的事件；
- 高风险 cancel/retry 需人工或固定规则批准；
- 不直接写 Redis。

---

# 16. 前端状态矩阵

| SSE | Task HB | Agent | Lease | 展示 |
|---|---|---|---|---|
| 正常 | 新鲜 | online | 有效 | 当前 phase/tool/progress |
| 断开 | 新鲜 | online | 有效 | 连接中断，任务仍在运行，自动重连 |
| 正常 | 过期 | online | 有效 | 执行器疑似阻塞，可取消 |
| 断开 | 过期 | suspect | 即将过期 | Agent 连接不稳定 |
| 任意 | 过期 | offline | 已过期 | Agent 失联，正在恢复判定 |
| 任意 | 终态 | 任意 | — | 终态、结果和 Artifact |

任务卡片至少显示：Task ID、Agent/instance、运行时长、最近进度时间、phase、attempt、连接状态、工具摘要、Cancel。

---

# 17. 失败与补偿矩阵

| 失败点 | Task/内部状态 | 补偿 |
|---|---|---|
| NATS dispatch 失败 | SUBMITTED | 同 messageId 有限重试 |
| Runtime 不存在 | FAILED | 更新 Card/runtime health |
| Runtime 60 秒静默 | WORKING + heartbeat | 正常，不误判 |
| heartbeat 停止但进程存在 | stalled | watchdog/人工取消 |
| lease 续租失败 | recovering | 旧 owner 停止副作用 |
| SSE 断线 | Task 不变 | GetTask + 重订阅 |
| Push 失败 | Task 不变 | Dispatcher 重试/DLQ |
| Event Relay 落后 | Redis 快照已提交 | outbox 重投；SSE/Push 暂时延迟 |
| Projector 落后 | Task 查询不受影响 | 重放派生视图，禁止覆盖新快照 |
| Cancel kill 失败 | FAILED | 运维告警和进程清理 |
| unsafe task owner 丢失 | FAILED/人工 | 禁止自动重跑 |
| provider timeout/响应丢失 | UNKNOWN | 查询 provider/本地回执；未对账不重试 |
| 取消时已有不可逆副作用 | FAILED + reconciliation_required | 补偿或人工对账，不伪造 CANCELED |

---

# 18. 验收用例

- **TEST-LONG-001 / EVT-HEARTBEAT-001**：Runtime 60 秒无 stdout 或无换行时仍每 5 秒 heartbeat，deadline/cancel 正常。
- **TEST-STREAM-001 / EVT-PROGRESS-001**：SSE keepalive 不被误判为进度；多订阅者顺序一致，慢订阅者不拖住任务。
- **TEST-RECOVERY-001**：SSE 断线后 GetTask/Subscribe 恢复；lease 过期后旧 owner 不能写终态。
- **TEST-PUSH-001**：Push 失败不阻塞 Runtime，重复 delivery 可去重。
- **TEST-RETRY-001**：unsafe task 崩溃不自动重跑，retry-safe attempt 使用新 fencing token。
- **TEST-OBSERVER-001**：Observer 不处理普通 heartbeat，不响应自身事件，干预次数/冷却生效。
- **TEST-A2A-001**：Progress 可由官方 SDK 解析，忽略扩展后仍是合法 TaskStatusUpdateEvent。
- **TEST-SEC-001**：Progress、stdout 摘要和审计不泄露思维链、secret 或未授权参数。
- **TEST-IDENTITY-001**：跨 Binding 同 Principal 可 Get/List/Cancel；不同 Principal 统一 no-leak；Credential 轮换不改历史 owner。
- **TEST-MCP-IDEMP-001**：MCP 超时重试得到同一 Task，关闭 MCP stream 不取消后台 Task。
- **TEST-OUTBOX-001**：State mutation 与 outbox 原子，Relay 崩溃重投不丢进度/终态且不重复改变 Task。
- **TEST-EFFECT-001**：所有 effect 状态转换、provider idempotency、UNKNOWN 对账和 compensation 审计通过。
- **TEST-CANCEL-001**：无副作用/补偿成功才 CANCELED；不可逆或 UNKNOWN effect 返回 FAILED + reconciliation_required。
- **TEST-DR-001**：服务重启、完整节点恢复和 15 分钟备份缺口门禁满足 RTO/RPO。
---

# 19. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.3](A2AMesh_业务与总体架构设计_V1.3.md)
- [AgentCard与协议对象规范 V1.3](A2AMesh_AgentCard与协议对象规范_V1.3.md)
- [A2A协议与NATS集成适配设计 V1.3](A2AMesh_A2A协议与NATS集成适配设计_V1.3.md)
- [Redis状态平面与数据设计 V1.3](A2AMesh_Redis状态平面与数据设计_V1.3.md)
- [编排器 Runtime与工具适配设计 V1.3](A2AMesh_编排器_Runtime与工具适配设计_V1.3.md)
- [接口请求与响应标准 V1.3](A2AMesh_接口请求与响应标准_V1.3.md)
- [统计审计与运行监控规则 V1.3](A2AMesh_统计审计与运行监控规则_V1.3.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
- [A2A v1.0.1 canonical Proto](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)
- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A Custom Protocol Bindings](https://a2a-protocol.org/latest/topics/custom-protocol-bindings/)
