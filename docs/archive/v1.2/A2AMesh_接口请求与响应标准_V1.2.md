# A2AMesh 接口请求与响应标准 V1.2
> 文档ID：`A2AM-API-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：公共A2A、SSE、Push、错误、扩展和内部接口
> 目标读者：Gateway、SDK、联调、测试、安全
> 评审状态：文档自检通过；JSON-RPC/gRPC/MCP黑盒待完成
> 最后更新：2026-08-14
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 替代版本：V1.1
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh V1 的公共 Agent Card、标准 A2A JSON-RPC/SSE 与 gRPC、Push Webhook、MCP Bridge、内部 NATS Binding、公共报文、错误、幂等、分页和扩展接口。接口对象以官方 A2A v1 Proto/SDK 为准，本文档不复制完整 Proto 字段表。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 A2A v1.0 Gateway、11 操作、SSE、Push、NATS Envelope、错误和示例契约 |
| V1.1 | 2026-08-14 | 补齐A2A-Extensions、JSON-RPC/gRPC双Binding、MCP Bridge、官方错误码与固定路由 |
| V1.2 | 2026-08-14 | 补齐身份归一、MCP submit schema、OAuth discovery和tenant拒绝行为 |

## 1.2 接口状态

当前代码只实现私有 NATS 风格方法，本文标准 Gateway 为目标设计。在官方 SDK 黑盒通过前，不得将本接口标记为已上线。

---

# 2. 基础约定

## 2.1 地址

```text
Agent Card:   GET  https://<agentId>.agents.<baseDomain>/.well-known/agent-card.json
JSON-RPC:     POST https://<agentId>.agents.<baseDomain>/a2a
gRPC:              https://<agentId>.agents.<baseDomain>/a2a/grpc
SSE:          由 SendStreamingMessage / SubscribeToTask 返回
MCP:          POST https://mcp.<baseDomain>/mcp
Health live:  GET  https://mesh.<baseDomain>/health/live
Health ready: GET  https://mesh.<baseDomain>/health/ready
```

生产 V1 使用通配 DNS/证书。Gateway 从 Host 解析 agentId 并调用内部 `GetAgentCard`；真实 JSON-RPC URL 必须与 Agent Card 第一项标准 `supportedInterfaces[].url` 一致。根域不代表主 Agent。

## 2.2 请求头

| Header | 规则 |
|---|---|
| `A2A-Version` | 标准请求必须为 `1.0`；空值按规范视作旧版本并返回 `VersionNotSupportedError` |
| `A2A-Extensions` | 客户端希望使用的扩展 URI，多个值在一个 Header 中逗号分隔 |
| `Content-Type` | `application/json` 或规范要求的 A2A media type |
| `Accept` | JSON 或 `text/event-stream` |
| `Authorization` | 生产固定 `Bearer <credentialId>.<random-secret>`，每客户端独立 Credential；仅隔离测试可关闭 |
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

## 2.5 Gateway Host 路由

1. TLS/SNI 与 Host 必须匹配 `*.agents.<baseDomain>`；
2. agentId 只允许 `^[a-z0-9][a-z0-9-]{0,62}$`；
3. Gateway 调 `a2a.v1.state.card.get` 获取 Card/meta/presence；
4. Card 不存在或 tombstone：Agent Card 请求返回 404；JSON-RPC 返回不泄露内部信息的路由 not-found；
5. Card 存在但 offline：Card 仍可读取，操作请求返回 HTTP 503/系统 unavailable；
6. Gateway 只做标准协议适配和路由，Peer 间调用直接走 NATS。

## 2.6 A2A gRPC Binding

gRPC 使用官方 `a2a.v1.A2AService` 和 Proto 类型：

| RPC | 形态 | Request | Response |
|---|---|---|---|
| SendMessage | unary | SendMessageRequest | SendMessageResponse |
| SendStreamingMessage | server-streaming | SendMessageRequest | stream StreamResponse |
| GetTask | unary | GetTaskRequest | Task |
| ListTasks | unary | ListTasksRequest | ListTasksResponse |
| CancelTask | unary | CancelTaskRequest | Task |
| SubscribeToTask | server-streaming | SubscribeToTaskRequest | stream StreamResponse |
| CreateTaskPushNotificationConfig | unary | TaskPushNotificationConfig | TaskPushNotificationConfig |
| GetTaskPushNotificationConfig | unary | GetTaskPushNotificationConfigRequest | TaskPushNotificationConfig |
| ListTaskPushNotificationConfigs | unary | ListTaskPushNotificationConfigsRequest | ListTaskPushNotificationConfigsResponse |
| DeleteTaskPushNotificationConfig | unary | DeleteTaskPushNotificationConfigRequest | Empty |
| GetExtendedAgentCard | unary | GetExtendedAgentCardRequest | AgentCard |

metadata key 使用 lowercase：`a2a-version: 1.0`、`a2a-extensions`、`authorization: Bearer *** deadline/cancel 传给 Core，但传输取消不自动等同 `CancelTask`。反向代理按 TLS SNI/`:authority` 路由 agentId，并允许 HTTP/2 与 server streaming。

