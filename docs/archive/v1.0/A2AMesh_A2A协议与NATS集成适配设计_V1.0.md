# A2AMesh A2A 协议与 NATS 集成适配设计 V1.0

---

# 1. 文档目的

本文档定义 A2AMesh 外部标准 A2A v1 Gateway 与内部 A2A-over-NATS 自定义 Binding 的职责边界、Subject、Envelope、操作映射、流式投递、发现、幂等、错误、连接恢复和部署规则。

A2A 规范对象以《Agent Card 与协议对象规范》为准；Redis 状态以《Redis 状态平面与数据设计》为准。NATS 解决 NAT 可达性和实时消息，不替代标准 HTTPS Binding，也不充当权威 Task 数据库。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立标准 Gateway、NATS v1 Binding、Subject、Envelope、11 操作映射和一致性门禁 |

---

# 2. 集成原则

1. 外部接口与内部 Binding 使用同一 Application Core 和官方对象。
2. NATS Binding URI 固定为：

```text
https://a2amesh.dev/bindings/nats/v1
```

3. NATS Binding 是项目自定义协议，不属于标准 `JSONRPC`、`GRPC` 或 `HTTP+JSON`。
4. Windows/Linux Peer 只主动连接 NATS；不要求 NAT 打洞或反向端口。
5. Request/Reply 使用私有 caller inbox；不能把输出发布到可预测公共 subject。
6. JetStream 保存有序 Task 事件；Core NATS request/reply 用于低延迟命令。
7. Redis 是 Task/Card 快照权威来源；NATS 消息不是数据库事实。
8. V1 单 Mesh，不在 subject/payload 引入 tenant；以配置 `mesh_id`、独立 NATS account 和凭据隔离部署。
9. 每个 advertised interface 必须通过同一语义测试。

---

# 3. 组件关系

```text
Official A2A Client
  │ HTTPS JSON-RPC / SSE
  ▼
Public Gateway
  │ official request/response/event
  ▼
Application Core
  ├── StateClient ─NATS RPC─▶ State Service ─▶ Redis
  └── NatsBindingClient ─────▶ NATS Server
                                ├▶ target Peer queue group
                                ├▶ private caller inbox
                                └▶ JetStream task events
```

| 组件 | 做什么 | 不做什么 |
|---|---|---|
| Gateway | 标准序列化、A2A-Version、SSE | 不执行 Runtime |
| Core | 业务语义、状态机、路由、错误 | 不依赖 HTTP/NATS 细节 |
| NATS Binding | canonical object 与 subject/envelope 映射 | 不维护 Task 权威状态 |
| State Service | 幂等、lease、Task/Card 索引 | 不转发 stdout |
| Peer | Runtime 执行和事件产生 | 不直接访问 Redis |

---

# 4. 连接与身份

## 4.1 NATS 连接

生产连接要求：

- TLS 或 WSS；
- 每个 Peer/服务独立 NKey；
- 禁止共享 seed；
- seed 从 OS Secret Store/受保护文件加载；
- 自动重连、指数退避和 jitter；
- 连接名包含 `mesh_id/agent_id/instance_id`；
- 服务端启用 JetStream 持久目录与容量限制。

## 4.2 身份映射

NATS 服务端认证身份映射为：

```text
principal = nkey_public_key or configured account user
agent_id  = server-side credential mapping
instance_id = peer-generated UUID per process start
```

payload 中的 `callerAgentId` 仅用于诊断，State Service/Core 必须使用认证连接身份，不能相信调用方自报 ID。

## 4.3 ACL

示例最小权限：

| 身份 | Publish | Subscribe |
|---|---|---|
| Peer `agent-a` | 自身 RPC reply、events、state client request | `a2a.v1.rpc.agent-a`、自身 control、私有 inbox prefix |
| Gateway | 任意目标 RPC、state query、push control | Gateway 私有 inbox、task event consumer |
| State Service | state replies、card/task change event | `a2a.v1.state.>` |
| Observer | 无或受控 intervention request | 指定 task event durable consumer |

真实 ACL 必须在启用 JetStream 后做端到端验证；Core NATS ACL 成功不代表 Stream/KV 权限正确。

---

# 5. Subject 规范

| Subject | 类型 | 说明 |
|---|---|---|
| `a2a.v1.rpc.<agentId>` | Core request/reply | 目标 Agent 的 11 操作入口 |
| `a2a.v1.control.<agentId>.<instanceId>` | Core pub/sub | Cancel、shutdown、lease-lost 等实例控制 |
| `_INBOX.a2amesh.<callerId>.<random>` | Core reply | 调用方私有单次/流式回复 |
| `a2a.v1.events.<taskId>` | JetStream | 标准 Task 事件和内部序列元数据 |
| `a2a.v1.state.<operation>` | Core request/reply | Peer/Gateway 调 State Service |
| `a2a.v1.cards.changed` | JetStream/Core event | Card 变更提示，Card 内容仍从 State 查询 |
| `a2a.v1.presence.heartbeat` | Core event | Peer heartbeat 输入 |
| `a2a.v1.observer.intervention` | Core request | 经策略授权的观察者建议/操作请求 |

