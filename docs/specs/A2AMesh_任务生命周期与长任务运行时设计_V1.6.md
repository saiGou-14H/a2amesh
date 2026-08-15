# A2AMesh 任务生命周期与长任务运行时设计 V1.6
> 文档ID：`A2AM-RUN-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：Task 状态机、Supervisor、进度、订阅/Push 生命周期语义与恢复；HTTP/SSE/Push wire 以接口标准为准
> 目标读者：Runtime、Gateway、前端、测试、运维
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

本文档定义 A2AMesh Task 从受理、排队、执行、流式进度、工具运行、等待输入、取消、完成、失败到崩溃恢复的完整运行时，重点解决数分钟长任务中“仍在推理、正在执行工具、连接断开或进程崩溃无法区分”的问题。

Task 标准对象见《Agent Card 与协议对象规范》；Redis/lease 见《Redis 状态平面与数据设计》；SSE/Push 接口见《接口请求与响应标准》。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立异步 TaskSupervisor、四维状态、Progress、heartbeat、SSE/Push/GetTask、Observer 和恢复规则 |
| V1.1 | 2026-08-14 | 落地EVT/TEST标识并补齐长任务验收追踪 |
| V1.2 | 2026-08-14 | 补齐跨Binding Task所有权、取消、恢复和审计主体规则 |
| V1.3 | 2026-08-14 | 闭合状态提交、副作用回执、取消后对账、未知结果与灾难恢复语义 |
| V1.4 | 2026-08-14 | 明确 Artifact finalize 前置门禁、人工对账流程和终态 Task 不可改写规则 |
| V1.5 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，Task 与恢复合同不变 |
| V1.6 | 2026-08-14 | 闭合 G0：完整状态矩阵、持久 dispatch、状态驱动 cancel、retry 与 UNKNOWN 恢复 |

### 1.2 当前问题

当前 `runtime/executor.py` 主要依赖子进程 stdout `readline()` 触发 `on_stream`。Runtime 静默、工具阻塞或输出没有换行时，没有独立 heartbeat；任务最终状态主要由进程结束后产生。因此当前实现是“部分流式”，不是本文目标长任务运行时。

---

## 2. 设计决策

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

## 3. 状态模型

### 3.1 标准 TaskState

| From | To | 条件 |
|---|---|---|
| SUBMITTED | WORKING | dispatch ACCEPTED，owner lease 有效，未请求取消 |
| SUBMITTED | CANCELED | cancel 在线性化点先发生且 Runtime 从未启动 |
| SUBMITTED | FAILED | dispatch deadline/静态 Runtime 路由失败 |
| SUBMITTED | REJECTED | 仅目标在受理后发现业务输入不可处理；认证/授权/准入失败不创建 Task |
| WORKING | INPUT_REQUIRED / AUTH_REQUIRED | 标准交互等待 |
| INPUT_REQUIRED / AUTH_REQUIRED | WORKING | `append_task_message` 持久 input intent，owner `ack_input_and_resume` 原子确认，且 Task 未过期/取消 |
| WORKING / INPUT_REQUIRED / AUTH_REQUIRED | CANCELED | 进程停止且 effect 安全/补偿完成 |
| WORKING / INPUT_REQUIRED / AUTH_REQUIRED | COMPLETED | 结果、Artifact、effect 全部验证通过 |
| WORKING / INPUT_REQUIRED / AUTH_REQUIRED | FAILED / REJECTED | 执行、deadline、业务拒绝或 UNKNOWN 规则触发 |

`COMPLETED/FAILED/CANCELED/REJECTED` 均为终态且不可迁出；对账只增加 version、审计和结果引用，不改标准终态。

### 3.2 内部执行 phase

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

### 3.3 四维前端状态

| 维度 | 示例 | 权威来源 |
|---|---|---|
| Task 生命周期 | WORKING/FAILED | Redis Task |
| 执行 phase | tool_running | Progress 快照 |
| Agent 健康 | online/suspect/offline | Presence |
| 传输连接 | connected/reconnecting/disconnected | SSE/NATS 客户端 |

不能因 SSE 断开直接把 Task 标为 failed。

### 3.4 Task 所有权与 Canonical Principal

- Task 创建时把验证后的 `callerPrincipal`、`principalType`、`credentialId`（非 secret）和 `aliasGeneration` 固化到 Redis/审计；
- `callerPrincipal` 在 Task 生命周期内不可更改；Credential 轮换、禁用或 alias 配置变化不重写历史 Task；
- 外部 `GetTask/ListTasks/CancelTask` 只允许当前请求解析出的 Canonical Principal 与 Task caller 相同；不存在和不可访问统一 no-leak；
- target Agent/owner instance 可领取 lease、写执行事件和 Artifact，但不能因此读取该 caller 的其他 Task；
- `system:projector`、`system:push`、`system:observer` 只拥有固定组件操作，不等同业务 caller；
- MCP Task Resource 和 A2A JSON-RPC/gRPC 使用同一所有权判断，不按 Binding 建第二套访问规则；
- payload/metadata 中的 caller/owner 字段不参与判定。

---

## 4. TaskSupervisor

### 4.1 职责

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

### 4.2 结构

```python
class TaskSupervisor:
    async def accept_dispatch(self, dispatch: DispatchTask) -> DispatchAccepted:
        # State 从当前 SENT dispatch tuple 返回创建 Task 时冻结的 exact command；
        # payloadRef、当前 config 或 NATS payload 都不能替代该读取。
        command = await self.state.get_task_command(
            task_id=dispatch.task_id,
            dispatch_id=dispatch.dispatch_id,
            dispatch_attempt=dispatch.dispatch_attempt,
            claim_token=dispatch.claim_token,
            expected_payload_digest=dispatch.payload_digest,
        )
        recovery = dispatch.dispatch_mode == "RECOVERY_RESUME"
        lease = await self.state.acquire_lease(  # 返回 provisional lease
            operation="RECOVERY_PROVISIONAL" if recovery else "INITIAL",
            task_id=dispatch.task_id,
            target_agent_id=self.agent_id,
            supervisor_instance_id=self.instance_id,
            dispatch_id=dispatch.dispatch_id,
            dispatch_attempt=dispatch.dispatch_attempt,
            claim_token=dispatch.claim_token,
            command_digest=command.payload_digest,
            recovery_operation_id=dispatch.recovery_operation_id if recovery else None,
        )
        accepted = await self.state.accept_dispatch_and_start(
            operation="RECOVERY_RESUME" if recovery else "INITIAL_START",
            dispatch=dispatch,
            provisional_lease=lease,
            command_digest=command.payload_digest,
        )
        # INITIAL_START把SUBMITTED推进WORKING；RECOVERY_RESUME保持WORKING且只换owner/fence。
        # 两者在以下观察完成前都不得spawn Runtime、创建effect或调用provider。
        observation = await self.containment_launcher.observe_without_spawn(
            execution_lease=accepted.execution_lease,
            command=command,
        )
        attestation = self.sign_containment_attestation(
            execution_lease=accepted.execution_lease,
            command=command,
            launcher_observation=observation,
        )
        registered = await self.state.register_containment_attestation(
            task_id=dispatch.task_id,
            execution_attempt=accepted.execution_lease.execution_attempt,
            expected_version=accepted.task_version,
            owner_instance_id=self.instance_id,
            fencing_token=accepted.execution_lease.fencing_token,
            attestation_jws_json=attestation.exact_jws_json,
            attestation_jws_digest=attestation.exact_jws_digest,
        )
        # REGISTER响应丢失时用同attestation重试；随后必须从State逐字节读回，不能信本地对象。
        readback = await self.state.read_containment_attestation(
            task_id=dispatch.task_id,
            execution_attempt=accepted.execution_lease.execution_attempt,
            owner_instance_id=self.instance_id,
            fencing_token=accepted.execution_lease.fencing_token,
            expected_ref=registered.containment_attestation_ref,
            expected_digest=attestation.exact_jws_digest,
        )
        if (
            readback.containment_attestation_ref != registered.containment_attestation_ref
            or readback.attestation_jws_digest != attestation.exact_jws_digest
            or readback.attestation_jws_json != attestation.exact_jws_json
        ):
            raise FailClosed("CONTAINMENT_READBACK_MISMATCH")
        # 只有accept→REGISTER→exact read-back全链成功后才可创建任何执行协程/进程/effect。
        binding = ContainmentBinding.from_readback(readback)
        asyncio.create_task(self._supervise(accepted.execution_lease, command, binding))
        return accepted

    async def _supervise(
        self, lease: ExecutionLease, command: CanonicalCommand, containment: ContainmentBinding
    ) -> Task:
        runtime = asyncio.create_task(self._run_runtime(lease, command, containment))
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

