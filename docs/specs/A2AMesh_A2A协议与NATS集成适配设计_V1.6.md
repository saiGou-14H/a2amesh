# A2AMesh A2A 协议与 NATS 集成适配设计 V1.6
> 文档ID：`A2AM-BIND-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：私有 NATS Binding 的 Subject、ACL、Envelope、Dispatch/Event wire 与投递语义；公共 API 语义以接口标准为准
> 目标读者：架构、Gateway、NATS、后端、测试、运维
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

本文档定义内部 A2AMesh custom NATS Binding 的职责边界、Subject、ACL、Envelope、操作映射、流式 wire、发现、投递、连接恢复和部署规则。公共 JSON-RPC/gRPC/SSE 请求响应与错误语义以《接口请求与响应标准》为权威，本文只给出到 NATS 的传输映射。

A2A 规范对象以《Agent Card 与协议对象规范》为准；Redis 状态以《Redis 状态平面与数据设计》为准。NATS 解决 NAT 可达性和实时消息，不替代标准 HTTPS Binding，也不充当权威 Task 数据库。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立标准 Gateway、NATS v1 Binding、Subject、Envelope、11 操作映射和一致性门禁 |
| V1.1 | 2026-08-14 | 补齐对称旁路、State Agent/Card RPC、JSON-RPC/gRPC语义复用、官方错误和发现闭环 |
| V1.2 | 2026-08-14 | 补齐可信AuthContext、Canonical Principal传播与跨Binding幂等 |
| V1.3 | 2026-08-14 | 冻结State outbox到JetStream的投递协议、过载语义与内部版本兼容规则 |
| V1.4 | 2026-08-14 | 增加受信配置 generation 传播和 Artifact/对账事件的权威来源边界 |
| V1.5 | 2026-08-14 | 澄清 State dedupe 只保证单 Task 映射，外部副作用由 ledger、provider 幂等和对账控制 |
| V1.6 | 2026-08-14 | 闭合 G0：ACL、Binding minor、durable dispatch/cancel、Event Relay 顺序和错误矩阵 |

---

## 2. 集成原则

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
10. Public Gateway 只处理外部标准 A2A 南北向流量；Peer A→Peer B 的东西向调用直接发布到目标 RPC Subject，不经过 Gateway，也不存在固定主调度节点。
11. Task 事件只能由 Event Relay 从已提交 Redis outbox 发布；Runtime、Peer 和 Projector 不直接向 JetStream 声明权威状态。
12. 内部协议 major 不匹配直接拒绝；同一 major 的 minor 版本只保证 N/N-1 滚动兼容。

---

## 3. 组件关系

```text
Official A2A Client
  │ HTTPS JSON-RPC/SSE or HTTP/2 gRPC
  ▼
Public Gateway
  │ official request/response/event
  ▼
Application Core
  ├── StateClient ─NATS RPC─▶ State Service ─▶ Redis
  │                                          └─ outbox ─▶ Event Relay ─PubAck─▶ JetStream ─▶ event consumers
  └── NatsBindingClient ─────────────────────▶ NATS Server
                                               ├▶ target Peer queue group
                                               └▶ private caller inbox