规则：

- `agentId`、`taskId` 只允许安全字符并在构造 subject 前校验；
- 不把 workdir、runtime 参数、用户文本放 subject；
- reply subject 必须使用不可预测随机 token；
- 目标 Peer 使用 queue group `a2a-worker-<agentId>`，同一请求只交给一个实例；
- Task 事件不允许任意 Peer 广播订阅，按 account/consumer 权限限制。

---

# 6. Binding Envelope

## 6.1 请求

```json
{
  "bindingVersion": "https://a2amesh.dev/bindings/nats/v1",
  "protocolVersion": "1.0",
  "operation": "SendMessage",
  "requestId": "req-01H...",
  "callerAgentId": "linux-gateway",
  "targetAgentId": "windows-a",
  "sentAt": "2026-08-14T03:00:00Z",
  "deadlineAt": "2026-08-14T03:30:00Z",
  "replySubject": "_INBOX.a2amesh.linux-gateway.<random>",
  "payload": {
    "message": {
      "messageId": "msg-01H...",
      "role": "ROLE_USER",
      "parts": [{"text": "Run the repository tests."}]
    },
    "configuration": {"returnImmediately": true}
  }
}
```

`payload` 是对应官方 Request 的 ProtoJSON，不再嵌套旧版 JSON-RPC。

## 6.2 一次性响应

```json
{
  "bindingVersion": "https://a2amesh.dev/bindings/nats/v1",
  "protocolVersion": "1.0",
  "requestId": "req-01H...",
  "sequence": 1,
  "final": true,
  "payload": {
    "task": {
      "id": "task-01H...",
      "contextId": "ctx-01H...",
      "status": {
        "state": "TASK_STATE_WORKING",
        "timestamp": "2026-08-14T03:00:00Z"
      }
    }
  }
}
```

## 6.3 错误响应

```json
{
  "bindingVersion": "https://a2amesh.dev/bindings/nats/v1",
  "protocolVersion": "1.0",
  "requestId": "req-01H...",
  "sequence": 1,
  "final": true,
  "error": {
    "type": "TaskNotFound",
    "message": "Task was not found or is not accessible.",
    "retryable": false
  }
}
```

外部诊断必须脱敏；内部 Trace 通过 `requestId/taskId/traceId` 关联。

## 6.4 流式帧

每帧必须包含递增 `sequence`、同一 `requestId` 和单个 canonical `StreamResponse`。终态帧 `final=true`，之后禁止再发布。

---

# 7. 核心操作映射

| API ID | A2A v1 操作 | NATS operation | State/Peer |
|---|---|---|---|
| API-A2A-001 | SendMessage | `SendMessage` | Core claim + Peer execute |
| API-A2A-002 | SendStreamingMessage | `SendStreamingMessage` | Peer stream + Gateway SSE |
| API-A2A-003 | GetTask | `GetTask` | Redis 快照，不必访问 Peer |
| API-A2A-004 | ListTasks | `ListTasks` | Redis 索引/游标 |
| API-A2A-005 | CancelTask | `CancelTask` | Redis CAS + owner control |
| API-A2A-006 | SubscribeToTask | `SubscribeToTask` | Redis 首帧 + JetStream live |
| API-A2A-007 | CreateTaskPushNotificationConfig | 同名 | State + Push Dispatcher |
| API-A2A-008 | GetTaskPushNotificationConfig | 同名 | State |
| API-A2A-009 | ListTaskPushNotificationConfigs | 同名 | State |
| API-A2A-010 | DeleteTaskPushNotificationConfig | 同名 | State，幂等 |
| API-A2A-011 | GetExtendedAgentCard | 同名 | State/Card Service |

禁止混用 v0.3 `message/send`、`message/stream`、`tasks/get`、`tasks/resubscribe` 作为 v1 wire method。迁移期旧入口只能放在明确标记的 compatibility adapter 中，默认关闭。

---

# 8. SendMessage 流程

```mermaid
sequenceDiagram
    participant C as Caller
    participant G as Gateway/Core
    participant S as State Service
    participant N as NATS
    participant P as Target Peer

    C->>G: SendMessage(messageId, returnImmediately=true)
    G->>S: claim_message(caller,target,messageId,payloadHash)
    S-->>G: taskId + dedupeResult
    alt duplicate same payload
      G-->>C: existing Task
    else new task
      G->>N: request a2a.v1.rpc.target
      N->>P: queue-group delivery
      P->>S: acquire_lease(taskId,instance)
      P-->>N: accepted Task
      G-->>C: working Task
      P->>N: ordered progress/artifact events
      N->>S: projector updates snapshot
    end
```

Core 在 dispatch timeout 后只允许使用同一 messageId 重试；Peer/State dedupe 必须保证不会启动第二个副作用执行。

---

# 9. Streaming 与订阅

## 9.1 SendStreamingMessage

1. Core 先 claim Task。
2. 首帧必须为 Task 或规范允许的 Message。
3. Peer 事件转 canonical `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent`。
4. Gateway 转 SSE，保持顺序。
5. 终态关闭。

## 9.2 SubscribeToTask