### 4.3 不变量

1. heartbeat 不依赖 stdout。
2. 每个副作用前校验 lease/fencing。
3. lease 续租失败立即停止新副作用。
4. Runtime 退出后所有协程和 pipe 被回收。
5. 最终状态只写一次。
6. 旧 attempt 的事件不能覆盖新 attempt。
7. 未能确认进程终止时不得宣称 canceled。
8. Task 状态/进度/Artifact 必须先由 State mutation 提交，再由 outbox Relay 发布；Supervisor 不直接声明未提交事实。
9. 外部副作用必须先写 ledger，`UNKNOWN` 未对账前不得自动重试、补偿成功或取消成功。
10. 唯一启动授权链固定为`get_task_command → provisional lease → accept_dispatch_and_start(WORKING) → observe-without-spawn → sign attestation → REGISTER_CONTAINMENT → State exact read-back → _supervise/_run_runtime`。accept前失败必须撤销provisional lease；accept后但REGISTER/read-back前失败或响应不确定时保留零Runtime/零effect/零provider调用并让当前execution lease受控失败或过期恢复，绝不能绕过到`_supervise`。

---

## 5. RuntimeEvent

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

## 6. EVT-HEARTBEAT-001：Heartbeat 与 Lease

| 信号 | 默认 | 用途 |
|---|---:|---|
| SSE comment keepalive | 10 秒 | 浏览器→Gateway 连接 |
| Agent presence | 5 秒 | Peer 进程健康 |
| Task heartbeat | 5 秒 | Supervisor 健康 |
| Task lease | TTL 30 秒，10 秒续租 | 执行所有权/fencing |
| stalled 阈值 | 15 秒无 task heartbeat 或 phase 超策略 | UI/Observer 提示 |
| agent suspect/offline | 15/30 秒 | 调度与恢复 |
| hard timeout | 每任务配置 | 强制停止上限 |

