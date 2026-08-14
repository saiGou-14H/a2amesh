# A2AMesh 接口请求与响应标准 V1.0

---

# 1. 文档目的

本文档定义 A2AMesh V1 的公共 Agent Card、标准 A2A JSON-RPC/SSE、Push Webhook、内部 NATS Binding、公共报文、错误、幂等、分页和扩展接口。接口对象以官方 A2A v1 Proto/SDK 为准，本文档不复制完整 Proto 字段表。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 A2A v1.0 Gateway、11 操作、SSE、Push、NATS Envelope、错误和示例契约 |

## 1.2 接口状态

当前代码只实现私有 NATS 风格方法，本文标准 Gateway 为目标设计。在官方 SDK 黑盒通过前，不得将本接口标记为已上线。

---

# 2. 基础约定

## 2.1 地址

```text
Agent Card:   GET  https://<host>/.well-known/agent-card.json
JSON-RPC:     POST https://<host>/a2a
SSE:          由 SendStreamingMessage / SubscribeToTask 返回
Health live:  GET  https://<host>/health/live
Health ready: GET  https://<host>/health/ready
```

真实 JSON-RPC URL 以 Agent Card `supportedInterfaces[].url` 为准。

## 2.2 请求头

| Header | 规则 |
|---|---|
| `A2A-Version` | 标准请求必须为 `1.0`；空值按规范视作旧版本并返回 VersionNotSupported |
| `Content-Type` | `application/json` 或规范要求的 A2A media type |
| `Accept` | JSON 或 `text/event-stream` |
| `Authorization` | 公网生产按 Card security 声明；私有测试可关闭 |
| `X-Request-Id` | 可选；Gateway 缺失时生成，只用于 Trace，不替代 messageId 幂等 |
| `traceparent` | 支持 W3C Trace Context |

响应回传 `X-Request-Id`，不得回显 Token。

## 2.3 ID 与时间

- ID 为不透明字符串；
- Task ID 由服务端生成；
- Message ID 由消息创建方生成并稳定重试；
- 时间使用 RFC 3339；
- duration/latency 内部使用毫秒整数；
- 不把 Redis/NATS/主机 ID 当公共资源 ID。

## 2.4 JSON-RPC

请求：

```json
{
  "jsonrpc": "2.0",
  "id": "rpc-01H...",
  "method": "SendMessage",
  "params": {}
}
```

响应：

```json
{
  "jsonrpc": "2.0",
  "id": "rpc-01H...",
  "result": {}
}
```

具体 result/params 使用官方操作对象。实现必须复用官方 SDK Server Adapter，避免手写字段丢失。

---

# 3. Agent Card 接口

## 3.1 请求

```http
GET /.well-known/agent-card.json HTTP/1.1
Host: mesh.example.com
If-None-Match: "card-sha256-..."
```