## 2.7 MCP Bridge

MCP endpoint 为 `https://mcp.<baseDomain>/mcp`，规范版本 `2026-07-28`，使用 Streamable HTTP；本地 Client 另支持 stdio。每个 HTTP JSON-RPC 消息独立 POST，必须校验 `Origin`、`MCP-Protocol-Version`、OAuth audience/resource、Accept 和大小。旧 HTTP+SSE 不作为新实现目标。

Bridge V1 只声明 tools/resources，映射 `mesh_list_agents`、`mesh_get_agent`、`mesh_submit_task`、`mesh_get_task`、`mesh_cancel_task` 及 `a2amesh://` 资源。MCP 与 A2A 使用独立认证令牌；不得把 A2A Bearer 透传给 MCP Server。

## 2.8 Canonical Caller Identity

| Binding | 客户端凭据 | 初始 Principal | 入口注入 |
|---|---|---|---|
| JSON-RPC/SSE | `meshBearer` opaque credential | `a2a:<credentialId>` | Gateway |
| gRPC | lowercase `authorization` metadata | `a2a:<credentialId>` | gRPC interceptor |
| NATS | NKey public identity + Envelope signature | `agent:<agentId>` | NATS Binding verifier |
| MCP | OAuth `iss + client_id` | `mcp:<issuerHash>:<clientId>` | MCP OAuth middleware |

随后由 State Service 应用可选、显式且不可改指的 alias，得到 Canonical Principal。请求 body/metadata 中的 `callerPrincipal`、`credentialId`、`authContext`、`ownerAgentId` 一律不是身份来源。Task ownership、Get/List/Cancel、幂等和审计只使用 Canonical Principal。

A2A Bearer 为每客户端独立 opaque credential，格式 `<credentialId>.<random-secret>`；Gateway 通过 credentialId 定位记录并常量时间验证 secret digest。单个全局共享 Token 不满足生产 V1。

## 2.9 官方 tenant 字段

- Agent Card 的所有 interface tenant 均为空；
- JSON-RPC 非空 tenant 返回 `Invalid params`（-32602）；
- gRPC 非空 tenant 返回 `INVALID_ARGUMENT`；
- 非空 tenant 必须在 Principal 解析、claim、Task ID 生成和 NATS dispatch 前失败；
- `mesh_id` 不作为请求字段，不映射 tenant；
- 空 tenant 继续由官方 SDK 正常解析/序列化。

## 2.10 MCP OAuth 端点

```text
Protected Resource Metadata:
  GET https://mcp.<baseDomain>/.well-known/oauth-protected-resource
Authorization Server Metadata:
  GET https://auth.<baseDomain>/.well-known/oauth-authorization-server
MCP Resource:
  https://mcp.<baseDomain>/
Required scope:
  a2amesh.invoke
Grant:
  client_credentials
```