所有周期可配置并加 jitter。每 5 秒 heartbeat 更新 Redis `lastHeartbeatMs/freshnessVersion` 并读取 `cancelRequested`；纯 heartbeat 不改变 Task version、官方 status timestamp、eventSequence 或 ETag。只有 phase/progress 变化、状态变化或最长 30 秒采样点才作为普通 Task mutation 写外部事件 outbox；普通 heartbeat 不进入 Push。

---

## 7. 受理与执行

### 7.1 推荐客户端模式

```text
SendMessage(returnImmediately=true 或缺省)
→ claim commit 后立即得到 SUBMITTED Task
→ SubscribeToTask
→ 断线时 GetTask
→ 未终态则重新 SubscribeToTask
```

不要让一个 HTTP 请求阻塞数分钟后才返回 Task ID。

### 7.2 执行步骤

1. Gateway/Core 验证凭据并完成协议对象、请求大小的静态校验。
2. State `claim_message` 原子解析 Canonical Principal、复核 capability/admission、生成/复用 Task，并写 SUBMITTED 快照、事件 outbox 和 durable dispatch intent。
3. Admission Scheduler 用持久 DRR 原子 `QUEUED→SELECTED` 并令 dispatch due；Dispatch Worker 认领后向独立 dispatch subject 投递 immutable `DispatchTask`。Worker 崩溃或 timeout 由 claim lease 接管，不依赖客户端重试。
4. Task Supervisor直接消费本Agent的DispatchTask，以dispatch token调用`task.command.get`取得并校验immutable command，再领取provisional lease并调用`accept_dispatch_and_start`；该State原子同时写dispatch ACCEPTED、Task WORKING、admission RUNNING和outbox，不存在ACCEPTED/SUBMITTED中间状态。
5. 受信Runtime launcher构造并签名ContainmentAttestation，调用`REGISTER_CONTAINMENT`并读回不可变ref；仅登记和durable audit成功后启动 Runtime，收集结构化事件、stdout/stderr 和独立 heartbeat。
6. inline Artifact 可直接提交；大型 Artifact 必须先按对象存储合同完成 upload/finalize，只有 `AVAILABLE` 元数据可进入 Task。
7. 外部副作用在执行前写 `PREPARED/APPLYING` ledger，完成后写 `APPLIED/FAILED/UNKNOWN`。
8. 每次可见进度、已完成 Artifact 元数据和状态变化先提交 Redis，再由 Relay 发布对应 eventSequence。
9. finalizing 校验 lease、退出码、所有 effect 状态和 Artifact 可用性。
10. 原子写 terminal Task、Artifact 元数据和终态 outbox。
11. 终态提交后即可释放 lease 和清理本地资源；Relay 独立发布终态并获 PubAck，SSE/Push 延迟不反向阻塞权威终态。