## 3.2 响应

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: public, max-age=60
ETag: "card-sha256-..."
```

Card 内容见《Agent Card 与协议对象规范》。ETag 匹配返回 304 无 body。presence offline 不直接使 public Card 404；Gateway 可在路由时返回 unavailable。

---

# 4. 操作清单

| API ID | Operation | 输入 | 输出 |
|---|---|---|---|
| API-A2A-001 | SendMessage | SendMessageRequest | SendMessageResponse（Task 或 Message） |
| API-A2A-002 | SendStreamingMessage | SendMessageRequest | StreamResponse stream |
| API-A2A-003 | GetTask | GetTaskRequest | Task |
| API-A2A-004 | ListTasks | ListTasksRequest | ListTasksResponse |
| API-A2A-005 | CancelTask | CancelTaskRequest | Task |
| API-A2A-006 | SubscribeToTask | SubscribeToTaskRequest | StreamResponse stream |
| API-A2A-007 | CreateTaskPushNotificationConfig | TaskPushNotificationConfig | PushNotificationConfig |
| API-A2A-008 | GetTaskPushNotificationConfig | request | PushNotificationConfig |
| API-A2A-009 | ListTaskPushNotificationConfigs | request | list response |
| API-A2A-010 | DeleteTaskPushNotificationConfig | request | 幂等删除确认 |
| API-A2A-011 | GetExtendedAgentCard | request | AgentCard |

接口只在 Card capability/interface 已声明时可调用。

---

# 5. SendMessage

## 5.1 新任务

```json
{
  "jsonrpc": "2.0",
  "id": "rpc-1",
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "msg-1",
      "role": "ROLE_USER",
      "parts": [{"text": "Inspect the repository and run tests."}],
      "extensions": [
        "https://a2amesh.dev/extensions/runtime-selection/v1"
      ]
    },
    "configuration": {
      "returnImmediately": true,
      "acceptedOutputModes": ["text/plain", "application/json"]
    }
  }
}
```

服务端：

- 校验官方对象；
- 用 caller/target/messageId/payloadHash claim；
- 服务端生成 Task ID；
- `returnImmediately=true` 时快速返回当前 Task；
- 相同 messageId 同 payload 返回原 Task；不同 payload 返回 InvalidArgument。

## 5.2 继续现有 Task

Message 携带已有 `taskId/contextId`。两者必须匹配且 Task 非终态，否则返回标准错误。不能通过传一个不存在 taskId 创建新 Task。

---

# 6. Streaming

## 6.1 SendStreamingMessage

- 响应为 SSE；
- 第一项为 Task 或规范允许的 Message；
- 后续为 `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent`；
- 保持生成顺序；
- 终态后关闭。

## 6.2 SubscribeToTask

- 第一项必须是订阅时的当前 Task；
- 只允许未终态 Task；
- 标准 Request 没有 replay cursor；
- 断线恢复：GetTask 后重新 Subscribe；
- `lastEventSequence` 只能通过声明的 A2AMesh 扩展使用。

## 6.3 SSE 帧

```text
data: <single StreamResponse ProtoJSON>

```

Keepalive：

```text
: keepalive

```

Keepalive 不是 Task 事件，不含 data/id，不增加 eventSequence。

响应头：

```http
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

---

# 7. GetTask

请求至少包含 Task ID；可使用规范字段控制 `historyLength`、`includeArtifacts`。

规则：

- 从 Redis 权威快照读取；
- 不因 Peer offline 直接返回 not found；
- Task 不存在或调用者无归属统一返回不泄露内部信息的 TaskNotFound；
- historyLength 有上限；
- 大 Artifact 只返回引用和元数据。

GetTask 可作为 SSE 断线校准，不应用于高频逐秒轮询。

---

# 8. ListTasks

支持规范字段，项目实现约束：

```text
contextId
state
targetAgentId（项目扩展过滤，若使用必须声明）
updatedAfter / updatedBefore
pageSize / pageToken
historyLength
includeArtifacts
```

- 默认 pageSize 50，最大 200；
- 排序 `(updatedMs DESC, taskId DESC)`；
- pageToken 是签名不透明字符串；
- 改变 filter 后复用 token 返回 InvalidArgument；
- 调用者归属由服务端注入，不接受请求覆盖；
- V1 无 tenant 参数。

---

# 9. CancelTask

```text
请求受理
→ State CAS cancelRequested
→ owner control message
→ Runtime process tree 退出
→ 返回 CANCELED Task
```

重复 Cancel 幂等。终态 Task 返回当前状态或 TaskNotCancelable（按官方 SDK 行为固定测试）。HTTP 请求超时不代表取消失败，调用方随后 GetTask。

---

# 10. Push Notification CRUD

## 10.1 Create

配置至少包含 Task ID 和 Webhook URL；服务端分配 config ID。只有 Card `pushNotifications=true` 时可用。

校验：

- Task 存在且归属允许；
- HTTPS（生产）；
- SSRF/DNS/redirect 检查；
- credential 类型受支持；
- 相同配置可通过客户端 config ID/幂等规则避免重复。

## 10.2 Get/List/Delete

- Get 不返回明文 credential；
- List 支持分页；
- Delete 幂等，删除后不得再创建新 delivery；
- 已在途请求可完成，但后续重试在发送前再次检查配置状态。

## 10.3 Webhook

```http
POST <configured-url>
Content-Type: application/a2a+json
X-A2A-Delivery-Id: delivery-...
X-A2A-Event-Sequence: 42
Authorization: <configured scheme>
```

body 是单个标准 `StreamResponse`。接收方 2xx 为成功；重复投递必须去重。

---

# 11. Extended Agent Card