```

| 组件 | 做什么 | 不做什么 |
|---|---|---|
| Gateway | JSON-RPC/gRPC 标准序列化、A2A-Version/metadata、SSE/stream | 不执行 Runtime |
| Core | 业务语义、状态机、路由、错误 | 不依赖 HTTP/NATS 细节 |
| NATS Binding | canonical object 与 subject/envelope 映射 | 不维护 Task 权威状态 |
| State Service | 幂等、lease、Task/Card 索引 | 不转发 stdout |
| Peer | Runtime 执行和事件产生 | 不直接访问 Redis |

---

## 4. 连接与身份

### 4.1 NATS 连接

生产连接要求：

- TLS 或 WSS；
- 每个 Peer/服务独立 NKey；
- 禁止共享 seed；
- seed 从 OS Secret Store/受保护文件加载；
- 自动重连、指数退避和 jitter；
- 连接名包含 `mesh_id/agent_id/instance_id`；
- 服务端启用 JetStream 持久目录与容量限制。

### 4.2 身份映射

NATS 服务端认证身份映射为：

```text
principal = nkey_public_key or configured account user
agent_id  = server-side credential mapping
instance_id = peer-generated UUID per process start
```

payload 中的 `callerAgentId` 仅用于诊断，State Service/Core 必须使用认证连接身份，不能相信调用方自报 ID。

所有内部请求还必须携带入口实际使用的 `configGeneration`。Core/Peer/State 只接受当前 active generation 或受控滚动升级窗口内明确兼容的 generation；未知、已撤销或内容 hash 冲突一律 fail closed。generation 只用于一致性校验，不能由业务 caller 自报来选择旧授权。

### 4.3 ACL

本节只冻结边界，唯一可生成矩阵见§16.6：Peer可publish**获授权目标的RPC literal subjects**，但只subscribe自身RPC/control/inbox与自身stream delivery prefix；独立Task Supervisor才以queue group subscribe本Agent dispatch。接收request的Peer/State/Supervisor只依赖有界`allow_responses`回复，绝不预授予任意`_INBOX.>` publish。Peer不得publish或subscribe dispatch subject，Dispatch Worker才可publish目标dispatch。Event Relay只能publish Task event和调用固定outbox State subjects，不能管理JetStream；Stream Session Controller负责鉴权/帧/ACK，独立JS Provisioner是唯一Stream/Consumer API身份。

Peer、Gateway、Projector 和 Runtime 均不得发布 `a2a.v1.events.>`。`replySubject` 必须位于认证 caller 的 inbox prefix，并同时进入 AuthProof 签名范围；任意公共 Subject、其他 Principal inbox 和宽 `_INBOX.>` 均拒绝。真实 ACL 必须在启用 JetStream 后做端到端验证；Core NATS ACL 成功不代表 Stream/Consumer 权限正确。

---

## 5. Subject 规范

| Subject | 类型 | 说明 |
|---|---|---|
| `a2a.v1.rpc.<agentId>` | Core request/reply | 目标 Agent 的 11 操作入口；不承载 durable worker dispatch |
| `a2a.v1.dispatch.<agentId>` | Core request/reply | 内部 `DispatchTask` 投递；不是 A2A/JSON-RPC 操作，不重新进入 `claim_message` |
| `a2a.v1.control.<agentId>.<instanceId>` | Core pub/sub | Cancel、shutdown、lease-lost 等实例控制 |
| `_INBOX.a2amesh.<callerId>.<random>` | Core reply | 调用方私有单次回复；每个 request 最多一个 response |
| `a2a.v1.events.<taskId>` | JetStream | 标准 Task 事件和内部序列元数据 |
| `a2a.v1.stream.open` | Core request/reply | 目标 Core/Gateway 请求建立 DATA-STREAM-SESSION-001；恰回一个 `StreamSessionOpened` |
| `a2a.v1.stream.ack` / `a2a.v1.stream.close` | Core request/reply | caller 对流帧确认/关闭；Controller 代发真实 JS ACK |
| `_DELIVER.a2amesh.stream.<callerScope>.<instanceId>.<streamOpenId>` | Core pub/sub | Controller 向唯一 caller 投递后续 canonical StreamResponse 帧；caller 在发请求前预订阅并 flush |
| `a2a.v1.js.consumer.create` / `a2a.v1.js.consumer.info` / `a2a.v1.js.consumer.delete` | Core request/reply | 仅 Stream Session Controller 调用 JS Provisioner执行确定性Create/Info/Delete；不是 `$JS.API.*` 透传 |
| `a2a.v1.state.card.upsert` | Core request/reply | 注册/更新认证 Peer 的 Card |
| `a2a.v1.state.card.get` | Core request/reply | 按 agentId 读取 Card/meta/presence |
| `a2a.v1.state.agent.list` | Core request/reply | 稳定排序和游标分页列出 Agent |
| `a2a.v1.state.agent.search` | Core request/reply | 按 skill/binding/runtime/online/capacity 查询 |
| `a2a.v1.state.presence.heartbeat` | Core request/reply | 更新认证 Peer instance presence |
| `a2a.v1.state.agent.unregister` | Core request/reply | generation/tombstone 幂等退役 |
| `a2a.v1.state.principal.resolve` | Core request/reply | 验证 Credential/alias，返回 Canonical Principal |
| `a2a.v1.state.push.config` | Core request/reply | Application Core/Gateway调用closed `CREATE\|GET\|LIST\|DELETE` Push config variant；State统一做owner、SSRF、credential与幂等校验 |
| `a2a.v1.state.effect.scan-stale` | Core request/reply | Reconciliation Service按持久 stale-due 索引认领超时 APPLYING 并原子转 UNKNOWN/case |
| `a2a.v1.state.plan.recovery.scan` | Core request/reply | Orchestrator按持久 Plan due 索引获取有界恢复候选；不接受调用方任意planId扫描 |
| `a2a.v1.state.stream.flush` | Core request/reply | Application Core在唯一opened response实际flush后提交持久确认，Controller关闭前必须读取 |
| `a2a.v1.state.stream-config.begin` | Core request/reply | Config Controller在有效rollout lease/maintenance gate内提交固定Task Event Stream desired config并取得持久operation |
| `a2a.v1.state.stream-config.claim` | Core request/reply | JS Provisioner以唯一NKey领取STREAM_CREATE/UPDATE/INFO operation ticket/fence |
| `a2a.v1.state.stream-config.complete` | Core request/reply | JS Provisioner提交exact signed broker result；State确认INFO与desired digest一致后完成 |
| `a2a.v1.state.dispatch.accept` | Core request/reply | Task Supervisor取得provisional lease后确认dispatch intent，并与Task WORKING原子提交 |
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

## 6. Binding Envelope

### 6.1 请求

```json
{
  "bindingUri": "https://a2amesh.dev/bindings/nats/v1",
  "bindingSchemaVersion": "1.1",
  "a2aProtocolVersion": "1.0",
  "operation": "SendMessage",
  "requestId": "req-01H...",
  "callerInstanceId": "gateway-01",
  "streamOpenId": null,
  "configGeneration": 42,
  "callerAgentId": "linux-gateway",
  "authContext": {
    "principalId": "a2a:cli-buildbot",
    "credentialId": "cli-buildbot",
    "method": "a2a-bearer",
    "issuer": "a2amesh-gateway",
    "subject": "cli-buildbot",
    "issuedAt": "2026-08-14T03:00:00Z",
    "expiresAt": "2026-08-14T03:05:00Z"
  },
  "authProof": {
    "signer": "gateway-service-nkey-public",
    "algorithm": "nkey-ed25519",
    "signature": "base64url-signature"
  },
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

`payload` 是对应官方 Request 的 ProtoJSON，不再嵌套旧版 JSON-RPC。`callerInstanceId` 必填并由连接 NKey/active presence 复核。`streamOpenId` 对两个流式操作必填、其余操作必须为 null；它是安全 ULID、进入 AuthProof/request digest，并在 response-lost transport retry 间保持不变，而 requestId/AuthProof 必须更新。流式 caller 必须先订阅并 flush State 模板推导的 `_DELIVER.a2amesh.stream.<callerScope>.<callerInstanceId>.<streamOpenId>`，再发送 RPC；不得提交自选 delivery subject。

#### 6.1.1 内部 `DispatchTask` Envelope

`DispatchTask`是State/Dispatch Worker/Task Supervisor之间的独立内部命令，**不属于11个A2A操作，不得发布到`a2a.v1.rpc.<agentId>`，也不得再次调用`claim_message`**。Peer Binding不订阅dispatch subject。其canonical ProtoJSON fixture固定包含：

```json
{
  "bindingUri": "https://a2amesh.dev/bindings/nats/v1",
  "bindingSchemaVersion": "1.1",
  "a2aProtocolVersion": "1.0",
  "operation": "DispatchTask",
  "requestId": "dispatch-req-01H...",
  "dispatchId": "dispatch-01H...",
  "dispatchAttempt": 2,
  "claimToken": "opaque-state-claim-token",
  "taskId": "task-01H...",
  "contextId": "ctx-01H...",
  "messageId": "msg-01H...",
  "requestHash": "sha256:...",
  "configGeneration": 42,
  "policySnapshotHash": "sha256:...",
  "targetAgentId": "windows-a",
  "deadlineAt": "2026-08-14T03:30:00Z",
  "payloadRef": "state://task/task-01H.../canonical-command",
  "payloadDigest": "sha256:...",
  "authProof": {
    "signer": "dispatch-service-nkey-public",
    "algorithm": "nkey-ed25519",
    "signature": "base64url-signature"
  }
}
```

`payloadRef`指向State已持久化的不可变canonical command。Task Supervisor收到DispatchTask后必须以自身NKey调用`a2a.v1.state.task.command.get`，State仅在dispatchId/attempt/claimToken仍为当前CLAIMED或SENT、targetAgentId与Supervisor身份匹配时返回exact command bytes/digest；Supervisor逐项校验`taskId/dispatchId/dispatchAttempt/claimToken/requestHash/payloadDigest/configGeneration/targetAgentId`，不得从当前请求重新拼装用户消息。Supervisor只返回内部`DispatchAccepted`/`DispatchRejected`，`DispatchAccepted`只有经State `accept_dispatch_and_start`原子提交后才生效。

### 6.2 一次性响应

```json
{
  "bindingUri": "https://a2amesh.dev/bindings/nats/v1",
  "bindingSchemaVersion": "1.1",
  "a2aProtocolVersion": "1.0",
  "requestId": "req-01H...",
  "configGeneration": 42,
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

### 6.3 错误响应

```json
{
  "bindingUri": "https://a2amesh.dev/bindings/nats/v1",
  "bindingSchemaVersion": "1.1",
  "a2aProtocolVersion": "1.0",
  "requestId": "req-01H...",
  "configGeneration": 42,
  "sequence": 1,
  "final": true,
  "error": {
    "type": "TaskNotFoundError",
    "message": "Task was not found or is not accessible.",
    "retryable": false
  }
}
```

外部诊断必须脱敏；内部 Trace 通过 `requestId/taskId/traceId` 关联。

### 6.4 流式帧

Core NATS responder对`SendStreamingMessage/SubscribeToTask`也受`allow_responses {max:1}`约束：它只在DATA-STREAM-SESSION-001 ACTIVE后向原reply inbox发送一个`StreamSessionOpenedV1`，字段固定为`schemaVersion,streamSessionId,streamOpenId,taskId,operation,callerDeliverySubject,snapshotEventSeq,expiresAt,initialFrame`。initialFrame是sequence=0的committed Task snapshot；整份response和initialFrame都必须使用State已持久化的canonical bytes，不得按当前Task或active generation重建，不得把delivery subject取自caller payload。成功写入并flush exact response bytes后，Core以`SHA-256(exact openedResponseJson bytes)`作为openedResponseDigest调用Redis §6.23的无controller-fence flush writer；不能从重建对象计算。

后续帧不再是 request reply，也不由目标 Peer 直发；Stream Session Controller 从 fixed filtered consumer 读取 committed event，经 `_DELIVER.a2amesh.stream.<callerScope>.<instanceId>.<streamOpenId>` 发布 `StreamSessionFrameV1`：`schemaVersion,streamSessionId,streamOpenId,sequence,eventSeq,final,canonicalStreamResponse,payloadDigest`。sequence 从 1 严格递增；eventSeq 必须大于 snapshotEventSeq/lastAckedEventSeq；payloadDigest=SHA-256(RFC8785(不含 payloadDigest 的 frame))。终态帧 `final=true`，之后只能幂等重投同一 digest，禁止新 sequence；caller ACK、broker ACK INFO确认、consumer delete+INFO-not-found 后才 CLOSED，超时清理则 EXPIRED。所有 TaskStatus/Artifact 事实只来自 State committed event outbox→Relay→JetStream；NATS 流路径禁止 Peer preview。caller 已在请求前订阅并缓冲该 subject，对每帧调用 `a2a.v1.stream.ack`，但没有 JS ACK 权限。

三个 session request payload wire 恰为：Open=`schemaVersion,streamOpenId,operation,taskId,callerInstanceId,requestDigest,expiresAt,configGeneration`；Ack=`schemaVersion,streamSessionId,streamOpenId,sequence,eventSeq,payloadDigest`；Close=`schemaVersion,streamSessionId,streamOpenId,reason`。它们都装入 §6.1 公共安全字段相同的独立 Stream Control Envelope 并签 AuthProof；外层 `operation` 与 subject 固定一一映射为 `StreamSessionOpen→a2a.v1.stream.open`、`StreamSessionAck→a2a.v1.stream.ack`、`StreamSessionClose→a2a.v1.stream.close`。这三个值属于内部 closed `StreamControlOperation`，不得加入官方 11 操作 `Operation/OPERATION_SPECS`；Open payload 内层 `operation` 仍只允许官方 `SendStreamingMessage|SubscribeToTask`。Controller/State 从认证连接重建 caller Principal/scope，payload 不得声明 Principal 或 Subject。Open response 恰为 `StreamSessionOpenedV1`，Ack/Close 各恰回一次 `accepted/currentState`；异 session/caller、旧 fence、非单调或 digest 不符统一拒绝且不发 JS ACK。

### 6.5 可信 AuthContext

`callerAgentId` 只用于诊断，业务身份以验证后的 `authContext.principalId` 为准。AuthContext 不是外部 A2A 对象，由可信入口生成：

| 入口 | 原始凭据 | Principal 初值 | 签名者 |
|---|---|---|---|
| Peer 东西向 | NATS NKey | `agent:<agentId>` | Peer 自身 NKey |
| JSON-RPC/gRPC | 独立 opaque Bearer/credentialId | `a2a:<credentialId>` | Gateway service NKey |
| MCP Bridge | OAuth issuer + client_id | `mcp:<issuerHash>:<clientId>` | MCP Gateway service NKey |
| 内部服务 | service NKey | `system:<component>` | 组件 NKey |

入口调用 State `principal.resolve` 应用显式 alias 后，使用 RFC 8785 规范化 Envelope（排除 `authProof.signature`），再用 NKey Ed25519 签名。接收端验证 signer 是否有权代表该 method/subject、签名、issuedAt/expiresAt、requestId/deadline 和目标 Subject。任何失败在 claim/dispatch 前拒绝。

业务 payload、Message metadata 或 MCP arguments 中的 `callerPrincipal/authContext/credentialId` 均不可信，不得覆盖。外部 Token 不随 Envelope 转发；仅传不可逆 credentialId/issuerHash 和已签名 Principal。

---

## 7. 核心操作映射

JSON-RPC 与 gRPC 都把下列 11 个操作交给同一 Core。gRPC 使用官方 `A2AService`：`SendStreamingMessage` 与 `SubscribeToTask` 为 server-streaming；其余为 unary。任何 Binding 特有适配不得改变 Task ID、状态迁移、错误、幂等键、排序或终态规则。

| API ID | A2A v1 操作 | NATS operation | State/Peer |
|---|---|---|---|
| API-A2A-001 | SendMessage | `SendMessage` | Core claim + Peer execute |
| API-A2A-002 | SendStreamingMessage | `SendStreamingMessage` | Core claim + committed-event subscription；不直转 Peer 权威帧 |
| API-A2A-003 | GetTask | `a2a.v1.state.task.get(operation=GET)` | Redis 快照，不必访问 Peer |
| API-A2A-004 | ListTasks | `a2a.v1.state.task.get(operation=LIST)` | Redis 索引/游标；callerPrincipal由Core注入 |
| API-A2A-005 | CancelTask | `CancelTask` | Redis CAS + owner control |
| API-A2A-006 | SubscribeToTask | `SubscribeToTask` | Redis 首帧 + JetStream live |
| API-A2A-007 | CreateTaskPushNotificationConfig | `a2a.v1.state.push.config(operation=CREATE)` | State + Push Dispatcher |
| API-A2A-008 | GetTaskPushNotificationConfig | `a2a.v1.state.push.config(operation=GET)` | State |
| API-A2A-009 | ListTaskPushNotificationConfigs | `a2a.v1.state.push.config(operation=LIST)` | State |
| API-A2A-010 | DeleteTaskPushNotificationConfig | `a2a.v1.state.push.config(operation=DELETE)` | State，幂等 |
| API-A2A-011 | GetExtendedAgentCard | 同名 | State/Card Service |

禁止混用 v0.3 `message/send`、`message/stream`、`tasks/get`、`tasks/resubscribe` 作为 v1 wire method。迁移期旧入口只能放在明确标记的 compatibility adapter 中，默认关闭。实现层唯一显式开关为 `compatibility.legacy_private_rpc_enabled=true`；字段缺失或 `false` 时不得订阅 `a2a.rpc.*`/`a2a.cards.*`，所有旧调用必须在任何 NATS I/O 前失败。该开关只允许严格布尔值，不得接受 truthy 字符串/数字，不得由 v1 超时、无 responder、Schema/AuthProof 错误触发自动降级；旧适配器也不得发布或订阅 `a2a.v1.*`。

---

## 8. SendMessage 流程

```mermaid
sequenceDiagram
    participant C as Caller
    participant G as Gateway/Core
    participant S as State Service
    participant D as Dispatch Worker
    participant N as NATS
    participant P as Task Supervisor
    participant R as Runtime

    C->>G: SendMessage(messageId, returnImmediately=true)
    G->>S: claim_message(caller,target,messageId,payloadHash)
    S-->>G: SUBMITTED taskId + dedupeResult + durable dispatch intent
    alt duplicate same payload
      G-->>C: existing Task
    else new task
      G-->>C: SUBMITTED Task（不等待 Worker）
      S->>S: Admission Scheduler lease + select_admission_for_dispatch CAS
      D->>S: claim_dispatch（仅已进入due的intent）
      D->>S: mark_dispatch_sent(dispatchId,attempt,claimToken)
      D->>N: DispatchTask → a2a.v1.dispatch.target
      N->>P: queue-group delivery
      P->>S: get_task_command(exact dispatch tuple)
      P->>S: acquire_lease(taskId,instance,dispatch tuple) [provisional]
      P->>S: accept_dispatch_and_start(taskId,dispatchId,claimToken,leaseToken)
      S-->>P: ACCEPTED + WORKING committed
      P->>P: observe_without_spawn(execution lease, command)
      P->>P: sign ContainmentAttestation exact bytes
      P->>S: REGISTER_CONTAINMENT(ref,digest,JWS)
      S-->>P: registration ref/digest committed
      P->>S: READ_CONTAINMENT(exact ref,digest)
      S-->>P: exact JWS/read-back
      alt all containment gates pass
        P-->>N: DispatchAccepted
        P->>R: start exact canonical command
        P->>S: transition_task(progress/artifact/terminal)
      else register/read-back failure or uncertain reply
        P->>S: fail/expire execution lease; zero Runtime/effect/provider
      end
      S-->>D: snapshot/index + event outbox committed
      Note over S,N: Event Relay later publishes taskId:eventSeq; Projector never writes Task authority
    end
```

`dedupeKey = Canonical Principal + targetAgentId + messageId`；`payloadHash` 存在 dedupe record 中用于冲突检测，不属于 Key。相同 Key/Hash 返回同一 Task，不同 Hash 固定冲突。Dispatch Worker 的 claim 过期后由其他实例接管；客户端是否重试不影响 intent 的持续投递。lease/fencing 只约束当前状态 owner，外部 effect 仍必须经过 SideEffect Adapter、provider idempotency key、持久 ledger 和 UNKNOWN 对账。

---

## 9. Streaming 与订阅

### 9.1 SendStreamingMessage

1. Core 先 claim Task；只有 live consumer 已建立并开始缓冲后，才读取 committed snapshot/eventSeq 并发送 SUBMITTED/当前快照首帧。
2. Gateway 使用 §9.2 的 consumer-first 顺序；NATS caller 使用 §9.4 Stream Session，二者不得用多次 request reply 模拟流。
3. 后续 `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` 只来自 State outbox 经 PubAck 的 committed event。
4. Gateway 按 Task `eventSeq` 去重并转 SSE；Peer preview 不能进入该权威序列。
5. 终态 committed event 后关闭。

### 9.2 SubscribeToTask

1. State 鉴权/归属校验；Gateway 先建立 `taskId` filtered live consumer 并缓冲新消息。
2. Gateway 再读取 Task snapshot 与内部 `eventSeq`，发送当前 Task 首帧。
3. 丢弃缓冲/后续消息中 `eventSeq <= snapshot.eventSeq` 的重复，再顺序发送更大序列；禁止把 per-Task `eventSeq` 当作 JetStream 全局 stream start sequence。
4. 标准请求没有客户端 replay cursor；断线后客户端执行 `GetTask + SubscribeToTask`。
5. V1.6 不交付私有 replay cursor；任何 `lastEventSequence` 请求字段均按未知可选扩展忽略或按 required-extension 规则拒绝。

### 9.3 多订阅者

- 每个订阅独立 consumer/私有 delivery subject；request reply inbox 只承载一次 open response；
- 关闭一个订阅不影响 Task；
- 不同订阅者收到相同生成顺序；
- 慢订阅者按队列上限断开并要求 GetTask 重连，不能拖住任务执行。

### 9.4 NATS Stream Session 唯一路径

1. caller/Gateway 生成或复用稳定 `streamOpenId`，先订阅并 flush 由 authenticated callerScope+instanceId+streamOpenId 推导的私有 delivery subject，再发流式 RPC；`SendStreamingMessage` 随后 claim Task，`SubscribeToTask` 完成 caller ownership，目标 Core 再向 `a2a.v1.stream.open` 请求 DATA-STREAM-SESSION-001。若 open 失败，caller 取消预订阅。
2. Controller先写OPENING，再让JS Provisioner创建固定stream `A2AMESH_TASK_EVENTS`上的durable consumer：`durable_name=consumerName`、`filter_subject=a2a.v1.events.<taskId>`、稳定`deliver_subject=_DELIVER.a2amesh.controller.<meshId>.<consumerName>`、`deliver_group`必须absent、`deliver_policy=new`、`ack_policy=explicit`、`replay_policy=instant`、`max_ack_pending=1`、`ack_wait=<signed value>`；`inactive_threshold`必须absent/0，禁止broker按订阅者inactive自动删除。Session expiresAt和显式CLOSING/EXPIRING cleanup是唯一生命周期边界。`meshId`来自signed config且必须是安全单token；Controller对该subject使用非queue subscription，所有存活实例可见同一delivery，只有State当前fence holder可处理/ACK。Provisioner必须INFO回读逐字段相等，并把broker absent/0规范化为语义值0；consumer先于snapshot建立并缓冲。
3. Controller从State session读取已绑定的configGeneration/consumerConfigJson/digest，再读取committed snapshot/eventSeq并以Task version/eventSeq/snapshot digest原子activate；State持久化initialFrameJson和openedResponseJson后，Core才逐字节返回该唯一response。对buffer中每条`eventSeq<=snapshotEventSeq`，Controller必须调用`claim_stream_frame`持久推进snapshot-covered broker ACK watermark并取得ACK permit，逐条JS ACK；不得仅内存丢弃，否则`max_ack_pending=1`会阻塞后续事件。
4. 对首个更大 eventSeq，Controller 调用同一 `claim_stream_frame` 持久化唯一 pending `streamSeq/sequence/eventSeq/payloadDigest` 后才投递；caller 只订阅自身 literal delivery prefix并调用 stream.ack/close。Controller 仅凭 State ACK permit publish对应 `$JS.ACK.A2AMESH_TASK_EVENTS.<consumer>.>`；未 ACK 时不取下一事件，slow consumer 按 policy 重投或进入 EXPIRING，不影响 Relay/State。
5. Controller不保存可恢复session清单。启动及周期扫描时仅向现有`a2a.v1.state.stream.reclaim`提交Redis §6.23 closed `SCAN` request，从持久`stream-session:due`取得State选择的candidate/token；再提交`RECLAIM`以CAS重验观察tuple并取得更高fence。接管后只使用State记录中的同configGeneration、consumerConfigJson/digest、consumerName、稳定controller delivery subject和持久watermark恢复；不得自选streamSessionId、扫描进程内列表、反查consumerName或读取当前active generation重建。旧实例即使仍收到同一delivery，也因fence拒绝且不得ACK/NAK，新owner不需迁移consumer deliver_subject。Provisioner create/info/delete均按`streamSessionId+consumerConfigDigest`幂等。
6. final live frame必须依次完成caller ACK→State ACK permit→实际JS ACK→Provisioner INFO确认ack floor/无pending→State broker-ack confirm→CLOSING→delete→INFO-not-found→CLOSED。若已存initialFrame本身终态，则`initialFrame.final=true`，不等待不存在的live frame/JS ACK；Core成功发布并flush已存openedResponseJson后，以exact response bytes digest和稳定flushOperationId调用`a2a.v1.state.stream.flush`。State按session已存responseCorePrincipalHash鉴权，不要求Core持有/猜测controllerFence；Controller只读取该持久确认后才可CLOSING→delete/not-found→CLOSED。同streamOpenId重试逐字节返回同一openedResponseJson和currentState，即使Task后续version/审计/结果引用变化也不得重建。caller close可直接CLOSING并放弃pending；deadline/slow consumer走EXPIRING→delete→INFO-not-found→EXPIRED，不得提前写CLOSED/EXPIRED。

每次Provisioner调用都必须携带State签发的`BrokerOperationTicketV1`，字段恰为`schemaVersion,streamSessionId,controllerFence,brokerOpEpoch,brokerOpKind,brokerChallenge,brokerOpRequestDigest,issuedAt,expiresAt,authProof`；**禁止别名`brokerRequestDigest`**。brokerChallenge是32随机字节的base64url无padding编码。`brokerOpRequestDigest=SHA-256(RFC8785(request payload排除brokerOpRequestDigest和brokerOperationTicket))`，ticket AuthProof覆盖除自身外全部字段。Provisioner必须验证State signer、时效、session当前fence/epoch/kind/challenge/digest及subject-kind映射；create只接受CREATE，delete只接受DELETE，info只接受CREATE_INFO/FINAL_ACK_INFO/CLEANUP_INFO。

`CreateStreamConsumerV1`请求固定为`schemaVersion,streamSessionId,controllerFence,configGeneration,streamName,consumerName,filterSubject,controllerDeliverySubject,deliverPolicy,ackPolicy,replayPolicy,maxAckPending,ackWaitMs,brokerOpEpoch,brokerOpKind,brokerChallenge,brokerOpRequestDigest,brokerOperationTicket,consumerConfigDigest`，其中全部配置字段逐项来自State已存consumerConfigJson，maxAckPending必须为1；未列出的deliverGroup和inactiveThresholdMs必须absent，出现任一字段即拒绝。`InfoStreamConsumerV1`请求固定为`schemaVersion,streamSessionId,controllerFence,configGeneration,streamName,consumerName,brokerOpEpoch,brokerOpKind,brokerChallenge,brokerOpRequestDigest,brokerOperationTicket,consumerConfigDigest`；`DeleteStreamConsumerV1`字段与Info相同但kind固定DELETE。Create/Delete响应统一为`BrokerConsumerMutationResultV1`，字段恰为`schemaVersion,streamSessionId,controllerFence,brokerOpEpoch,brokerOpKind,brokerChallenge,brokerOpRequestDigest,consumerName,consumerConfigDigest,succeeded,observedAt,provisionerInstanceId,responseDigest,authProof`。

Info响应固定为`schemaVersion,streamSessionId,controllerFence,configGeneration,brokerOpEpoch,brokerOpKind,brokerChallenge,brokerOpRequestDigest,consumerName,consumerConfigDigest,exists,numAckPending,ackFloorStreamSeq,deliveredStreamSeq,observedAt,provisionerInstanceId,responseDigest,authProof`。exists=true时三个计数/sequence均为非负整数且broker consumer config的inactive_threshold必须absent/0；exists=false时三者必须显式null，禁止省略或伪造0。Provisioner从请求重建RFC8785 consumer config并校验consumerConfigDigest；responseDigest是排除自身和authProof后的RFC8785 SHA-256，Provisioner AuthProof覆盖完整响应。

JS Provisioner只接受Stream Session Controller NKey和State ticket。执行Create/Info/Delete前，它必须以自身NKey调用`a2a.v1.state.stream.broker-op.claim`；State只返回`EXECUTE(attempt,lease)`、`BUSY`或`REPLAY_STORED(responseJson)`。只有EXECUTE可调用对应裸JS API；结果签名后必须先通过`a2a.v1.state.stream.broker-op.complete`持久化，才可回复Controller。REPLAY_STORED只能逐字节返回State结果，禁止再次调用broker。不匹配、过期ticket、错误subject-kind、任意其他stream、包含`*`/`>`的动态字段全部拒绝；是否已执行/完成由State持久账本判断而不是Provisioner内存。CREATE结果必须由Controller单次consume，之后才能签发CREATE_INFO；DELETE结果同理，之后才签CLEANUP_INFO。网络错误不等价于不存在。OPENING时经合法CREATE序列后的exists=false才可创建；ACTIVE/DRAINING_FINAL时当前challenge的exists=false是`CONSUMER_LOST`，Controller必须原子转EXPIRING并要求caller `GetTask+SubscribeToTask`，禁止以`deliver_policy=new`重建；CLOSING/EXPIRING时只有当前未消费CLEANUP_INFO的exists=false才是预期cleanup结果。旧的创建前not-found即使AuthProof仍在有效期，也因epoch/kind/challenge/requestDigest或已消费状态不符被拒绝。

---

### 9.5 固定 Task Event Stream 创建与升级合同

`A2AMESH_TASK_EVENTS`由JS Provisioner在Config rollout的生产维护窗口管理，不是隐含部署前置，也不允许任意Runtime/Controller直接CREATE/UPDATE。signed bundle的`TaskEventStreamConfigV1` payload字段恰为`schemaVersion,streamName,subjects,retentionPolicy,storageType,discardPolicy,maxAgeMs,maxMessages,maxBytes,maxMessageBytes,replicas,duplicateWindowMs,allowDirect,allowRollup,denyDelete,denyPurge`：`schemaVersion="1.0"`、`streamName="A2AMESH_TASK_EVENTS"`、`subjects=["a2a.v1.events.*"]`且数组不得额外/重排，policy固定`LIMITS/FILE/OLD`，`allowDirect=false,allowRollup=false,denyDelete=true,denyPurge=true`；其余整数必须来自bundle、为JSON安全整数并通过容量/副本门禁。`desiredConfigDigest=SHA-256(RFC8785(TaskEventStreamConfigV1))`；字段缺失、额外、别名、大小写变化、subject wildcard扩大或digest不等全部拒绝。

Config Controller调用`a2a.v1.state.stream-config.begin`的`StreamConfigBeginRequestV1`恰含`schemaVersion,streamOperationId,generation,rolloutLeaseId,rolloutFencingToken,expectedObservedConfigDigest,desiredConfigJson,desiredConfigDigest,requestDigest,authProof`；`requestDigest=SHA-256(RFC8785(request排除requestDigest/authProof))`。State要求generation已STAGED、production maintenance gate关闭流量、rollout lease/fence有效且desired config逐字节等于该bundle；首次CAS写持久operation ledger并进入`PENDING_INFO`。同operationId同digest逐字节返回已存状态/result，异digest冲突。

State按`INFO→(不存在时CREATE；存在且安全可更新字段漂移时UPDATE；完全相同时CONFIRMED)→fresh INFO→CONFIRMED`驱动唯一状态机。每个broker步骤签发`StreamConfigOperationTicketV1`，字段恰为`schemaVersion,streamOperationId,generation,rolloutLeaseId,rolloutFencingToken,streamName,streamOpEpoch,streamOpKind,challenge,desiredConfigDigest,brokerRequestDigest,issuedAt,expiresAt,authProof`；kind只允许`STREAM_INFO|STREAM_CREATE|STREAM_UPDATE`，challenge为32随机字节base64url无padding，epoch单调。JS Provisioner先经`stream-config.claim`取得`EXECUTE|BUSY|REPLAY_STORED`和execution fence/lease，只有EXECUTE可调用ticket对应的literal `$JS.API.STREAM.*.A2AMESH_TASK_EVENTS`；再经`stream-config.complete`提交exact signed response。response恰含`schemaVersion,streamOperationId,streamOpEpoch,streamOpKind,streamName,exists,observedConfigJson,observedConfigDigest,brokerResponseDigest,observedAt,provisionerInstanceId,authProof`；不存在时observed config/digest显式null，存在时必须从broker INFO重建完整`TaskEventStreamConfigV1`。

State只在fresh INFO的`observedConfigDigest=desiredConfigDigest`时写`CONFIRMED`并绑定generation/rollout lease/result digest；CREATE/UPDATE成功响应本身不能确认。storageType、streamName或无法无损更新的漂移固定`FAILED_CLOSED/STREAM_RECREATE_REQUIRED`，不得自动DELETE/PURGE/recreate；旧generation、旧fence、旧epoch、过期ticket、错误kind/API subject、INFO字段缺失、同ID异body全部零写入。ticket/claim前后、裸JS成功后/complete前、complete后/reply前崩溃只能接管/重放同一epoch；任何generation只有一个CONFIRMED desired digest，activate CAS必须读取它。`TEST-NATS-STREAM-SESSION-001`还须覆盖初次CREATE、无损UPDATE、already-matching、unsafe drift、双Provisioner、每个kill point及无ticket直接调用Stream API负例。

---

## 10. Agent Card 注册与发现

Peer 启动后调用 State Service：

```text
upsert_card(agentId, instanceId, cardGeneration, configGeneration, fencingToken, cardProtoJson, etag)
```

State Service：

- 使用认证连接映射 agentId；
- 官方 SDK 解析 Card；
- 私有 Registry 校验自定义 NATS route/Binding；公网标准 Card 不发布私有 NATS interface 或 NKey 细节；
- 原子替换 skill/interface 索引；
- 发布 `cards.changed` 轻量事件；
- presence 单独更新。

NATS `$SRV.PING` 可用于服务运行诊断，但不能作为唯一 Agent Card registry；普通 `nc.request($SRV.PING)` 只得到一个 responder，枚举时必须 inbox + bounded collection。

### 10.1 State Service Agent/Card RPC

以下均为 **A2AMesh 内部操作**，不是 A2A v1 核心操作：

| Subject | 请求关键字段 | 响应 | 幂等/排序 |
|---|---|---|---|
| `a2a.v1.state.card.upsert` | `instanceId,cardGeneration,configGeneration,fencingToken,cardProtoJson,etag`；agentId 取认证身份 | `cardMeta,created` | active config + 最新 fencing；旧 generation/token 拒绝 |
| `a2a.v1.state.card.get` | `agentId,includeOffline` | `card,cardMeta,presence` | 只读 |
| `a2a.v1.state.agent.list` | `pageSize,pageToken,onlineOnly` | `agents,nextPageToken` | `agentId ASC`，最大 200 |
| `a2a.v1.state.agent.search` | `skillTags,bindingUri,runtime,onlineOnly,minAvailableCapacity,page*` | `agents,nextPageToken` | filtersHash 绑定游标；稳定 agentId 次序 |
| `a2a.v1.state.presence.heartbeat` | `instanceId,startedAt,runtimes,runningTasks,capacity` | `acceptedAt,presenceState` | agentId 取认证身份；同 instance 覆盖最新值 |
| `a2a.v1.state.agent.unregister` | `instanceId,generation,reason` | `tombstonedAt` | 重复调用返回同 tombstone |
| `a2a.v1.state.principal.resolve` | `authMethod,credentialId/issuer+subject,nkeyPublic` | `principalId,aliasGeneration` | 只读；结果由受信配置与 Credential Registry 决定 |

`card.get/list/search` 返回的 Agent 摘要至少包含 `agentId,name,version,skills,supportedInterfaces,online,lastSeenAt,instances,runtimes,availableCapacity`。Card 本体只由 `card.get` 返回；列表不复制完整 Card，避免大响应。查询游标、索引和 tombstone 的 Redis 契约见 `DATA-CARD-001`。

调用者不能在 heartbeat/upsert payload 中指定其他 agentId；State Service 使用 NATS 认证 principal 映射。Gateway 的 Host 路由先调用 `card.get`，Peer 编排发现优先调用 `agent.search`。

---

## 11. 投递语义与幂等

| 场景 | 保证 |
|---|---|
| Core request/reply | 重试可能重复投递；State dedupe 返回同一个 Task，不承诺任意外部 effect 至多一次 |
| JetStream event | 至少一次消费；consumer 按 taskId/eventSequence 去重 |
| Push webhook | 至少一次；按 deliveryId/eventSequence 去重 |
| Cancel | `CANCELED` 重复调用返回当前 Task；`COMPLETED/FAILED/REJECTED` 返回 `TaskNotCancelableError` |
| Card upsert | card/config generation + publisher fencing 幂等，旧 generation/token 拒绝 |
| State transition | task version + fencing token CAS |
| Redis→JetStream event | State mutation 与 outbox 原子；Relay 至少一次发布，消息 ID=`taskId:eventSeq`，PubAck 后完成 |

Artifact finalize 和 reconciliation resolution 都必须先通过各自权威服务调用 State 原子 mutation，再由同一 outbox/Relay 发布标准 Artifact/Task 更新或项目扩展事件；Object Store、运维 CLI、Runtime 和 provider adapter 均不得直接向 JetStream 声明这些事实。

任意自动重试必须同时满足：稳定 idempotency key、服务端 dedupe、payload hash 一致、任务策略允许重试。

事件提交顺序固定为：

```text
State mutation
→ 原子更新 Task/索引并写 outbox
→ Event Relay 读取 due outbox
→ JetStream publish(eventId=taskId:eventSeq)
→ 等待 PubAck
→ 标记 delivered/删除 outbox
```

Relay 在 publish 前崩溃会重新读取 outbox；在 PubAck 后、标记 delivered 前崩溃可能重复发布，因此 JetStream 和所有消费者都按 eventId/eventSequence 去重。outbox 不接受查询历史，Projector 也不能把旧事件覆盖到更新的 Redis Task 或终态。

---

## 12. 错误映射

| 内部情况 | A2A 错误 | 是否重试 |
|---|---|---|
| 版本不是 1.0 | `VersionNotSupportedError` | 否，协商后重试 |
| target Card 不存在 | 内部 `AgentNotFound`；外部系统错误/路由不可用 | 否 |
| target offline | HTTP 503 / gRPC `UNAVAILABLE` / JSON-RPC system error | 有限重试 |
| 调用方队列/大小上限 | HTTP 429 / gRPC `RESOURCE_EXHAUSTED` / JSON-RPC overload data | 遵守 retry-after |
| 全局执行面过载/不可用 | HTTP 503 / gRPC `UNAVAILABLE` / JSON-RPC system error | 退避后 |
| Message/字段非法 | Binding invalid request/invalid params | 否 |
| Task 不存在 | `TaskNotFoundError` | 否 |
| 已 CANCELED 重复取消 | 返回当前 Task | 否 |
| COMPLETED/FAILED/REJECTED 取消 | `TaskNotCancelableError` | 否 |
| 未声明 Streaming | `UnsupportedOperationError` | 否 |
| Push 未启用 | `PushNotificationNotSupportedError` | 否 |
| NATS 超时 | 系统 unavailable，不伪造 A2A 专用错误 | 同 messageId 可重试 |
| Runtime 启动失败 | Task → FAILED | 按策略 |
| Redis 不可用 | 系统 unavailable，停止新提交 | 恢复后 |

错误响应不包含 subject、内部 IP、argv、栈、凭据和绝对路径。

---

## 13. 重连与故障

### 13.1 Peer 断线

- NATS 客户端自动重连；
- presence 进入 suspect/offline；
- 已运行任务 heartbeat/lease 过期；
- 只有 retry-safe 任务允许新实例接管；
- 旧实例恢复后因 fencing token 失效不能写状态。

### 13.2 NATS 重启

- JetStream 使用持久目录；
- Peer/Gateway 重连并重新订阅；
- Redis 保留 Task 快照；
- Relay 从 Redis outbox 恢复未发布事件；消费者从 JetStream durable 位点重放派生视图；
- 无法确认的副作用任务不自动重新 dispatch。

### 13.3 Gateway 重启

Task 独立于 Gateway 连接继续执行。客户端重新 GetTask/Subscribe。Push Dispatcher 使用 durable consumer 恢复。

---

## 14. 配置基线

### 14.1 内部版本兼容

- Envelope、State RPC、event schema 和 extension URI 均携带 major/minor 版本；
- major 不一致立即返回 version mismatch，不降级猜测；
- 滚动升级只支持当前 minor `N` 与前一 minor `N-1`；发送方在窗口内只使用对端声明支持的字段；
- 新字段必须可选并有安全默认值；删除/改义字段只能进入新 major；
- State schema 采用读旧/写新迁移，所有实例切到读新并观察一个保留窗口后才删除旧字段；
- Agent Card 只在对应 Binding 的语义门禁完成后发布该 interface，不能用代码存在代替兼容声明。

### 14.2 示例配置

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
    stream: "A2AMESH_TASK_EVENTS"
    subjects: ["a2a.v1.events.*"]
    max_age_hours: 24
binding:
  uri: "https://a2amesh.dev/bindings/nats/v1"
  protocol_version: "1.0"
```

凭据不写 YAML，使用环境变量指向 secret 文件或 OS Secret Store。

---

## 15. 验收用例

- **TEST-MESH-001**：Linux 与两台 Windows 仅主动连接 NATS，任意方向 RPC 成功且 Peer 东西向调用不经过 Gateway。
- **TEST-A2A-001**：每个操作的 NATS payload 可由官方对象解析，JSON-RPC/gRPC/NATS advertised Binding 通过同一语义套件。
- **TEST-GRPC-001**：官方 stub 的 unary/server-streaming、deadline/cancel、metadata 和 gRPC status 映射通过。
- **TEST-SEC-001**：私有 inbox/Task event ACL 阻止其他 Peer 订阅；伪造 agentId 无效。
- **TEST-IDEMP-001**：queue group 双实例和 timeout 重试只创建或返回同一个 Task，旧 owner 的状态写入被 fencing 拒绝；外部 effect 由 `TEST-EFFECT-001` 和对账测试验收。
- **TEST-STREAM-001**：多订阅者事件顺序一致，终态后无多余帧；标准订阅不依赖 replay cursor；NATS 具体 session/ACL 故障由 `TEST-NATS-STREAM-SESSION-001` 覆盖。
- **TEST-REGISTRY-001**：upsert/get/list/search/heartbeat/unregister 的认证、分页、排序、generation 和 tombstone 正确。
- **TEST-IDENTITY-001**：三类入口签名、过期/重放/伪造 caller、alias generation 与 Principal 传播正确。
- **TEST-TENANT-001**：NATS Envelope 不含 tenant；官方 payload 非空 tenant 在签名/dispatch 前拒绝。
- **TEST-RECOVERY-001**：NATS/Peer/Gateway 分别重启后 Task 快照与终态一致。
- **TEST-OUTBOX-001**：Relay 在 publish 前后崩溃均不丢事件；重复发布不产生重复状态或通知。
- **TEST-OVERLOAD-001**：Principal 级超限映射 429/RESOURCE_EXHAUSTED，全局不可用映射 503/UNAVAILABLE。
- **TEST-COMPAT-001**：N/N-1 minor 双向 fixture 通过，major mismatch fail closed，Card 不发布未通过门禁的 Binding。
- **TEST-CONFIG-ATOMIC-001**：Envelope、Card upsert、Principal/Grant 使用同一 active config generation，撤销和 generation 冲突在副作用前拒绝。
- **TEST-ARTIFACT-ATOMIC-001 / TEST-RECON-RESOLVE-001**：Artifact/对账事件只能来自已提交 State outbox，直接发布不能改变权威快照。
---

## 16. G0 Binding 冻结合同

### 16.1 内部版本协商

- `bindingUri` 冻结 major 路由；`bindingSchemaVersion=<major>.<minor>` 管内部 Envelope/State/Event Schema；`a2aProtocolVersion` 只表示官方 A2A 协议。
- major 不同直接返回 `BINDING_VERSION_UNSUPPORTED`，不得猜测降级。
- V1 只承诺当前 minor 与前一 minor；发送端选择双方交集的最高 minor，未知 optional 字段安全忽略，未知 required feature 拒绝。
- State/Event/Dispatch response 均回显协商后的 schema version；Card 私有 Registry metadata 记录 supported minor 列表。

### 16.2 Durable dispatch 与 cancel

`claim_message`对新Task原子创建immutable command与dispatch intent，但该intent初始不可投递；`select_admission_for_dispatch`取得DRR/容量reservation后才令其due。Worker领取单调claim token后，必须先调用State `mark_dispatch_sent`成功写SENT，再向独立`a2a.v1.dispatch.<agentId>` publish/request `DispatchTask`；Task Supervisor以dispatch tuple读取immutable command并取得的首次lease仅是provisional，在`accept_dispatch_and_start`成功前不得启动Runtime/effect。该State原子提交同时写ACCEPTED、Task WORKING、reservation→running与outbox，随后Worker才停止重投。CLAIMED/SENT lease过期由`reclaim_expired_dispatch`以新token回到PENDING；旧reply/token永久拒绝。超过dispatch deadline时，State在确认无已接受owner/无未知effect后原子置FAILED。Cancel以Redis `cancelRequested`为权威；SUBMITTED且dispatch未ACCEPTED/无effect时，即使存在provisional owner，也由`request_cancel`同一CAS撤销lease/fence并直接写CANCELED/ABORTED；control消息只加速WORKING owner。

### 16.3 Ordered Event Relay

多 Relay 通过 State `claim_outbox` 获取带 fencing 的 Task head。对同一 Task，未完成 event `n` 时不得 claim/publish `n+1`；其他 Task 可并行。CLAIMED Relay 过期由 `reclaim_expired_outbox` 原子恢复同一 head→PENDING并推进 tombstone token，旧 Relay 的 PubAck/reschedule 永久拒绝。publish 前、PubAck 前后崩溃均可重复，但 `eventId=<taskId>:<eventSeq>` 不变。JetStream 去重窗口过期后，消费者仍按 eventSeq 幂等处理。

### 16.4 G0 验收补充

- **TEST-DISPATCH-001**：分别在 CLAIMED 后、SENT 后/NATS publish 前、publish 后/reply 前杀 Worker；再分别在Supervisor完成`task.command.get`后、取得provisional lease后、`accept_dispatch_and_start`提交前后杀Supervisor或并发cancel。INITIAL_START accept提交前必须零Runtime进程、零effect attempt、零外部progress，provisional lease可被cancel/reclaim撤销；accept提交后仍必须完成observe-without-spawn→签名REGISTER_CONTAINMENT→State READ_CONTAINMENT exact read-back，任何一步前都保持零Runtime/零effect/零外部调用，只有完整链成功才允许启动exact command。另以已WORKING/admission RUNNING Task构造RECOVERY_RESUME dispatch，断言claim允许现有running slot、RECOVERY_PROVISIONAL与RECOVERY_RESUME accept后Task/admission及queued/reserved/running计数不变，只更换owner/attempt/fence并产生一次TASK_OWNER_RECOVERED；错mode、旧operation/fence或走INITIAL_START全部零写入。过期claim必须以更高attempt/token接管，旧token/reply拒绝；同一`leaseOperationId`在provisional lease提交后丢失响应时必须返回原`ExecutionLeaseV1`逐字节且不发第二个fence，异`requestDigest`必须零写入冲突。
- **TEST-CANCEL-RACE-001**：cancel CAS 后丢 control、cancel vs provisional lease、cancel vs accept、cancel vs complete、cancel vs recovery takeover 均符合冻结矩阵；pre-accept cancel 必须撤销 lease/fence且不得被 deadline 改写为 FAILED。
- **TEST-OUTBOX-ORDER-001**：多Relay、CLAIMED后崩溃/过期接管、PubAck前后崩溃和event `n`退避时，旧token拒绝且`n+1`不越过。另把head `n`推进DEAD并设置blockedByDeadSeq，分别错置path taskId、字符串eventId、整数expectedHeadSeq、expectedEventDigest、publishedSeq+1或dead index，逐项断言四个独立比较只要一项失败即零写入且`n+1`仍不可claim。无`ops.outbox.recover`、错误Ops NKey、可变URI、坏hash/签名/expiry/字段/Principal或未绑定tuple的`OutboxRepairEvidenceV1`、同Idempotency-Key异body也全部拒绝。唯一Ops Recovery身份经API→NATS subject提交正确tuple/evidence/key后，同一CAS将原eventId恢复PENDING、清block、写唯一幂等result和一次durable audit；在State提交后/reply前杀进程，同key重入必须逐字节返回原result且recoveryCount/audit不增加。随后只能先发布`n`再发布`n+1`，原DEAD事实仍可从event recovery字段和WORM audit证明。
- **TEST-BINDING-VERSION-001**：major reject、minor N/N-1、required feature 和字段降级 fixture 通过。
- **TEST-NATS-ACL-001**：由signed config展开两Peer Binding、每Peer独立Application Core/Task Supervisor/Orchestrator、Gateway、State、Artifact Adapter、Dispatch/Event Relay、Stream Controller、JS Provisioner、Audit Relay、Recovery Orchestrator、Recovery Verifier、独立Recovery Compactor、Reconciliation Service、Ops Recovery与一个SSE consumer的完整literal-subject fixture，先通过`nats-server --config <fixture> -t`。自动断言`STATE_REQUEST_SUBJECTS_V1`全部89个literal恰有预期发布角色，并为每个signed `components[]` NKey叠加唯一`config.ready`；Artifact Adapter仅可经`artifact.source.commit`提交完整source-centric request，旧ref增删subject必须不存在；Artifact Hold Reaper仅可发布hold.expire closed `SCAN|EXPIRE|REPLAY_CLAIM`，覆盖claim base commit/current authority正反例且不得获得Object Store/provider删除凭据；Artifact Adapter对artifact.delete仅可`REQUEST`，Artifact Delete Worker仅可`COMPLETE`且是唯一provider delete credential持有者；三者Principal、NKey selector和private inbox两两不同且不得互相调用；`recon.claim`五个variant与`recon.scan-due`双scanner正例必须可达，其他Principal、旧scanner fence/candidate token和Recon对其他State subject的越权全部拒绝；正例同时覆盖隔离candidate READY与生产维护域`PRODUCTION_GATED` READY（后者绑定rollout lease/fence、deployed ACL/stream/environment且不承接业务流量），以及获授权跨Agent RPC→本地Core→task claim/get/list/cancel/append/card.get、Push config closed CRUD、stream.flush、Config rollout PREPARE/RENEW/ENTER/ACTIVATE/RESTORE/FINISH/MARK、Config NKey叠加`ops.config.recover`机器Credential执行TAKEOVER、Config→stream-config.begin、Provisioner→stream-config.claim/complete、Dispatch Worker→Supervisor command.get/lease/accept、Supervisor recovery/heartbeat/input/effect、Stream Controller在同一stream.reclaim literal上的SCAN/RECLAIM、Recovery Orchestrator seal/release、Recovery Verifier SCAN→VERIFY/restore、Recovery Compactor在唯一recovery.compact literal上的SCAN/ACQUIRE/RENEW/ADVANCE/RELEASE、Reconciliation effect stale scanner、Orchestrator Plan/recovery.scan、Event Relay outbox、Audit Relay audit.claim/audit.ack和受控Ops dead-outbox recovery。负例必须拒绝Peer直接Task/Plan/input/effect或订阅dispatch、Gateway发布任意`a2a.v1.state.*`或连接Peer/Core IPC、Core调用Plan/effect、非Audit Relay调用audit.claim/audit.ack、Audit Relay调用其他State subject、Recovery Orchestrator调用verify/restore、Recovery Verifier调用seal/release、Recovery Compactor调用seal/verify/restore/release、普通Recovery身份调用compact、旧compaction fence/伪造candidate/越级transition、Supervisor调用Plan/其他Agent command/recovery、Orchestrator调用Task/effect、组件替他人READY或伪造plane/rollout/deployed digest、Config替组件READY、普通Config NKey无独立recover Credential/capability执行TAKEOVER、非Config rollout control/stream-config.begin、非Provisioner stream-config claim/complete、任意非Relay event publish、Event Relay `$JS.API.>`、非Provisioner broker-op、Provisioner其他State RPC、非Provisioner Stream/Consumer API、他人inbox/delivery、伪造replySubject，以及responder第2次或5秒后reply（`allow_responses max=1`）。
`TEST-NATS-ACL-001`还必须以同一bundle分别重算CORE/INTEROP/EXTENDED的`RequiredSlotSetV1`，逐项断言fixed base、`requiredForProfiles`和`explicitlyRequiredOperationalSlots[]`均只生成一个READY publish路径；删除/追加/重复slot、缺requiredSlots/recovery requiredComponents、未知profile/explicit slot、缺`readyReporterPrincipal/probeNkeySelector`、fixed probe与components复用Principal/NKey、probe多授其他State subject、集合外身份获得overlay或Core内嵌Merge Broker被错误展开为独立slot均失败。candidate与production对同一profile生成的stable slot set必须逐字节相等，仅receipt plane/rollout/deployed/environment绑定不同。

- **TEST-NATS-STREAM-SESSION-001**：以两个 Peer、Gateway、两个 Controller instance、JS Provisioner和固定`A2AMESH_TASK_EVENTS` stream生成真实broker fixture。断言reply inbox恰一个`StreamSessionOpenedV1`，后续只走caller delivery。consumer-first后在snapshot读取前发布两事件，使首事件被snapshot覆盖；`max_ack_pending=1`下必须由State covered watermark/permit逐条ACK并最终送达第二事件，Controller不得内存丢弃死锁。让旧Controller持pending后全部Controller进程退出并清空内存；新实例只能通过stream.reclaim SCAN取得持久due candidate/token，再以RECLAIM CAS取得同consumerName、稳定`_DELIVER.a2amesh.controller.<meshId>.<consumerName>`和更高fence，禁止caller自选session或按consumer反查；旧实例无权ACK/NAK。分别在scan ledger/reclaim CAS、final pending、caller ACK、State permit、实际JS ACK、INFO broker确认、CLOSING/EXPIRING、delete、INFO-not-found前后杀进程；同scan/reclaim ID重试逐字节返回且不二次升fence。正常live路径只在finalBrokerAckConfirmed后CLOSED；terminal snapshot路径在唯一已存openedResponseJson flush后，由匹配responseCorePrincipalHash的Core提交exact digest+flushOperationId持久确认，不含controllerFence；在response flush后先让Controller换fence，Core仍必须可提交，错Core/digest/同ID异body拒绝。重试逐字节返回同一initialFrameJson/openedResponseJson。session OPENING后切换active generation并崩溃接管，必须继续使用已存configGeneration/consumerConfigJson/digest；terminal open成功后再改变Task version/审计/结果引用，重试不得重建。deadline路径只在删除确认后EXPIRED。另让全部Controller取消订阅/离线超过旧inactive threshold但短于session expiry，INFO必须仍exists=true且接管无事件缺口；模拟管理员外部删除ACTIVE consumer时必须CONSUMER_LOST→EXPIRING→EXPIRED并要求GetTask+SubscribeToTask，禁止deliverPolicy=new重建。负例逐项拒绝Peer/Gateway `$JS.API.*`/`$JS.ACK.*`、他人caller delivery、伪造scope/streamOpenId、动态filter/delivery、deliverGroup、无State permit、旧fence、伪造INFO/网络错误当not-found及slow consumer越界。所有ticket/request/response fixture只接受`brokerOpRequestDigest`，出现`brokerRequestDigest`或两者并存必须拒绝。在CREATE result被State consume前请求BEGIN CREATE_INFO必须直接拒绝；测试夹具另注入同session/fence但较旧epoch的合法Provisioner签名`exists=false`，完成CREATE后将其重放到CLEANUP_INFO，必须因epoch/kind/challenge/requestDigest不符拒绝；当前合法响应消费后再次提交也必须拒绝。分别在ticket持久化后、Provisioner claim后、裸JS API成功后/complete前、complete后/Controller consume前杀进程；执行lease接管只能幂等执行同一请求，COMPLETED重试必须REPLAY_STORED且不得再次Create/Delete，新controller fence只可签发新epoch，旧ticket/result不得改变session终态。专门构造e1 CREATE已claim且broker apply延迟、e2已BEGIN/PENDING或已claim覆盖current pointer、随后close/expire的交错；close CAS必须读到所有epoch claim单调上界并等待`brokerOpQuiesceUntilMs`后DELETE，最终INFO-not-found且零orphan consumer。

`TEST-NATS-STREAM-SESSION-001`还必须用健康、空闲但未到session expiry的ACTIVE会话执行连续RENEW：同renewOperationId提交后丢响应须逐字节返回原lease结果且fence不变，新的leaseUntil/due score只前进一次；RENEW与SCAN/RECLAIM并发时只能一个CAS成功。未过期owner不得被SCAN作为可接管candidate，lease确已过期后旧owner RENEW必须零写入且新Controller可取得更高fence；CLOSING/EXPIRING/CLOSED/EXPIRED不得续租。

### 16.5 BindingCapabilities 与事件 wire schema

私有 Registry 的 `BindingCapabilities` 必须包含：

```json
{
  "bindingUri": "https://a2amesh.dev/bindings/nats/v1",
  "supportedBindingSchemaVersions": ["1.1", "1.0"],
  "supportedOperations": ["SendMessage", "GetTask"],
  "supportedRequiredFeatures": ["dispatch-task-v1", "ordered-task-events-v1"],
  "eventSchemaVersions": ["1.1"],
  "configGeneration": 42
}
```

协商前不得发送业务命令；双方取 schema version 交集最高值，并验证请求 `requiredFeatures` 是对端声明的子集。所有 response 回显 `bindingUri/bindingSchemaVersion/a2aProtocolVersion/configGeneration`。JetStream `TaskEventEnvelope` 固定包含 `eventId,taskId,eventSeq,taskVersion,eventType,attempt,occurredAt,configGeneration,bindingSchemaVersion,payloadDigest,canonicalStreamResponse`；其中 `eventSeq` 是 Task 内序列，不是 JetStream 全局 stream sequence。Relay 获 PubAck 后可把 `jetStreamStreamSeq` 写入投递回执用于诊断，但订阅正确性不依赖该映射。

### 16.6 可生成的最小 NATS permission matrix

下表中的 `<meshId>/<agentId>/<target>/<instanceId>/<consumer>` 都是**配置生成期占位符**，生成 `nats-server` 配置前必须展开为逐项 literal subject；它们不是 broker wildcard。只有明确写出的 `*`/`>` 才是运行时 wildcard。

| NKey 身份 | Publish allow | Subscribe allow | responder policy |
|---|---|---|---|
| `peer:<agentId>` | grant展开的每个`a2a.v1.rpc.<target>` literal；`a2a.v1.stream.open`、`a2a.v1.stream.ack`、`a2a.v1.stream.close`；固定Registry literals：`a2a.v1.state.card.upsert`、`a2a.v1.state.card.get`、`a2a.v1.state.agent.search`、`a2a.v1.state.agent.unregister`、`a2a.v1.state.principal.resolve`、`a2a.v1.state.presence.heartbeat` | literal `a2a.v1.rpc.<agentId>`、`a2a.v1.control.<agentId>.<instanceId>`；`_INBOX.a2amesh.<agentId>.<instanceId>.>`；`_DELIVER.a2amesh.stream.<agentId>.<instanceId>.*` | `allow_responses {max:1,expires:5s}`仅用于收到的A2A RPC；Peer经受保护本地IPC调用独立Application Core，不持有其NKey；流帧不使用reply |
| `application-core:<agentId>:<instanceId>` | `a2a.v1.state.task.claim`、`a2a.v1.state.task.get`（closed `GET\|LIST` variant）、`a2a.v1.state.task.cancel`、`a2a.v1.state.task.append`、`a2a.v1.state.push.config`（closed CRUD variant）、`a2a.v1.state.stream.flush`、`a2a.v1.state.card.get`、`a2a.v1.state.agent.list`、`a2a.v1.state.agent.search`、`a2a.v1.state.principal.resolve`；为入站流式操作请求`a2a.v1.stream.open`；获授权的literal control | `_INBOX.a2amesh.core.<agentId>.<instanceId>.>` | 无；Peer节点只接受Peer Binding经§16.9 IPC转交；公网Gateway/MCP adapter与Core library固定同一受信进程内调用且由本行Core NKey执行State mutation，禁止Gateway经§16.9 socket/pipe或直接持有Task State subjects；State仍重验Principal/capability/target |
| `task-supervisor:<agentId>:<instanceId>` | `a2a.v1.state.task.command.get`、`a2a.v1.state.task.recover`、`a2a.v1.state.task.heartbeat`、`a2a.v1.state.lease.acquire`、`a2a.v1.state.lease.renew`、`a2a.v1.state.task.transition`、`a2a.v1.state.dispatch.accept`、`a2a.v1.state.input.claim`、`a2a.v1.state.input.ack`、`a2a.v1.state.effect.prepare`、`a2a.v1.state.effect.begin`、`a2a.v1.state.effect.start`、`a2a.v1.state.effect.complete` | queue-group订阅literal `a2a.v1.dispatch.<agentId>`；literal `a2a.v1.control.<agentId>.<instanceId>`；`_INBOX.a2amesh.supervisor.<agentId>.<instanceId>.>` | `allow_responses {max:1,expires:5s}`仅回复收到的DispatchTask；State还必须匹配targetAgent、当前dispatch token或Task owner lease/fence/attempt |
| `orchestrator:<agentId>:<instanceId>` | `a2a.v1.state.plan.save`、`a2a.v1.state.plan.acquire`、`a2a.v1.state.plan.renew`、`a2a.v1.state.plan.recover`、`a2a.v1.state.plan.recovery.scan`、`a2a.v1.state.plan.recovery.step`、`a2a.v1.state.plan.recovery.finalize`、`a2a.v1.state.plan.transition` | `_INBOX.a2amesh.orchestrator.<agentId>.<instanceId>.>` | 无；State 还必须匹配 Plan owner lease/fence/recovery gate；scan只返回持久due候选 |
| `gateway:<instanceId>` | 仅transport/routing：路由展开的每个`a2a.v1.rpc.<target>` literal；`a2a.v1.stream.ack`、`a2a.v1.stream.close`；获授权的literal `a2a.v1.control.<agentId>.<instanceId>`；**无任何`a2a.v1.state.*`** | `_INBOX.a2amesh.gateway.<instanceId>.>`；`_DELIVER.a2amesh.stream.gateway.<instanceId>.*` | 无；Gateway adapter与同进程Core library以typed in-process调用分工，Task/query/stream-open/Principal State RPC均使用独立Core NKey，不能把Core NKey暴露给HTTP/gRPC adapter或Runtime |
| `dispatch-worker:<instanceId>` | 配置中每个 literal `a2a.v1.dispatch.<target>`；`a2a.v1.state.dispatch.claim`、`a2a.v1.state.dispatch.sent`、`a2a.v1.state.dispatch.reclaim`、`a2a.v1.state.dispatch.expire` | `_INBOX.a2amesh.dispatch.<instanceId>.>` | 无 |
| `event-relay:<instanceId>` | `a2a.v1.events.*`（仅一个 taskId token）；`a2a.v1.state.outbox.claim`、`a2a.v1.state.outbox.reclaim`、`a2a.v1.state.outbox.published`、`a2a.v1.state.outbox.reschedule` | `_INBOX.a2amesh.event-relay.<instanceId>.>` | 无；**禁止任何 `$JS.API.>`** |
| `stream-session-controller:<instanceId>` | `a2a.v1.state.stream.open`、`a2a.v1.state.stream.broker-op.begin`、`a2a.v1.state.stream.broker-op.consume`、`a2a.v1.state.stream.activate`、`a2a.v1.state.stream.frame`、`a2a.v1.state.stream.ack`、`a2a.v1.state.stream.broker-ack`、`a2a.v1.state.stream.close`、`a2a.v1.state.stream.expire`、`a2a.v1.state.stream.cleanup`、`a2a.v1.state.stream.reclaim`；该subject的closed operation仅为`SCAN`,`RENEW`,`RECLAIM`；`a2a.v1.js.consumer.create`、`a2a.v1.js.consumer.info`、`a2a.v1.js.consumer.delete`；`_DELIVER.a2amesh.stream.*.*.*` | `a2a.v1.stream.open`、`a2a.v1.stream.ack`、`a2a.v1.stream.close`；`_DELIVER.a2amesh.controller.<meshId>.*`；`_INBOX.a2amesh.stream-controller.<instanceId>.>` | `allow_responses {max: 1, expires: 5s}`；只回复open/ack/close，不以reply发送帧；State校验session owner/fence/broker operation ticket，Controller无裸`$JS.API.*` |
| `js-provisioner:<instanceId>` | `a2a.v1.state.stream.broker-op.claim`、`a2a.v1.state.stream.broker-op.complete`、`a2a.v1.state.stream-config.claim`、`a2a.v1.state.stream-config.complete`；`_INBOX.a2amesh.js-provisioner.<instanceId>.>`；JS API另见下表 | `a2a.v1.js.consumer.create`、`a2a.v1.js.consumer.info`、`a2a.v1.js.consumer.delete`；`_INBOX.a2amesh.js-provisioner.<instanceId>.>` | `allow_responses {max: 1, expires: 5s}`；consumer操作仅接受Controller NKey/session ticket；stream-config操作仅接受State签发的固定stream ticket/execution lease |
| `state:<instanceId>` | `a2a.v1.cards.changed`；`_INBOX.a2amesh.state.<instanceId>.>` | 下面 `STATE_REQUEST_SUBJECTS_V1` 的逐项 literal；`_INBOX.a2amesh.state.<instanceId>.>` | `allow_responses {max: 1, expires: 5s}`；除此之外不得 publish caller inbox |
| `artifact:<instanceId>` | `a2a.v1.state.artifact.create`、`a2a.v1.state.artifact.finalize`、`a2a.v1.state.artifact.delete`、`a2a.v1.state.artifact.hold.create`、`a2a.v1.state.artifact.hold.renew`、`a2a.v1.state.artifact.hold.release`、`a2a.v1.state.artifact.source.commit` | `_INBOX.a2amesh.artifact.<instanceId>.>` | 无；artifact.delete只允许closed `REQUEST`；source.commit可原子触及多个Artifact，State必须重验source owner AuthProof、path tuple、完整五字段refs和old∪new expected versions，不能按单个target授权 |
| `artifact-hold-reaper:<instanceId>` | `a2a.v1.state.artifact.hold.expire` | `_INBOX.a2amesh.artifact-hold-reaper.<instanceId>.>` | 无；只接受Artifact Hold Reaper signed component的closed `SCAN\|EXPIRE\|REPLAY_CLAIM`；State重验due membership/score或terminal commit、绑定owner Principal/instance、lease expiry、issuance ID、fence、完整due tuple的candidate ledger；higher-fence replay必须先持久化claim candidate，裸整数拒绝；不得调用Artifact source/ref/delete或物理Object Store写入 |
| `artifact-delete-worker:<instanceId>` | `a2a.v1.state.artifact.delete` | `_INBOX.a2amesh.artifact-delete-worker.<instanceId>.>` | 无；同subject只允许closed `COMPLETE`，State必须按认证Principal+operation拒绝Adapter伪造COMPLETE；该组件与Adapter/Hold Reaper使用不同Principal/NKey，是唯一可持有provider物理删除凭据的运行组件 |
| `config:<instanceId>` | `a2a.v1.state.config.genesis.prepare`、`a2a.v1.state.config.genesis.commit`、`a2a.v1.state.config.genesis.recover`、`a2a.v1.state.config.stage`、`a2a.v1.state.config.evidence.stage`、`a2a.v1.state.config.ready`、`a2a.v1.state.config.activate`、`a2a.v1.state.stream-config.begin` | `_INBOX.a2amesh.config.<instanceId>.>` | 无；evidence stage仍须State重验release签名/报告/ACL/READY digest；config.activate普通variant只凭Config身份仍须lease/fence，TAKEOVER还须独立机器Credential与`ops.config.recover`；stream-config.begin仅允许有效rollout lease/maintenance gate且固定streamName |
| `ops-recovery:<instanceId>` | `a2a.v1.state.outbox.recover` | `_INBOX.a2amesh.ops-recovery.<instanceId>.>` | 无；只接受私网Ops API经独立机器Credential和`ops.outbox.recover` capability提交Redis §6.15 exact task/event/head/digest/evidence/idempotency tuple，不得自动批量复活或skip |
| `recon:<instanceId>` | `a2a.v1.state.recon.open`、`a2a.v1.state.recon.claim`、`a2a.v1.state.recon.scan-due`、`a2a.v1.state.recon.evidence`、`a2a.v1.state.recon.resolve`、`a2a.v1.state.recon.close`、`a2a.v1.state.recon.reopen`、`a2a.v1.state.effect.scan-stale` | `_INBOX.a2amesh.recon.<instanceId>.>` | 无；`recon.claim`是closed五操作union，`recon.scan-due`是持久scanner lease/scan union；EXPIRE/ESCALATE的Idempotency-Key使用稳定`dueOperationId`，最终`claimOperationId`按通用域分离公式派生且不得等于due ID；State重验case/due candidate与双层fence，其他Principal不得调用 |
| `audit-relay:<instanceId>` | `a2a.v1.state.audit.claim`、`a2a.v1.state.audit.ack` | `_INBOX.a2amesh.audit-relay.<instanceId>.>` | 无；只接受signed components中的`audit-relay` NKey，State重验claim token/fence与exact WORM receipt；不得调用其他State RPC，Event Relay/Recovery/AUDIT_SINK不得复用此身份 |
| `recovery-orchestrator:<instanceId>` | `a2a.v1.state.recovery.seal`、`a2a.v1.state.recovery.release` | `_INBOX.a2amesh.recovery-orchestrator.<instanceId>.>` | 无；唯一Manifest producer/ReleaseReceipt writer，State必须匹配signed component、manifest/release signer kid、approval tuple和operation ledger；不得调用verify/compact或Plan subject |
| `recovery-verifier:<instanceId>` | `a2a.v1.state.recovery.verify`（closed `SCAN\|VERIFY`）、`a2a.v1.state.recovery.restore` | `_INBOX.a2amesh.recovery-verifier.<instanceId>.>` | 无；唯一VerificationReceipt/RestoreReceipt writer，State必须匹配独立verifier signer和producer-different约束；不得调用seal/release/compact或Plan subject |
| `recovery-compactor:<instanceId>` | 仅`a2a.v1.state.recovery.compact` | `_INBOX.a2amesh.recovery-compactor.<instanceId>.>` | 无；subject内为closed `SCAN\|ACQUIRE\|RENEW\|ADVANCE\|RELEASE` union，State还校验持久due candidate、source lease/fence、transition state和exact URI/digest；不得调用其他Recovery/State RPC |
| `RequiredSlotSetV1` READY overlay | signed `components[]`运行组件的自身NKey额外且仅额外获得`a2a.v1.state.config.ready`；外部固定基础slot的descriptor-bound独立probe credential也仅获该subject | 运行组件复用既有私有inbox；probe仅订阅`_INBOX.a2amesh.ready-probe.<instanceId>.>` | ACL generator必须从`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`逐slot生成，不接受手写列表；State要求AuthProof signer/readyReporterPrincipal、componentPrincipal/nodeId/instanceId与signed slot一一匹配，probe result还须匹配verificationMethod/expectedDigest；request只允许`CANDIDATE`或`PRODUCTION_GATED`平面，后者绑定current rollout lease/fence、deployed ACL/stream/environment且trafficGate=CLOSED；不能替其他slot报告、复用receipt或为集合外身份生成overlay |

`application-core:<agentId>:<instanceId>`可发布既有`a2a.v1.state.lease.acquire`/`a2a.v1.state.lease.renew`的closed `resourceType=WORKSPACE` variant，仅用于Core-owned Merge校验/续租；`task-supervisor`的同一subject权限仍只覆盖Task lease variant。任何Core/Peer/Runtime caller都不能用Task variant取得或延长workspace fence，且不存在额外`workspace.merge` State subject。

`STATE_REQUEST_SUBJECTS_V1` 是以下 literal 集，生成器不得缩写成 `a2a.v1.state.*` 或 `a2a.v1.state.>`：

```text
a2a.v1.state.task.claim        a2a.v1.state.task.get
a2a.v1.state.task.command.get  a2a.v1.state.task.recover
a2a.v1.state.task.heartbeat
a2a.v1.state.task.transition   a2a.v1.state.task.cancel
a2a.v1.state.task.append       a2a.v1.state.card.get
a2a.v1.state.agent.list        a2a.v1.state.agent.search
a2a.v1.state.agent.unregister  a2a.v1.state.principal.resolve
a2a.v1.state.push.config       a2a.v1.state.effect.scan-stale
a2a.v1.state.plan.recovery.scan a2a.v1.state.stream.flush
a2a.v1.state.stream-config.begin a2a.v1.state.stream-config.claim
a2a.v1.state.stream-config.complete
a2a.v1.state.card.upsert       a2a.v1.state.presence.heartbeat
a2a.v1.state.lease.acquire     a2a.v1.state.lease.renew
a2a.v1.state.dispatch.claim    a2a.v1.state.dispatch.sent
a2a.v1.state.dispatch.reclaim  a2a.v1.state.dispatch.accept
a2a.v1.state.dispatch.expire   a2a.v1.state.outbox.claim
a2a.v1.state.outbox.reclaim    a2a.v1.state.outbox.published
a2a.v1.state.outbox.reschedule a2a.v1.state.outbox.recover
a2a.v1.state.input.claim
a2a.v1.state.input.ack         a2a.v1.state.effect.prepare
a2a.v1.state.effect.begin      a2a.v1.state.effect.start
a2a.v1.state.effect.complete   a2a.v1.state.plan.save
a2a.v1.state.plan.acquire      a2a.v1.state.plan.renew
a2a.v1.state.plan.recover      a2a.v1.state.plan.recovery.step
a2a.v1.state.plan.recovery.finalize a2a.v1.state.plan.transition
a2a.v1.state.artifact.create   a2a.v1.state.artifact.finalize
a2a.v1.state.artifact.delete   a2a.v1.state.artifact.hold.create
a2a.v1.state.artifact.hold.renew a2a.v1.state.artifact.hold.release a2a.v1.state.artifact.hold.expire
a2a.v1.state.artifact.source.commit
a2a.v1.state.config.genesis.prepare a2a.v1.state.config.genesis.commit
a2a.v1.state.config.genesis.recover a2a.v1.state.config.stage
a2a.v1.state.config.evidence.stage
a2a.v1.state.config.ready      a2a.v1.state.config.activate
a2a.v1.state.recon.open        a2a.v1.state.recon.claim
a2a.v1.state.recon.scan-due
a2a.v1.state.recon.evidence    a2a.v1.state.recon.resolve
a2a.v1.state.recon.close       a2a.v1.state.recon.reopen
a2a.v1.state.audit.claim       a2a.v1.state.audit.ack
a2a.v1.state.recovery.seal     a2a.v1.state.recovery.verify
a2a.v1.state.recovery.restore  a2a.v1.state.recovery.release
a2a.v1.state.recovery.compact
a2a.v1.state.stream.open       a2a.v1.state.stream.activate
a2a.v1.state.stream.broker-op.begin a2a.v1.state.stream.broker-op.claim
a2a.v1.state.stream.broker-op.complete a2a.v1.state.stream.broker-op.consume
a2a.v1.state.stream.frame      a2a.v1.state.stream.ack
a2a.v1.state.stream.broker-ack a2a.v1.state.stream.close
a2a.v1.state.stream.expire     a2a.v1.state.stream.cleanup
a2a.v1.state.stream.reclaim
```

`task-supervisor` 与 `orchestrator` 必须是 signed config `components[]` 中不同的稳定 component Principal/NKey selector，均不得复用 untrusted Runtime、Tool 或通用 Peer Binding NKey。Broker ACL 只给出调用可能性；State 在每次 supervisor RPC 上复核 taskId 当前 owner agent/instance、lease/fence/attempt，在每次 orchestrator RPC 上复核 planId owner lease/fence、expected revision/recoveryEpoch/recoveryRevision。其他 Supervisor/Orchestrator 即使猜中资源 ID 也必须拒绝并审计。

JetStream 权限与 Core request 权限分离：

| 身份 | JetStream Publish allow | Subscribe allow |
|---|---|---|
| `js-provisioner:<instanceId>` | 对固定 stream `A2AMESH_TASK_EVENTS` 的 literal `$JS.API.STREAM.CREATE.A2AMESH_TASK_EVENTS`、`$JS.API.STREAM.UPDATE.A2AMESH_TASK_EVENTS`、`$JS.API.STREAM.INFO.A2AMESH_TASK_EVENTS`；动态 session consumer 仅允许单 token wildcard `$JS.API.CONSUMER.DURABLE.CREATE.A2AMESH_TASK_EVENTS.*`、`$JS.API.CONSUMER.INFO.A2AMESH_TASK_EVENTS.*`、`$JS.API.CONSUMER.DELETE.A2AMESH_TASK_EVENTS.*` | `_INBOX.a2amesh.js-provisioner.<instanceId>.>`；禁止 `$JS.API.>` |
| `stream-session-controller:<instanceId>` | `$JS.ACK.A2AMESH_TASK_EVENTS.*.>`；只能在 State 发放可幂等重取的 exact tuple ACK permit 后使用 | `_DELIVER.a2amesh.controller.<meshId>.*`；不得订阅裸 `a2a.v1.events.>` |
| `sse/push/observer:<instanceId>` | 自身 consumer 的 `$JS.ACK.A2AMESH_TASK_EVENTS.<consumer>.>`；Observer intervention 另需业务 capability 和固定 State subject | 配置生成的 literal `_DELIVER.a2amesh.<role>.<instanceId>.<consumer>` 与自身 inbox；不得订阅裸 `a2a.v1.events.>` |

Event Relay只向stream subject `a2a.v1.events.*` publish并收PubAck，不创建/更新Consumer。JS Provisioner的consumer `*`只代表固定stream后的单个consumer-name token；应用层必须按Create/Info/Delete wire、Controller NKey、State session、确定性名称和consumerConfigDigest再授权，绝不接受caller提供的任意stream/filter/delivery。Peer/Gateway无任何`$JS.API.*`或`$JS.ACK.*`；其第二次request reply仍被broker拒绝，合法多帧只来自Controller的私有delivery publish。

NATS `*` 只匹配单个 token；`>` 只允许在身份私有 inbox 或固定 stream+consumer ACK prefix。broker ACL 是连接级粗粒度门禁；State capability/grant 是逐 Principal/target/operation 业务门禁，两者必须都通过。配置 generation rollout 先展开并以 `nats-server --config <fixture> -t` 验证新 ACL，再激活业务 generation；正反向连接测试必须覆盖跨 Agent RPC、伪造 dispatch/event、他人 inbox、reply 放大、Stream Controller 越权、JS provisioner 越权和 consumer ACK/delivery。

### 16.7 通用 AuthProof replay 与合法业务重试

每个有副作用或受保护的内部 request（含 Get/Cancel/State mutation、dispatch accept、Config READY）先调用通用 `claim_auth_request`。`requestId` 在 signer scope 内一次性使用：同 digest 或异 digest 重放都在 State 内部记录 reason `AUTH_PROOF_REPLAYED`，不会再次进入业务函数；NATS response wire 与所有外部 Binding 一律只返回 `AUTH_PROOF_INVALID`。若 response 丢失，调用方必须生成**新的 requestId/issuedAt/AuthProof**，同时保持业务幂等 ID（如 messageId、taskId+operation、dispatchId）不变；State 业务幂等返回既有结果。这样 replay 防护不承担业务结果缓存，也不产生跨 Binding 错误差异。

入口签名前传输层携带的是已验证 credential identity（credentialId/NKey/issuer+clientId）和 alias generation observation，不是可信 `principalId`。State 在同一受信调用内按指定 active generation 最终解析 Principal；Envelope 中的 Principal 只作为签名声明与审计比对，不能跳过 Credential/alias 复核。

### 16.8 Canonical request hash 与期限分类

Send 的 `lookupKey = principalHash + targetAgentId + messageId`；`conflictDigest` 为：官方类型解析 Request → 拒绝重复 JSON key/未知 required 字段/非有限数字 → 将 absent `returnImmediately` 规范为 `true`、拒绝 `false` → 对具有集合语义的 extensions/output modes 去重并按 UTF-8 排序 → 官方 ProtoJSON 输出 → RFC 8785 canonical bytes → SHA-256。transport `requestId/replySubject/sentAt/AuthProof` 不进入 digest；业务 Message、声明扩展与结果模式进入 digest。所有 Binding 共用同一 fixture。

期限字段不得复用：`requestDeadlineAt`（入口处理）、`queueDeadlineAt`（从 claim commit 起）、`dispatchDeadlineAt`（从 DRR SELECTED 起）、`softExecutionDeadlineAt` 与 `hardExecutionDeadlineAt`（从 WORKING commit 起）。实际值取签名 policy、caller 更短限制与 Task hard deadline 的最小值；等待 INPUT/AUTH 不暂停 hard deadline，除非签名 policy 明示。queue 过期写 CANCELED/FAILED 以 Task 状态矩阵为准，dispatch 过期写 FAILED，request 过期不创建 Task，hard execution 到期走 cancel/effect 对账。

### 16.9 Protected Local IPC Profile V1

本节端点用于**Peer Binding或Task Supervisor→同机Application Core**的两类closed request：`A2A_BINDING`与`WORKSPACE_MERGE`。公网Gateway/MCP adapter不连接该socket/pipe；它们与Core library固定同一受信进程，通过typed in-process interface调用并由Core NKey执行State RPC。Peer/Core路径禁止loopback TCP、继承stdio、临时文件、共享内存裸队列或caller直接复用Core NKey。`agentKey=lowerBase32NoPad(SHA-256(UTF8(agentId)))[0:26]`；active signed config的`components[]`必须为本Agent的`peer-binding`、`task-supervisor`与`application-core`分别固定稳定component Principal、NKey selector、`ipcProfileVersion=1`、host OS principal和binary digest，主机服务管理器实际身份/二进制任一不匹配即READY NACK；`task-supervisor`只可提交`WORKSPACE_MERGE`，Peer Binding只可提交`A2A_BINDING`，Core按OS身份与requestKind双重拒绝越权。

| OS | endpoint与ACL | 双向本机身份校验 |
|---|---|---|
| Linux | `AF_UNIX/SOCK_STREAM`端点固定`/run/a2amesh/<agentKey>/core.sock`；父目录由root预建、非符号链接、owner为Core UID、mode `0710`，socket owner为Core UID、group为仅含Peer/Core/Task-Supervisor UID的专用GID、mode `0660`；每次启动先`lstat`并拒绝symlink/world-writable/错误owner | Core对accept socket读取`SO_PEERCRED`并要求UID/GID恰等于signed component映射的`peer-binding`或`task-supervisor` OS principal；完成requestKind/operation allowlist检查（Peer只准`A2A_BINDING`，Supervisor只准`WORKSPACE_MERGE`）后才解析payload；Peer/Supervisor连接后同样读取peer credentials并要求server UID恰等于Core UID。PID只用于审计，授权不依赖PID复用；root/主机管理员属于部署信任边界，不映射为应用Principal |
| Windows | Named Pipe固定`\\.\pipe\a2amesh\<agentKey>\core`，以`PIPE_REJECT_REMOTE_CLIENTS`创建；SDDL仅授予Core service SID full control、Peer service SID与Task-Supervisor service SID read/write/connect，显式拒绝Anonymous/Network/Everyone，禁止继承宽DACL | Core在每次连接上`ImpersonateNamedPipeClient`并以`TokenUser`要求exact Peer或Task-Supervisor service SID，随后立即`RevertToSelf`；requestKind/operation必须分别是`A2A_BINDING`或`WORKSPACE_MERGE`；Peer/Supervisor用`GetNamedPipeServerProcessId`取得server并校验其process token包含exact Core service SID。任一API不可用、远程client、错误SID、错误requestKind或宽DACL均fail closed |

唯一request frame为`u32be(frameLen)||RFC8785_UTF8(LocalCoreRequestV1)`，`frameLen`仅计JSON bytes且必须在`1..16,777,216`。payload恰含`schemaVersion,ipcRequestId,ipcRequestDigest,agentId,callerComponentType,callerComponentPrincipal,callerInstanceId,bindingVersion,requestKind,operation,receivedAt,expiresAt,bindingEnvelope,credentialObservation,mergeRequest`；`ipcRequestId`为安全ULID，时间为UTC恰3位毫秒`Z`且窗口不超过5秒，`ipcRequestDigest=SHA-256(RFC8785(payload排除ipcRequestDigest))`。`credentialObservation`恰含`authMethod,credentialId,issuerHash,aliasGeneration,tokenDigest,verifiedAt`，nullable字段必须显式`null`；只传入口已验证观察值和Token HMAC/digest，绝不传Bearer/OAuth secret、私钥或预计算可信`principalId/AuthContext`。Core仍须按§6.5和State Principal/capability重新解析与授权，不能因OS身份跳过业务门禁。

`requestKind=A2A_BINDING`时，`callerComponentType=peer-binding`、`operation=BINDING`、`bindingEnvelope`非空、`mergeRequest=null`；`requestKind=WORKSPACE_MERGE`时，`callerComponentType=task-supervisor`、`operation=MERGE`、`bindingEnvelope=null`且`credentialObservation=null`（OS peer credential是唯一transport身份来源），`mergeRequest`恰含`schemaVersion,mergeOperationId,workspaceAlias,taskId,attemptId,workspaceFencingToken,baseRevision,expectedDiffDigest,activeGeneration,policySnapshotHash,sourceWorktreeHandle,mergeRequestDigest`。`sourceWorktreeHandle`是Core预授权的opaque handle，绝不是路径、路径片段或caller可解析的目录名；`mergeRequestDigest=SHA-256(RFC8785(mergeRequest排除mergeRequestDigest))`。Task Supervisor先通过既有`a2a.v1.state.lease.acquire`的`resourceType=WORKSPACE` variant取得workspace fencing grant，再把该grant和Core签发的handle带入Merge；Core在执行前和commit临界区分别向State重读/核验Task lease、workspace lease、五元组及active generation。`WorkspaceMergeResponseV1`恰含`schemaVersion,mergeOperationId,status,workspaceAlias,baseRevision,newRevision,commitId,actualDiffDigest,activeGeneration,policySnapshotHash,committedAt,resultDigest`，`status`只允许`COMMITTED|NOOP`；拒绝走外层error，不以伪造result冒充成功。

Merge本身是Core-owned本地操作，不新增NATS State literal：Core journal以`callerComponentPrincipalHash+mergeOperationId`为幂等scope，`businessOperationId=mergeOperationId`、`businessRequestDigest=mergeRequestDigest`；Core先持久`PREPARED`/commit marker，再以handle-relative方式读取私有worktree、计算actualDiffDigest并在同一临界区验证`workspaceFencingToken,baseRevision,expectedDiffDigest,activeGeneration,policySnapshotHash`五元组，成功后只产生一个`commitId`和受审计shared-root变更。旧token、旧revision、expected diff漂移、generation/policy漂移、错误caller或任一路径逃逸均零文件写、零State推进。Core恢复时只接受同一operation的持久commit marker/ledger，已COMMITTED逐字节重放，PREPARED无marker固定ABORTED；不得重新执行不确定的filesystem side effect。

唯一response frame使用同一`u32be(frameLen)||RFC8785_UTF8(LocalCoreResponseV1)`规则且不追加CRC；payload恰含`schemaVersion,ipcRequestId,ipcRequestDigest,status,bindingResponse,error,responseDigest`，成功时bindingResponse非null且error=null，失败时相反，`responseDigest=lowerhex(SHA-256(RFC8785(payload排除responseDigest)))`。Core必须先构造并append一条`LocalCoreReplayRecordV1(state=IN_FLIGHT)`，对journal record执行fdatasync；随后必须将包含该完整record的`durableOffset`写临时sidecar、fdatasync、原子rename并fsync父目录，**只有这一sidecar线性化提交成功后才允许调用State/业务writer**。State/业务writer提交后，Core再append完整`COMPLETED` record并fdatasync，重复同样的sidecar提交顺序后才可发送response；任何sidecar rename、父目录fsync或journal校验失败均保持READY NACK/fail closed，不调用后续业务或不发送不确定成功。IN_FLIGHT时后两字段必须都显式null；COMPLETED时responseFrameBytes必须是**完整response frame（含4字节长度前缀）**的base64url无padding编码，responseDigest必须是其中LocalCoreResponseV1的64位小写digest；其他组合、padding、非最短base64url或内外digest不符均拒绝。

journal物理record恰为`u32be(jsonLen)||canonicalJson||u32be(crc32c)`：jsonLen只计canonicalJson且范围`1..16,777,216`；CRC固定CRC-32C/ISCSI（Castagnoli normal polynomial `0x1EDC6F41`、reflected `0x82F63B78`、init/xorout均`0xffffffff`、refin/refout=true），**只覆盖canonicalJson bytes**并以network byte order写4字节。每次record fdatasync成功后，Core再将`durableOffset=previousOffset+4+jsonLen+4`写临时sidecar、fdatasync并原子rename且fsync父目录。重启只可截断durableOffset之后的incomplete suffix；durableOffset以内的长度/CRC/canonical/状态错误或完整尾记录CRC错误一律隔离journal并READY NACK，不能跳过中间记录。Core在有效期内按`callerComponentPrincipalHash+ipcRequestId`读取：同ID同digest且COMPLETED解码后逐字节返回原frame，同ID异digest拒绝；IN_FLIGHT不得再次执行业务，只以稳定businessOperationId/businessRequestDigest查询State幂等ledger，找到原result后append+sync COMPLETED，找不到则写并返回固定IPC_REPLAY_INDETERMINATE。过期记录只有在新compact journal、sidecar和父目录依次sync/rename后才能删除；journal不保存secret/AuthContext/raw request。

- **TEST-IPC-REPLAY-001**：固定序列化fixture如下；JSON均为所示单行UTF-8且不得添加换行。A2A_BINDING IN_FLIGHT canonical JSON为`{"businessOperationId":"op-01","businessRequestDigest":"2222222222222222222222222222222222222222222222222222222222222222","callerComponentPrincipalHash":"0000000000000000000000000000000000000000000000000000000000000000","createdAt":"2026-08-15T00:00:00.000Z","expiresAt":"2026-08-15T00:00:05.000Z","ipcRequestDigest":"1111111111111111111111111111111111111111111111111111111111111111","ipcRequestId":"01J00000000000000000000","responseDigest":null,"responseFrameBytes":null,"schemaVersion":"1","state":"IN_FLIGHT"}`，jsonLen=`513/0x00000201`、CRC32C=`0531df8e`、JSON SHA-256=`0e6430428bfe4b7d8ca2aa8aec41600b0a611f3166640bd8c22b85e441ba6d86`。COMPLETED使用相同identity/time/business字段，responseDigest=`6635368f67d6d79daaeea11e05d0dccf715ba3006e8b1b30a02c28bb65cf94f6`，responseFrameBytes=`AAABVnsiYmluZGluZ1Jlc3BvbnNlIjpudWxsLCJlcnJvciI6eyJjb2RlIjoiSVBDX1JFUExBWV9JTkRFVEVSTUlOQVRFIiwibWVzc2FnZSI6ImluZGV0ZXJtaW5hdGUifSwiaXBjUmVxdWVzdERpZ2VzdCI6IjExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTEiLCJpcGNSZXF1ZXN0SWQiOiIwMUowMDAwMDAwMDAwMDAwMDAwMDAwMCIsInJlc3BvbnNlRGlnZXN0IjoiNjYzNTM2OGY2N2Q2ZDc5ZGFhZWVhMTFlMDVkMGRjY2Y3MTViYTMwMDZlOGIxYjMwYTAyYzI4YmI2NWNmOTRmNiIsInNjaGVtYVZlcnNpb24iOiIxIiwic3RhdHVzIjoiRVJST1IifQ`；解码的response JSON len=`342/0x00000156`、CRC32C=`011c7ac0`。完整COMPLETED record canonical JSON len=`1035/0x0000040b`、CRC32C=`9adf5483`、JSON SHA-256=`39600c15fa1b6f0e10c97fa1a3e4a7f6efb2f7f0f5e801745cba0454158f0ef6`。
- **TEST-WORKSPACE-MERGE-REPLAY-001**：固定Merge fixture：IN_FLIGHT canonical JSON为`{"businessOperationId":"merge-op-01","businessRequestDigest":"2222222222222222222222222222222222222222222222222222222222222222","callerComponentPrincipalHash":"3333333333333333333333333333333333333333333333333333333333333333","createdAt":"2026-08-15T00:00:00.000Z","expiresAt":"2026-08-15T00:00:05.000Z","ipcRequestDigest":"4444444444444444444444444444444444444444444444444444444444444444","ipcRequestId":"01J00000000000000000001","responseDigest":null,"responseFrameBytes":null,"schemaVersion":"1","state":"IN_FLIGHT"}`，jsonLen=`519/0x00000207`、CRC32C=`47316f60`、JSON SHA-256=`d361409081e0ac8113b50869ef3931761d258b57d46343d9389bbca447499809`；唯一COMPLETED response JSON len=`765/0x000002fd`、CRC32C=`d116106f`、JSON SHA-256=`3d87abc9c50d9e2a688d572a5e0cf5b2ac0f00615bc026d54f24605b8b524147`、responseDigest=`a8af6f00631c35822b822c0b2501e025e30a802d9b2c49924695b16401f55e2f`、frameLen=`769`、responseFrameBytes=`AAAC_XsiYmluZGluZ1Jlc3BvbnNlIjp7ImFjdGl2ZUdlbmVyYXRpb24iOiJnZW4tMSIsImFjdHVhbERpZmZEaWdlc3QiOiI1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1NTU1IiwiYmFzZVJldmlzaW9uIjo3LCJjb21taXRJZCI6ImNvbW1pdC0wMSIsImNvbW1pdHRlZEF0IjoiMjAyNi0wOC0xNVQwMDowMDowMS4wMDBaIiwibWVyZ2VPcGVyYXRpb25JZCI6Im1lcmdlLW9wLTAxIiwibmV3UmV2aXNpb24iOjgsInBvbGljeVNuYXBzaG90SGFzaCI6IjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjYiLCJyZXN1bHREaWdlc3QiOiI0MGY5NjFmZDdjZjNiZjc1ZjljYzNmNmJiM2ViZDY0MWQwODA4YmI1YzkxZmYzMzlmMzRhNWQ0NjQxZTQ4MTRlIiwic2NoZW1hVmVyc2lvbiI6IjEiLCJzdGF0dXMiOiJDT01NSVRURUQiLCJ3b3Jrc3BhY2VBbGlhcyI6InJlcG86YTJhbWVzaCJ9LCJlcnJvciI6bnVsbCwiaXBjUmVxdWVzdERpZ2VzdCI6IjQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQiLCJpcGNSZXF1ZXN0SWQiOiIwMUowMDAwMDAwMDAwMDAwMDAwMDAwMSIsInJlc3BvbnNlRGlnZXN0IjoiYThhZjZmMDA2MzFjMzU4MjJiODIyYzBiMjUwMWUwMjVlMzBhODAyZDliMmM0OTkyNDY5NWIxNjQwMWY1NWUyZiIsInNjaGVtYVZlcnNpb24iOiIxIiwic3RhdHVzIjoiT0sifQ`；将IN_FLIGHT的`responseDigest/responseFrameBytes/state`替换为上述值与`COMPLETED`后，完整record len=`1605/0x00000645`、CRC32C=`b4d10f9c`、JSON SHA-256=`0d3e8d5b71685ef92ba5539614855666dec83cd86225357be3a1d6b2f33ecc18`。测试必须在journal append前、IN_FLIGHT fdatasync后、durableOffset sidecar rename前、filesystem commit marker写入后、State/ledger complete前、COMPLETED sidecar提交前和response发送前分别杀Core；重启只可查询同一`mergeOperationId`并逐字节重放，不能重复shared-root commit。

`TEST-WORKSPACE-FENCE-001`必须使用真实Core-owned Merge路径构造两个attempt：合法`WORKSPACE_MERGE`请求在Task lease、workspace lease、opaque handle和五元组全部匹配时只产生一次shared-root commit，并返回稳定`commitId,newRevision,actualDiffDigest`及审计记录；逐项篡改`workspaceFencingToken`、`baseRevision`、`expectedDiffDigest`、`activeGeneration`、`policySnapshotHash`、task/attempt owner、handle generation或mergeOperationId body，分别断言State/Core零写、文件系统零写、无revision推进、无audit/outbox成功记录。旧fence即使来自同一Principal、同一workspace和同一有效OS连接也必须拒绝；Runtime、Tool、Peer Binding、Gateway、错误UID/GID/SID或把`A2A_BINDING`改成Merge operation均拒绝。Linux逐项测试绝对路径、`..`、symlink、bind mount和`openat2`约束，Windows逐项测试drive escape、junction、reparse point和handle owner漂移；caller只能提供opaque `sourceWorktreeHandle`，不能提交路径字符串。并发提交同一baseRevision只能一个CAS成功，失败方不得重试成第二个commit；旧attempt仍可写自己的私有worktree但永远不能写shared root。结合R3c2b每个journal/marker/State complete crash点验证：恢复后同一mergeOperationId只REPLAY_STORED或固定ABORTED，不重复filesystem side effect，任一五元组不一致均保持零写入。

`TEST-NATS-ACL-001`的同机companion fixture必须在Linux和Windows各跑一组授权正例，并逐项拒绝错误UID/GID/SID、world-writable或继承宽ACL、symlink/stale endpoint、远程pipe、错误server身份、oversize/零长/截断frame、未知/额外字段、过期时间、同ipcRequestId异digest、raw secret/预计算AuthContext以及任意未授权本机进程；同时断言Peer进程没有Task mutation NKey，Gateway NKey没有任何State subject且不能连接§16.9端点，Core调用State后仍执行Principal/capability检查。`TEST-IPC-REPLAY-001`必须逐字节重算上述两个fixture，再注入journal append/fdatasync前、IN_FLIGHT sync后、业务State CAS后、COMPLETED sync前后、sidecar rename/父目录fsync前后、Core重启、durableOffset外截断、offset内坏CRC和中间损坏：同ID同digest在重启后逐字节返回原response，同ID异digest零写入，IN_FLIGHT不得重新执行业务而必须按businessOperationId查询既有ledger；ledger不存在时固定`IPC_REPLAY_INDETERMINATE`，只有offset外不完整suffix可截断，其他损坏READY NACK，过期journal安全清理，Linux/Windows权限边界和5秒窗口均验证。`TEST-IPC-REPLAY-001`顺序补充：在IN_FLIGHT record fdatasync后、sidecar rename前杀Core，必须断言State/业务writer调用次数为0；在sidecar父目录fsync成功后才允许一次业务CAS；在业务CAS成功后、COMPLETED sidecar提交前杀Core，重启只能依据稳定businessOperationId查询原State result并写同一COMPLETED，不得再次执行业务；任一sidecar损坏/offset内CRC错误都必须READY NACK而不是截断到猜测位置。

---

## 17. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [AgentCard与协议对象规范 V1.6](A2AMesh_AgentCard与协议对象规范_V1.6.md)
- [Redis状态平面与数据设计 V1.6](A2AMesh_Redis状态平面与数据设计_V1.6.md)
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