---

## 8. EVT-PROGRESS-001：Progress 事件

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

## 9. SSE

### 9.1 响应

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

### 9.2 断线

- 浏览器 no-byte 超过 25 秒进入 reconnecting；
- 退避 1s、2s、5s、10s，最大 30s；
- 重连前 GetTask；
- Task 未终态再 Subscribe；
- 标准 A2A 不承诺补齐所有瞬时 status message；重要事实必须进入 Task/Artifact。

订阅建立采用固定竞态消除算法：Gateway 先创建按 taskId 过滤的 live consumer 并缓冲，再读取 Redis Task snapshot/eventSeq、发送首帧，然后丢弃缓冲/后续消息中 `eventSeq<=snapshot.eventSeq` 的重复并顺序发送更大值。per-Task eventSeq 只位于消息 payload，**不得**作为 JetStream 全局 stream start sequence。若 live consumer/retention 无法建立，关闭订阅并要求客户端重新 GetTask。V1.6 不定义私有 replay cursor。

### 9.3 慢客户端

每订阅者设置缓冲上限。超过上限时关闭该订阅并返回可重连提示，不能阻塞 JetStream consumer 或 Runtime。

---

## 10. A2A Push

### 10.1 定位

Push 是离线/跨系统 HTTP Webhook，不是内部总线。payload 为单个标准 `StreamResponse`。

### 10.2 Dispatcher

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

### 10.3 安全

- 生产仅 HTTPS；
- 拒绝 loopback、RFC1918、link-local、metadata IP；
- 每次发送及重定向重新解析；
- 限制响应大小和跳转次数；
- credential 加密；
- payload 签名；
- 至少一次投递，客户端按 `taskId + eventSequence + deliveryId` 去重。

---

## 11. GetTask 轮询

GetTask 是权威校准和兜底，不是主实时通道。

- SSE 正常：不轮询，或 30～60 秒低频校准；
- SSE/Push 不可用：2s、5s、10s、30s 退避；
- 终态停止；
- 轮询读取 Redis，不直接请求运行 Peer；
- 可结合 task version/ETag 避免重复大响应。

---

## 12. Cancel 与 Timeout

### 12.1 Cancel

```text
CancelTask
→ State request_cancel CAS
→ 若 SUBMITTED、dispatch 未 ACCEPTED 且无 effect：同一 CAS 撤销 provisional lease/fence，直接 CANCELED、dispatch ABORTED、释放 reservation
→ control subject 通知 owner instance（仅加速）
→ Supervisor heartbeat/新 owner acquire 始终读取 Redis cancelRequested
→ Supervisor phase=canceling
→ terminate/kill process tree
→ 确认退出
→ 检查 side-effect ledger
→ 无不可逆 effect 或补偿成功：TASK_STATE_CANCELED
→ 存在 APPLIED/UNKNOWN 且未完成对账：TASK_STATE_FAILED + reconciliation_required + reconciliation case
```

Linux：新process group+TERM grace+KILL。Windows：CREATE_NEW_PROCESS_GROUP/Job Object+grace+terminate tree。进程退出只证明本地执行已停止，不证明远端副作用未发生。`CANCELED`重复Cancel返回当前Task；`COMPLETED/FAILED/REJECTED`固定`TaskNotCancelableError`。pre-accept的SUBMITTED cancel与`accept_dispatch_and_start`使用同一Task/dispatch/admission CAS；即使Task Supervisor已取得provisional lease，Cancel先提交也会推进fence、撤销lease并得到CANCELED，accept先提交则得到WORKING并走Supervisor cancel。不存在永久SUBMITTED或随后被dispatch deadline改写为FAILED的第三种结果。

### 12.2 Timeout