仅当 public Card `extendedAgentCard=true` 时开放。V1 默认关闭。启用时：

- 必须按 public Card security 认证；
- 不同调用方返回范围必须可审计；
- 仍不得返回 NKey seed、内部 subject、绝对路径、任意 shell；
- 客户端可在会话期间替换 cached public Card。

---

# 12. Runtime Selection Extension

Message 声明 URI，Data Part 或 metadata 中使用规范 Schema：

```json
{
  "runtime": "hermes",
  "profile": "default",
  "workingDirectoryRef": "repo:a2amesh",
  "timeoutSeconds": 1800,
  "requestedTools": ["repository.read", "test.run"]
}
```

服务端策略优先。绝对路径、argv、env 和未注册工具返回 InvalidArgument/策略错误。

---

# 13. Progress Extension

结构见对象规范。接口要求：

- 使用标准 TaskStatusUpdateEvent；
- TaskState 保持官方值；
- phase/heartbeat/source/eventSequence 在扩展 Data；
- 标准客户端可忽略；
- 不发送思维链；
- heartbeat 默认不 Push 到外部 Webhook。

---

# 14. 内部 NATS 请求与响应

内部 Envelope 见 NATS 集成设计。公共字段：

```text
bindingVersion
protocolVersion
operation
requestId
callerAgentId（仅诊断，认证身份为准）
targetAgentId
sentAt/deadlineAt
replySubject
payload
```

响应：

```text
requestId
sequence
final
payload or error
```

内部 NATS Envelope 不能从公共 JSON-RPC 原样透传未经校验的 `replySubject`。

---

# 15. 错误标准

## 15.1 JSON-RPC

使用官方 SDK 对应错误类型和 JSON-RPC error 表示。稳定分类：

| 错误 | 场景 | Retry |
|---|---|---|
| InvalidArgument | 字段/状态/扩展非法 | 否 |
| VersionNotSupported | A2A-Version 非 1.0 | 协商后 |
| TaskNotFound | 不存在或不可访问 | 否 |
| TaskNotCancelable | 不能取消 | 否 |
| UnsupportedOperation | Card 未声明能力 | 否 |
| PushNotificationNotSupported | Push 未启用 | 否 |
| ExtendedAgentCardNotConfigured | 声明但未配置 | 否 |
| ExtensionSupportRequired | 必需扩展不支持 | 否 |
| TemporarilyUnavailable | NATS/Redis/target 暂不可用 | 有限重试 |
| InternalError | 未分类服务端错误 | 谨慎 |

## 15.2 脱敏

外部 message 不含：内部 URL/subject、Redis Key、栈、argv、workdir、Token、NKey、stdout 原文。详细诊断通过 requestId/traceId 查内部日志。

---

# 16. Idempotency

| 操作 | 幂等键 |
|---|---|
| SendMessage/Streaming | caller + target + messageId + payloadHash |
| CancelTask | taskId |
| Push config create | taskId + client config identity/normalized config hash |
| Push delete | taskId + configId |
| Card upsert | agentId + generation |
| Event projection | taskId + eventSequence |

`X-Request-Id` 只用于追踪，不替代业务幂等键。

---

# 17. Health 接口

Health 不是 A2A 核心操作：

- `/health/live`：进程事件循环可响应；
- `/health/ready`：Gateway 可访问 State Service/NATS 且可接受请求；
- 响应只给 `UP/DEGRADED/DOWN` 和组件分类，不暴露版本漏洞、地址或凭据；
- Peer 健康通过 presence/registry 查询，不开放 Windows HTTP 入站。

---

# 18. 验收用例

1. 官方 SDK 使用 Card URL 和 JSON-RPC URL 完成全部已声明操作。
2. 缺失/错误 A2A-Version 返回规范错误。
3. Card ETag/304 正确。
4. SendMessage 重试不重复执行。
5. ListTasks 游标无重复/遗漏，非法 token 拒绝。
6. Streaming 首帧、顺序、终态关闭正确。
7. Subscribe 断线按 GetTask 恢复，不依赖标准 cursor。
8. Push config 凭据不回显，Webhook SSRF/重复投递测试通过。
9. Runtime/Progress 扩展未声明时拒绝或忽略符合规则。
10. 所有错误均脱敏并可用 requestId/traceId 内部追踪。