Token 仅通过 `Authorization: Bearer` 发送，禁止 query token。Bridge 必须校验 issuer、resource audience、scope、时间、kid、RS256/ES256 签名；Token TTL 最大 900 秒。Authorization Server 不可用时不得 fail open。

---

# 3. Agent Card 接口

## 3.1 请求

```http
GET /.well-known/agent-card.json HTTP/1.1
Host: windows-a.agents.example.com
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
- 用 Canonical Principal/target/messageId/payloadHash claim；
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
- Task 不存在或调用者无归属统一返回不泄露内部信息的 `TaskNotFoundError`；
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
- V1 客户端必须省略 tenant 或发送空值；非空值按 §2.9 在查询前拒绝。

---

# 9. CancelTask

```text
请求受理
→ State CAS cancelRequested
→ owner control message
→ Runtime process tree 退出
→ 返回 CANCELED Task
```

重复 Cancel 幂等。终态 Task 返回当前状态或 `TaskNotCancelableError`（按官方 SDK 行为固定测试）。HTTP 请求超时不代表取消失败，调用方随后 GetTask。

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
Authorization: *** scheme>
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
authContext（入口生成的 Canonical Principal 与非敏感 Credential 元数据）
authProof（NKey Ed25519 签名）
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

使用官方 SDK 对应错误类型和 JSON-RPC error 表示。A2A v1.0.1 精确映射：

| A2A Error | JSON-RPC Code | gRPC Status | HTTP+JSON Status | 场景 | Retry |
|---|---:|---|---:|---|---|
| `TaskNotFoundError` | -32001 | NOT_FOUND | 404 | Task 不存在或不可访问 | 否 |
| `TaskNotCancelableError` | -32002 | FAILED_PRECONDITION | 400 | Task 不可取消 | 否 |
| `PushNotificationNotSupportedError` | -32003 | FAILED_PRECONDITION | 400 | Card 未声明 Push | 否 |
| `UnsupportedOperationError` | -32004 | FAILED_PRECONDITION | 400 | 操作/Streaming/终态订阅不支持 | 否 |
| `ContentTypeNotSupportedError` | -32005 | INVALID_ARGUMENT | 400 | Part/Artifact media type 不支持 | 否 |
| `InvalidAgentResponseError` | -32006 | INTERNAL | 500 | Agent 返回不符合当前操作规范 | 谨慎 |
| `ExtendedAgentCardNotConfiguredError` | -32007 | FAILED_PRECONDITION | 400 | 声明 Extended Card 但未配置 | 否 |
| `ExtensionSupportRequiredError` | -32008 | FAILED_PRECONDITION | 400 | required 扩展未通过 `A2A-Extensions` 声明 | 否 |
| `VersionNotSupportedError` | -32009 | FAILED_PRECONDITION | 400 | `A2A-Version` 非 1.0 | 协商后 |

字段/状态非法使用 JSON-RPC `Invalid params`（-32602）等底层错误。NATS/Redis/target 暂不可用是系统错误：HTTP 503、gRPC `UNAVAILABLE` 或 JSON-RPC system/internal error；不得创造 `TemporarilyUnavailableError` 冒充标准 A2A Error。

## 15.2 脱敏

外部 message 不含：内部 URL/subject、Redis Key、栈、argv、workdir、Token、NKey、stdout 原文。详细诊断通过 requestId/traceId 查内部日志。

---

# 16. Idempotency

| 操作 | 幂等键 |
|---|---|
| SendMessage/Streaming | Canonical Principal + target + messageId + payloadHash |
| MCP mesh_submit_task | Canonical Principal + targetAgentId + required messageId + canonical SendMessageRequest hash |
| CancelTask | taskId |
| Push config create | taskId + client config identity/normalized config hash |
| Push delete | taskId + configId |
| Card upsert | agentId + generation |
| Event projection | taskId + eventSequence |

`X-Request-Id` 只用于追踪，不替代业务幂等键。

MCP `mesh_submit_task` 的 `messageId` 是 required；MCP JSON-RPC id、HTTP request ID 和 progressToken 都不是业务幂等键。成功返回 `taskId/contextId/state/resourceUri/deduplicationResult`；同 id 不同 payload 返回 Tool execution error。

---

# 17. Health 接口

Health 不是 A2A 核心操作：

- `/health/live`：进程事件循环可响应；
- `/health/ready`：Gateway 可访问 State Service/NATS 且可接受请求；
- 响应只给 `UP/DEGRADED/DOWN` 和组件分类，不暴露版本漏洞、地址或凭据；
- Peer 健康通过 presence/registry 查询，不开放 Windows HTTP 入站。

---

# 18. 验收用例

- **TEST-A2A-001**：官方 SDK 经每 Agent Card/JSON-RPC URL 完成全部已声明操作。
- **TEST-GRPC-001**：官方 stub 经每 Agent gRPC interface 完成 11 RPC，unary/stream/deadline/cancel/status 等价。
- **TEST-MCP-001**：MCP 2026-07-28 stdio/Streamable HTTP、Origin、OAuth、tools/resources 与 Task handle 通过。
- **TEST-VERSION-001**：缺失/错误 `A2A-Version` 返回 `VersionNotSupportedError/-32009`。
- **TEST-EXT-001**：`A2A-Extensions` 多值、可选回退和 required error 正确。
- **TEST-REGISTRY-001**：Host→agentId→Card 路由、offline/tombstone 和 URL 一致性正确。
- **TEST-IDEMP-001**：SendMessage 重试不重复执行。
- **TEST-LIST-001**：ListTasks 游标无重复/遗漏，非法 token 拒绝。
- **TEST-STREAM-001**：Streaming 首帧、顺序、终态关闭和 GetTask 重连正确。
- **TEST-SEC-001**：Bearer、Push 凭据、SSRF、错误脱敏和 Trace 追踪通过。
- **TEST-ERROR-001**：九个 A2A 专用错误的名称、JSON-RPC Code 与 HTTP 映射逐项通过。
- **TEST-IDENTITY-001**：四入口 Principal 映射、alias、Bearer rotation、伪造身份字段和跨 Binding ownership 通过。
- **TEST-MCP-IDEMP-001**：MCP messageId required、并发/超时重试和 payload conflict 通过。
- **TEST-OAUTH-001**：Protected Resource/AS discovery、audience、scope、JWT 时间、JWKS rotation/outage 通过。
- **TEST-TENANT-001**：Card tenant 为空；JSON-RPC/gRPC 非空 tenant 分别返回 -32602/INVALID_ARGUMENT 且无副作用。
---

# 19. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.2](A2AMesh_业务与总体架构设计_V1.2.md)
- [AgentCard与协议对象规范 V1.2](A2AMesh_AgentCard与协议对象规范_V1.2.md)
- [A2A协议与NATS集成适配设计 V1.2](A2AMesh_A2A协议与NATS集成适配设计_V1.2.md)
- [Redis状态平面与数据设计 V1.2](A2AMesh_Redis状态平面与数据设计_V1.2.md)
- [任务生命周期与长任务运行时设计 V1.2](A2AMesh_任务生命周期与长任务运行时设计_V1.2.md)
- [编排器 Runtime与工具适配设计 V1.2](A2AMesh_编排器_Runtime与工具适配设计_V1.2.md)
- [统计审计与运行监控规则 V1.2](A2AMesh_统计审计与运行监控规则_V1.2.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
- [A2A v1.0.1 canonical Proto](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)
- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A Custom Protocol Bindings](https://a2a-protocol.org/latest/topics/custom-protocol-bindings/)
- [MCP Specification 2026-07-28 Release](https://github.com/modelcontextprotocol/modelcontextprotocol/releases/tag/2026-07-28)
- [MCP 2026-07-28 Streamable HTTP](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/2026-07-28/docs/specification/2026-07-28/basic/transports/streamable-http.mdx)
- [MCP 2026-07-28 Authorization](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/2026-07-28/docs/specification/2026-07-28/basic/authorization/index.mdx)