- request deadline：claim 前过期，不创建 Task；
- queue deadline：从 claim commit 起，过期且未选中时原子 CANCELED/ABORTED 并释放 queued；
- dispatch deadline：从 DRR SELECTED 起，过期且无 owner 时 FAILED；
- soft execution deadline：从 WORKING commit 起发布提示，允许 Runtime 收尾；
- hard execution deadline：从 WORKING commit 起执行取消；等待 input/auth 默认不暂停；
- kill 失败：Task FAILED，错误为 runtime termination failure；
- timeout 是否重试由副作用策略决定。

---

## 13. 重试与副作用安全

### 13.1 重试分类

| 任务类型 | 自动重试 | 条件 |
|---|---|---|
| 只读分析 | 是 | 同 messageId、输入不变 |
| 测试/编译 | 通常是 | workspace 状态受控 |
| 生成未发布草稿 | 是 | 输出路径 attempt 隔离 |
| 文件修改 | 默认否 | 除非事务/快照/幂等补丁证明 |
| shell 任意命令 | 否 | 需人工确认 |
| 外部 API 写入 | 否 | 除非外部 idempotency key |
| 发布/部署/付款 | 否 | 必须人工或业务事务 |

非终态、retry-safe Task 的 lease takeover 可在同一 Task 内创建新 attempt。终态 Task 的人工重试必须提交新 messageId、创建新 Task，并通过 `retryOfTaskId/retryOfAttempt` 关联原事实；不得把 FAILED/CANCELED/REJECTED 改回 WORKING。

### 13.2 Side-effect ledger

每个 `WORKSPACE_WRITE`、`SYSTEM_WRITE` 或 `EXTERNAL_SIDE_EFFECT` 步骤在真正执行前创建 effect 记录：

```text
PREPARED → APPLYING → APPLIED
                    ↘ UNKNOWN
PREPARED/APPLYING/APPLIED → COMPENSATED
PREPARED/APPLYING → FAILED
```

- `effectIntentId` 由 State 以 `taskId+stepId+logicalEffectKey/requestHash` 唯一 CAS 分配，跨安全重试稳定并派生不可变 provider idempotency key；每次真实 provider 调用使用唯一 `effectAttemptId`；
- 执行顺序固定为`prepare intent → begin_effect_attempt(携带State read-back containment ref/digest并CAS写PREPARED) → 再校验Task/workspace lease、fencing、generation/revocation → start_effect(再次CAS同ref/digest并写APPLYING) → provider call`；任何Runtime/Adapter不得在REGISTER/READ前、begin/start失败后或先调用provider再补ledger；
- 只有前次被可信 evidence 证明 `FAILED_BEFORE_CALL/NOT_APPLIED` 且 retry policy 允许时才能创建下一 attempt；`UNKNOWN/APPLIED` 一律禁止自动新 attempt；
- 收到明确成功回执才写 `APPLIED`，明确未执行/业务拒绝才写 `FAILED`；
- timeout、连接断开、进程崩溃或响应丢失且无法证明未执行时写 `UNKNOWN`；
- `UNKNOWN` 不得按“可能失败”自动重放，必须查询 provider、本地不可变回执或人工对账；
- 补偿是新的受审计动作，成功后写 `COMPENSATED`，不能删除原 `APPLIED` 事实；
- Task 终态必须汇总 effect：存在未解决 `UNKNOWN` 或不可逆 `APPLIED` 且请求取消时，使用 `FAILED + reconciliation_required`，而不是 `CANCELED`。
- owner lease 失效且 APPLYING 超过 operation policy 时，Effect Reconciler 原子转 UNKNOWN 并创建唯一 case；不能无限停在 APPLYING。

### 13.3 Reconciliation 运维闭环

effect 进入 `UNKNOWN` 时，State 必须原子创建唯一 `OPEN` case。操作员通过独立机器 Credential 和 capability 认领 10 分钟 claim lease，追加 provider 查询、provider 幂等记录、本地不可变回执或补偿回执，再裁决 `APPLIED/FAILED/COMPENSATED`。自由文本不能单独解除 UNKNOWN。

resolution 与 effect ledger、case、Task `reconciliationRequired` 聚合、Task version/eventSequence、append-only audit 和 outbox 原子更新。已提交为 `TASK_STATE_FAILED` 的 Task 永远不改成 `COMPLETED/CANCELED`；只能追加脱敏 reconciliation result、Message 或 Artifact。完整 case/evidence/API/SLA 合同以《人工对账与运维操作设计》为准。