1. State 鉴权/归属校验并读取 Task 与内部 `eventSeq` 水位。
2. Gateway 建立 live consumer，发送当前 Task 首帧。
3. 服务端用水位消除“读快照到开始 live”之间的竞态。
4. 标准请求没有客户端 replay cursor；断线后客户端执行 `GetTask + SubscribeToTask`。
5. A2AMesh Progress Extension 可声明 `lastEventSequence` 进行私有补发，但不得称为标准能力。

## 9.3 多订阅者

- 每个订阅独立 consumer/私有 inbox；
- 关闭一个订阅不影响 Task；
- 不同订阅者收到相同生成顺序；
- 慢订阅者按队列上限断开并要求 GetTask 重连，不能拖住任务执行。

---

# 10. Agent Card 注册与发现

Peer 启动后调用 State Service：

```text
upsert_card(agentId, instanceId, generation, cardProtoJson, etag)
```

State Service：

- 使用认证连接映射 agentId；
- 官方 SDK 解析 Card；
- 校验自定义 NATS interface URL/Binding；
- 原子替换 skill/interface 索引；
- 发布 `cards.changed` 轻量事件；
- presence 单独更新。

NATS `$SRV.PING` 可用于服务运行诊断，但不能作为唯一 Agent Card registry；普通 `nc.request($SRV.PING)` 只得到一个 responder，枚举时必须 inbox + bounded collection。

---

# 11. 投递语义与幂等

| 场景 | 保证 |
|---|---|
| Core request/reply | 至多一次收到响应不等于至多一次执行；依赖 State dedupe |
| JetStream event | 至少一次消费；consumer 按 taskId/eventSequence 去重 |
| Push webhook | 至少一次；按 deliveryId/eventSequence 去重 |
| Cancel | 幂等；重复 cancel 返回当前 Task |
| Card upsert | generation 幂等，旧 generation 拒绝 |
| State transition | task version + fencing token CAS |

任意自动重试必须同时满足：稳定 idempotency key、服务端 dedupe、payload hash 一致、任务策略允许重试。

---

# 12. 错误映射

| 内部情况 | A2A 错误 | 是否重试 |
|---|---|---|
| 版本不是 1.0 | VersionNotSupported | 否，协商后重试 |
| target 不存在/offline | TemporarilyUnavailable 或 Task/Agent not found 语义 | 视情况 |
| Message 非法 | InvalidArgument | 否 |
| Task 不存在 | TaskNotFound | 否 |
| 终态取消 | TaskNotCancelable | 否 |
| 未声明 Streaming | UnsupportedOperation | 否 |
| Push 未启用 | PushNotificationNotSupported | 否 |
| NATS 超时 | TemporarilyUnavailable | 同 messageId 可重试 |
| Runtime 启动失败 | Task → FAILED | 按策略 |
| Redis 不可用 | TemporarilyUnavailable，停止新提交 | 恢复后 |

错误响应不包含 subject、内部 IP、argv、栈、凭据和绝对路径。

---

# 13. 重连与故障

## 13.1 Peer 断线

- NATS 客户端自动重连；
- presence 进入 suspect/offline；
- 已运行任务 heartbeat/lease 过期；
- 只有 retry-safe 任务允许新实例接管；
- 旧实例恢复后因 fencing token 失效不能写状态。

## 13.2 NATS 重启

- JetStream 使用持久目录；
- Peer/Gateway 重连并重新订阅；
- Redis 保留 Task 快照；
- 事件窗口内由 Projector 重放；
- 无法确认的副作用任务不自动重新 dispatch。

## 13.3 Gateway 重启

Task 独立于 Gateway 连接继续执行。客户端重新 GetTask/Subscribe。Push Dispatcher 使用 durable consumer 恢复。

---

# 14. 配置基线

```yaml
mesh:
  id: default
nats:
  servers: ["tls://mesh.example.com:4222"]
  connect_timeout_seconds: 5
  reconnect_wait_seconds: 2
  max_reconnect_attempts: -1
  inbox_prefix: "_INBOX.a2amesh"
  jetstream:
    stream: "A2A_TASK_EVENTS"
    subjects: ["a2a.v1.events.*"]
    max_age_hours: 24
binding:
  uri: "https://a2amesh.dev/bindings/nats/v1"
  protocol_version: "1.0"
```

凭据不写 YAML，使用环境变量指向 secret 文件或 OS Secret Store。

---

# 15. 验收用例

1. Linux 与两台 Windows 均仅主动连接 NATS，任意方向 RPC 成功。
2. 每个操作的 NATS payload 可由官方对象解析。
3. 私有 inbox ACL 阻止其他 Peer 订阅输出。
4. queue group 双实例只启动一次执行。
5. timeout 重试同 messageId 不重复执行。
6. 多订阅者事件顺序一致，终态后无多余帧。
7. 标准订阅不依赖 replay cursor；增强补发仅在扩展声明后启用。
8. Card upsert 旧 generation 不能覆盖新实例。
9. NATS/Peer/Gateway 分别重启后 Task 快照与终态一致。
10. 所有 advertised Binding 通过同一操作语义套件。