---

## 14. 崩溃恢复

### 14.1 Peer/Runtime 崩溃

1. task heartbeat 停止，execution lease到期候选已由accept/renew CAS维护在`task:recovery:due:<targetAgentId>`；
2. Agent presence suspect/offline；
3. 任一新Task Supervisor实例以自身NKey周期调用`a2a.v1.state.task.recover`，提交exact `TaskRecoveryScanRequestV1`；State按`(leaseUntilMs,taskId)`选择候选，Supervisor不保存旧进程内task列表也不能自选taskId；
4. State 查询side-effect ledger、Task/lease、本地恢复记录引用和provider状态；
5. 只有全部effect为无副作用、明确`FAILED_BEFORE_CALL/NOT_APPLIED`或具有可证明的provider幂等结果时，`claim_recovery_attempt`才以稳定recoveryOperationId创建唯一`dispatchMode=RECOVERY_RESUME`的新intent；该intent复用Task既有`admission=RUNNING/slotToken`，直接进入PENDING/due，不创建QUEUED/SELECTED reservation、不改变queued/reserved/running计数，也不得对旧ACCEPTED intent获取新lease；
6. 相同recoveryScanId重试返回原结果，新scanner即使使用新scan ID也由operation tuple去重；State提交后response丢失时已提交intent仍继续投递。新Supervisor仍严格执行`command.get → acquire_lease(RECOVERY_PROVISIONAL) → dispatch.accept(RECOVERY_RESUME) → containment REGISTER/READ`；recovery accept要求Task保持WORKING、旧lease已过期、admission仍RUNNING，并只原子替换owner/attempt/fence、写`TASK_OWNER_RECOVERED`，不产生第二个`TASK_WORKING`或第二份running计数；
7. owner已失效且存在`UNKNOWN`时Task固定进入`FAILED + reconciliation_required`，禁止自动重放；
8. 旧owner恢复也因fencing token失效不能写。

### 14.2 Gateway/SSE 崩溃

Task 继续。新 Gateway 从 Redis 取 Task，以 JetStream 水位建立 live stream。

### 14.3 Redis 暂不可用

Supervisor 不能把未提交状态直接发布为权威 JetStream 事件；不能安全续 lease/写 State 时应停止新副作用、进入受控等待，Core 停止新提交。Redis 恢复后按 ledger、进程状态和 provider 证据对账，无法确认的任务标记 reconciliation required。

### 14.4 灾难恢复目标

- State/Event 相关服务重启 RTO：15 分钟；
- 完整单节点恢复 RTO：4 小时；
- 受控进程/服务重启且 Redis/JetStream 持久卷完好时目标 State RPO：0；
- 整机、磁盘或电源故障时 State/Event RPO：不超过 15 分钟，异机备份频率必须匹配；
- 恢复后必须选择 `DATA-RECOVERY-001` manifest，对账 config generation、Redis committed eventSeq/outbox、JetStream watermark、Object inventory、audit checkpoint 和 effect ledger，再开放新副作用；不能仅凭进程健康就结束恢复。

---

## 15. Observer Agent

### 15.1 流程

```text
Task events
→ 规则过滤/窗口聚合
→ anomaly/milestone
→ Observer Agent 分析
→ 建议或受控 intervention
```

触发：heartbeat 过期、phase 超时、tool failed、retrying、lease expired、input/auth required、terminal failure。

### 15.2 权限与防环

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

## 16. 前端状态矩阵

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

## 17. 失败与补偿矩阵

| 失败点 | Task/内部状态 | 补偿 |
|---|---|---|
| NATS dispatch 失败 | SUBMITTED + durable intent | Dispatch Worker claim 过期接管；超过 deadline 后原子 FAILED |
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
| Artifact finalize/hash 校验失败 | WORKING 重试或 FAILED | 不提交不可下载 URI，不写成功终态 |
| reconciliation claim 过期 | Task 终态不变、case workflowState 保持 OPEN，escalated/priority 保留 | 新操作员以更高 fencing 认领，旧 token 拒绝 |

---

## 18. 验收用例

- **TEST-LONG-001 / EVT-HEARTBEAT-001**：Runtime 60 秒无 stdout 或无换行时仍每 5 秒 heartbeat，deadline/cancel 正常。
- **TEST-STREAM-001 / EVT-PROGRESS-001**：SSE keepalive 不被误判为进度；多订阅者顺序一致，慢订阅者不拖住任务。
- **TEST-RECOVERY-001**：SSE 断线后 GetTask/Subscribe 恢复。另在Task已WORKING后杀死当前Supervisor全部进程，等待`task:recovery:due:<agentId>`到期；新Supervisor只通过exact `TaskRecoveryScanRequestV1`获得State选择的taskId并创建唯一`RECOVERY_RESUME` operation/dispatch。分别在scan ledger、operation CAS、PENDING publish、RECOVERY_PROVISIONAL lease、RECOVERY_RESUME accept提交前后杀进程；相同recoveryScanId+requestDigest逐字节返回原结果，同key异body冲突，新scan id也不得为同一过期lease再次递增attempt或创建第二intent。断言Task始终WORKING、admission始终RUNNING、queued/reserved/running总量不变、不产生第二个`TASK_WORKING`；旧owner不能写，新owner严格通过`command.get → RECOVERY_PROVISIONAL → RECOVERY_RESUME → containment REGISTER/READ`后才启动Runtime。固定fixture `task-01/expiredFencingToken=7/fromAttempt=1`的canonical identity与recoveryOperationId必须等于Redis §6.19给定值。
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
- **TEST-ARTIFACT-ATOMIC-001**：大型结果 finalize 成功后才进入 Task，存储/State 任意故障点不产生悬空成功 Artifact。
- **TEST-RECON-RESOLVE-001**：case claim/evidence/resolution 与 effect/Task/audit/outbox 原子一致。
- **TEST-RECON-IMMUTABLE-001**：已失败 Task 在 APPLIED/FAILED/COMPENSATED resolution 或 reopen 后保持原标准终态。
- **TEST-DISPATCH-001**：claim commit 后杀 Core/Worker且客户端不重试，Task 仍执行或按 deadline 失败。
- **TEST-CANCEL-RACE-001**：丢 control、cancel vs complete、cancel vs lease takeover 的线性化结果稳定。
- **TEST-TASK-STATE-001**：SUBMITTED、INPUT_REQUIRED、AUTH_REQUIRED 全部合法/非法迁移通过表驱动测试。
- **TEST-EFFECT-STALE-001**：陈旧 APPLYING先写入持久stale-due；只有持有效scanner lease/fence的effect-reconciler可经`a2a.v1.state.effect.scan-stale`以scanOperationId认领。覆盖owner lease失效前后、scanner claim/UNKNOWN CAS前后、State/reply丢失、双scanner与重启；同ID同digest逐字节重放，异digest/错误Principal/错误staleAfter零写入，最终只产生一次UNKNOWN、一个case、一个告警outbox且不重复provider调用。
- **TEST-DR-MANIFEST-001**：跨存储水位不一致时恢复保持 fail closed。
---

## 19. G0 生命周期冻结合同

1. SUBMITTED/WORKING/WAITING/terminal 全迁移矩阵由 State expectedVersion CAS 线性化。
2. durable dispatch intent与Task Supervisor的State ACCEPTED提交关闭“Task已返回但未执行”窗口；失败在deadline后确定进入FAILED。
3. cancelRequested 是持久事实；CANCELED 重复 Cancel 返回当前 Task，其余终态返回 TaskNotCancelableError。
4. heartbeat 每 5 秒更新 snapshot/lease，状态变化立即发事件，纯心跳最多 30 秒采样，避免 outbox 洪峰。
5. SSE 固定 snapshot-first + watermark + live buffer；V1.6 不交付私有 replay cursor。
6. 终态人工重试创建新 Task；非终态接管只有 retry-safe 且 effect 可证明安全时才增加 attempt。
7. 陈旧 APPLYING、UNKNOWN、Cancel 与 terminal、lease takeover 均有唯一恢复/对账语义。

---

## 20. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [AgentCard与协议对象规范 V1.6](A2AMesh_AgentCard与协议对象规范_V1.6.md)
- [A2A协议与NATS集成适配设计 V1.6](A2AMesh_A2A协议与NATS集成适配设计_V1.6.md)
- [Redis状态平面与数据设计 V1.6](A2AMesh_Redis状态平面与数据设计_V1.6.md)
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
