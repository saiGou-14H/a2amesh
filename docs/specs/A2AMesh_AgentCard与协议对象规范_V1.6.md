# A2AMesh Agent Card 与协议对象规范 V1.6
> 文档ID：`A2AM-PROTO-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：Agent Card、官方协议对象、扩展和字段语义
> 目标读者：协议、Gateway、后端、SDK、测试
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

本文档定义 A2AMesh 对 A2A v1 Agent Card、Message、Part、Artifact、Task、状态事件和扩展对象的标准语义与交换结构，确保 Gateway、Application Core、NATS Binding、State Service、Peer、官方 SDK 和测试使用同一数据契约。

官方 `a2a.proto` 与固定版本官方 SDK 是标准对象的唯一事实源。本文档只规定项目约束和扩展，不维护平行的“类 A2A”模型。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 A2A v1 对象、Agent Card、Progress/Runtime 扩展和校验规则 |
| V1.1 | 2026-08-14 | 冻结每Agent Card路由、JSON-RPC/gRPC接口、Bearer、扩展协商及MCP发布边界 |
| V1.2 | 2026-08-14 | 冻结官方tenant空值规则、跨Binding身份与安全对象语义 |
| V1.3 | 2026-08-14 | 补齐交付剖面发布、Card单一发布者、能力授权与组件兼容元数据规则 |
| V1.4 | 2026-08-14 | 明确 Artifact blob 和 Card publisher 配置治理的权威边界及交叉引用 |
| V1.5 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，协议对象合同不变 |
| V1.6 | 2026-08-14 | 闭合 G0：公开/私有 Card 边界、完整 Task 迁移、Cancel 与扩展发布规则 |

### 1.2 规范基线

| 项 | 值 |
|---|---|
| A2A Specification release | v1.0.1 |
| 协商版本 | `1.0` |
| Python SDK 验证基线 | `a2a-sdk==1.1.2` |
| JSON 表示 | 官方 ProtoJSON |
| 时间 | RFC 3339/ISO 8601 UTC 或带偏移 |
| ID | 不透明字符串，由服务端生成 Task ID |

升级 SDK 前必须运行完整 fixture 和官方客户端黑盒，不得仅修改依赖范围。

---

## 2. 通用约束

1. 外部标准 JSON 使用官方 ProtoJSON 字段名，通常为 `lowerCamelCase`。
2. 枚举使用官方名称，如 `TASK_STATE_WORKING`、`ROLE_AGENT`。
3. 新 Task ID 由服务端生成；客户端通过 `message.messageId` 实现请求幂等。
4. `contextId` 是逻辑上下文，不是 tenant、用户或目录。
5. Message 用于交流和状态说明；最终结果用 Artifact。
6. 任意扩展必须使用受控 URI，在 Agent Card 声明，并允许标准客户端忽略非必需扩展。
7. Card、Message metadata 不得携带 NKey seed、Redis Key、绝对 workdir、原始 shell 命令或凭据。
8. 不输出原始 Chain-of-Thought；仅输出高层进度摘要。
9. 当前 V1 为单 Mesh，不在协议对象增加 `tenantId`；内部隔离使用部署配置 `mesh_id` 与 NATS account。

---

## 3. Agent Card

### 3.1 发现地址

生产 V1 冻结为每 Agent 通配子域名：

```text
GET https://<agentId>.agents.<baseDomain>/.well-known/agent-card.json
```

同一虚拟主机的标准 JSON-RPC 地址固定为 `https://<agentId>.agents.<baseDomain>/a2a`。Gateway 根据 Host 路由，但不是调度主节点；Peer 间调用直接走 NATS。Registry/直接配置可作为额外发现方式，但不得改变 Card 中的标准 URL。

### 3.2 必要字段

| 字段 | 规则 |
|---|---|
| `name` | 人类可读且稳定，不用主机临时名替代 |
| `description` | 描述业务能力，不暴露内部命令和目录 |
| `supportedInterfaces[]` | 只声明已通过测试的 Binding，按偏好排序 |
| `version` | Agent 能力版本，不随 heartbeat 变化 |
| `capabilities` | streaming、pushNotifications、extendedAgentCard、extensions |
| `defaultInputModes` | 如 `text/plain`、`application/json` |
| `defaultOutputModes` | 与实际 Artifact/Message 能力一致 |
| `skills[]` | 技能 ID、名称、说明、tags、examples、输入输出模式 |
| `securitySchemes` | 生产 V1 固定声明 `meshBearer` HTTP Bearer；仅隔离测试环境可显式关闭 |
| `securityRequirements` | 生产 V1 固定要求 `meshBearer`；它是部署级认证，不是 RBAC |
| `signatures` | V1 可选；声明支持后必须验证 JWS |

### 3.3 标准 Card 示例（INTEROP）

```json
{
  "name": "A2AMesh Windows Development Agent",
  "description": "Executes repository analysis, code changes and tests through approved local runtimes.",
  "supportedInterfaces": [
    {
      "url": "https://windows-a.agents.example.com/a2a",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    },
    {
      "url": "https://windows-a.agents.example.com/a2a/grpc",
      "protocolBinding": "GRPC",
      "protocolVersion": "1.0"
    }
  ],
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "extendedAgentCard": false,
    "extensions": [
      {
        "uri": "https://a2amesh.dev/extensions/runtime-selection/v1",
        "description": "Requests an approved local runtime.",
        "required": false
      },
      {
        "uri": "https://a2amesh.dev/extensions/execution-progress/v1",
        "description": "Structured task phase, heartbeat and progress updates.",
        "required": false
      }
    ]
  },
  "securitySchemes": {
    "meshBearer": {
      "httpAuthSecurityScheme": {
        "description": "Deployment-scoped A2AMesh bearer token over TLS.",
        "scheme": "Bearer",
        "bearerFormat": "opaque"
      }
    }
  },
  "securityRequirements": [
    {"schemes": {"meshBearer": {"list": []}}}
  ],
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "skills": [
    {
      "id": "repository-engineering",
      "name": "Repository Engineering",
      "description": "Analyzes and modifies an approved repository and runs its verification commands.",
      "tags": ["code", "test", "repository"],
      "examples": ["Run tests and fix the failing implementation."],
      "inputModes": ["text/plain"],
      "outputModes": ["text/plain", "application/json"]
    }
  ]
}
```

示例中的域名是占位符。公网标准 Card 只发布标准客户端可认证并通过交付门禁的 JSON-RPC/gRPC interface；私有 NATS route、NKey 和内部 Binding minor 只存在于受认证 Mesh Registry metadata，不进入公开 Card。生产 Card 由受信配置生成，不硬编码；Runtime 自报信息只能作为健康输入，不能直接扩展公开 Skill/Interface。

### 3.4 Card 生命周期

```text
active signed config generation 变化
→ 允许候选获取 publisher lease/fencing
→ 生成 canonical Card
→ 官方 SDK 解析校验
→ 计算 content hash/ETag
→ State Service upsert_card(cardGeneration, configGeneration, fencingToken)
→ 发布 CardChanged 事件
→ Gateway 缓存失效
```

规则：

- heartbeat 只更新 presence，不更新 Card；
- 每个 `agentId` 的 publisher 候选以稳定 `publisherPrincipal/nodeId` 来自 active signed bundle；启动时生成的临时 instanceId 必须绑定该稳定身份。Redis lease/fencing 选出唯一 active publisher，其他 instance 只更新自身 presence；
- `generation` 单调递增，旧实例不能覆盖新 Card；
- Card 必须记录来源 `configGeneration/contentSha256`；旧 config generation、过期 lease 或旧 fencing token 一律拒绝；
- ETag 由去除 `signatures` 后的规范内容计算；
- Gateway 支持 `If-None-Match`/304；
- Extended Card 可按部署级认证返回更详细技能，但 V1 默认关闭；
- JWS 签名按 RFC 8785 + RFC 7515，私钥不放 Redis。
- Card 只能发布当前交付剖面和对应语义门禁已通过的 interface/capability；`CORE` 可只声明 JSON-RPC/SSE，gRPC 仅在 `INTEROP` 门禁通过后追加，MCP 永远不作为 A2A Binding 声明。
- 11 个官方操作由共享 Core 给出确定性处理；未启用 Push/Extended capability 时对应方法返回标准“不支持”，不得因方法存在就把 capability 标为 true。

### 3.5 Gateway 虚拟路由与认证

- 通配 DNS/证书必须覆盖 `*.agents.<baseDomain>`；
- Host 中的 `agentId` 必须符合 `^[a-z0-9][a-z0-9-]{0,62}$`，禁止点号、路径字符和大小写混淆；
- Gateway 读取 `GetAgentCard` 结果并验证 Card 的第一标准接口与当前 Host 一致；
- Agent offline 时 Card 仍可返回，但请求路由返回可重试的系统不可用；明确 unregister/tombstone 后才停止发现；
- 生产请求必须携带 `Authorization: Bearer <opaque-token>`；轮换允许短暂双 key 窗口；
- Bearer Credential 每客户端独立，仅证明属于当前 Mesh；它提供稳定机器 Principal，但不提供用户、角色或资源权限。

### 3.6 gRPC 与 MCP 发布边界

- 已通过语义套件的 gRPC 必须作为 `protocolBinding="GRPC"` 的独立 `AgentInterface` 发布；URL 固定为 `https://<agentId>.agents.<baseDomain>/a2a/grpc`；
- JSON-RPC 与 gRPC interface 使用同一 Agent version、capabilities、skills、securityRequirements 和 Task 语义；
- Bearer 在 gRPC 中通过 lowercase metadata `authorization` 发送；`a2a-version`、`a2a-extensions` 同理；
- MCP 不是 A2A Binding，**不得**把 MCP endpoint 填入 `supportedInterfaces`；
- 公网 MCP Bridge 在 `https://mcp.<baseDomain>/mcp` 单独发现和初始化，按 MCP 2026-07-28 声明 tools/resources；
- 如需在 Gateway Agent Card 提示 MCP Bridge，只能使用非必需扩展 URI `https://a2amesh.dev/extensions/mcp-bridge/v1`，标准客户端忽略后不影响 A2A。

### 3.7 官方 tenant 字段的 V1 行为

A2A v1 官方 `AgentInterface` 及部分请求对象包含可选 `tenant`。A2AMesh 保留官方字段的解析/序列化能力，但业务规则固定为：

- 所有发布的 `supportedInterfaces[].tenant` 均省略或为空字符串；
- JSON-RPC/HTTP 请求中非空 tenant 使用 invalid params/HTTP 400 拒绝；
- gRPC 请求中非空 tenant 返回 `INVALID_ARGUMENT`；
- NATS 自定义 Envelope 不新增 tenant 字段；
- `mesh_id` 仅是部署命名空间，绝不映射到官方 tenant；
- 非空 tenant 在创建 Task、写 dedupe 或 dispatch 前拒绝，不能产生副作用。

### 3.8 Credential 与 Canonical Principal

Card 的 `meshBearer` 只声明认证方案，不包含 Principal。生产环境为每个机器调用方签发独立 `credentialId + opaque token`；Gateway 验证 token 后生成 `a2a:<credentialId>`。NATS NKey 生成 `agent:<agentId>`，MCP OAuth client_credentials 生成 `mcp:<issuerHash>:<clientId>`。

Principal alias 只能由部署配置显式声明；不得从 `name`、Card、IP、Host 或 Token 内容推断。外部 Message/metadata 中出现 `callerPrincipal`、`credentialId` 或 `authContext` 时一律忽略/拒绝，真实值由入口注入。

---

## 4. Agent Skill

每个 Skill 必须满足：

| 字段 | 约束 |
|---|---|
| `id` | Card 内唯一、稳定、kebab-case |
| `name` | 面向调用方的能力名 |
| `description` | 输入、结果和限制，不写营销语言 |
| `tags` | 用于检索；小写稳定词汇 |
| `examples` | 1～5 个真实请求示例，不包含敏感数据 |
| `inputModes/outputModes` | 不得超出 Runtime 实际能力 |

不要把每个内部 Tool 都暴露成 Skill。Skill 是业务能力，Tool 是执行细节。

---

## 5. Message 与 Part

### 5.1 Message

Message 至少包含：

- 服务端或调用方生成的唯一 `messageId`；
- `role`；
- 一个或多个 `parts`；
- 可选 `taskId/contextId`；
- 使用扩展时列出 `extensions`。

幂等键：

```text
canonicalPrincipal + targetAgentId + message.messageId
```

State Service 同时保存 payload hash。同 messageId、同 hash 返回原 Task；同 messageId、不同 hash 返回 InvalidArgument。

### 5.2 Part

允许：

- 文本；
- 文件引用或受控 URI；
- JSON/Data；
- 规范支持的媒体类型。

限制：

- 单个内联 Part 默认不超过 1 MiB；
- 大文件按《Artifact 与对象存储设计》先创建上传会话、验证完成，再使用稳定受控 URI；
- signed URL 只用于短期传输，不能写入 Part、Task、Redis、日志或审计；
- 不把未脱敏 stdout 或环境变量放入 Part。

---

## 6. Task

### 6.1 字段语义

| 字段 | 规则 |
|---|---|
| `id` | 服务端生成，不透明字符串 |
| `contextId` | 服务端生成或校验已有 Context |
| `status` | 官方 `TaskStatus`，含 state/timestamp/可选 message |
| `history` | 按 `historyLength` 返回；不保证保存所有瞬时进度消息 |
| `artifacts` | 业务输出；是否返回由请求配置控制 |
| `metadata` | 仅保存安全、稳定、声明过的项目扩展 |

### 6.2 标准状态机

| From | To | 说明 |
|---|---|---|
| SUBMITTED | WORKING | dispatch ACCEPTED 且 owner lease 有效 |
| SUBMITTED | CANCELED / FAILED / REJECTED | 未启动取消、dispatch 失败、目标业务拒绝 |
| WORKING | INPUT_REQUIRED / AUTH_REQUIRED | 标准交互等待 |
| INPUT_REQUIRED / AUTH_REQUIRED | WORKING | 合法输入恢复 |
| WORKING / INPUT_REQUIRED / AUTH_REQUIRED | COMPLETED / FAILED / CANCELED / REJECTED | 按执行、取消、副作用和业务结果提交 |

终态：completed、failed、canceled、rejected。终态后不能继续向 Task 发送新 Message。

内部 `cancel_requested`、`recovering`、`stalled` 不是官方 TaskState，只能作为内部字段或 Progress Extension phase。

### 6.3 状态不变量

1. 状态迁移必须带 expected version 和 lease fencing token。
2. 终态不可被 late event 覆盖。
3. Task status timestamp 单调不回退。
4. Task 与 Context 关系不可在执行中更换。
5. Artifact 属于确定 Task，不允许跨 Task 覆盖。
6. 多 Binding 对同一 Task 返回功能等价对象。

---

## 7. Artifact

Artifact 是任务结果，例如补丁、报告、结构化分析或文件引用。

| 规则 | 说明 |
|---|---|
| 稳定 ID | 同一 Artifact 增量更新保持 ID |
| 多 Part | 文本说明、JSON 摘要、文件引用可组合 |
| 流式更新 | 使用标准 `TaskArtifactUpdateEvent` |
| 大对象 | 写 Object Store，Artifact 放 URI、size、hash、mediaType |
| 最终性 | 终态前可为 partial；终态后快照不可被旧 attempt 覆盖 |
| 运行日志 | 默认不是 Artifact，进入受控日志/观测通道 |

本节只定义 A2A 对象语义。blob、object key、`PENDING_UPLOAD/AVAILABLE/QUARANTINED/DELETING/DELETED/FAILED`、上传完成、下载票据、删除、孤儿清理和恢复的唯一权威合同见《Artifact 与对象存储设计》。只有 `AVAILABLE` 元数据可进入对外 Task Artifact；signed URL 永远不是稳定 Artifact URI。

---

## 8. 流式事件

标准事件：

- `TaskStatusUpdateEvent`；
- `TaskArtifactUpdateEvent`；
- `StreamResponse` 中规范允许的 Task/Message/Event。

要求：

- 生成顺序不可重排；
- 多订阅者收到等价序列；
- 首帧遵循对应操作规范；
- 终态关闭 stream；
- 重要事实必须写 Task/Artifact 快照，不能只存在瞬时 Message；
- A2A v1 标准订阅无客户端 replay cursor。

---

## 9. Runtime Selection Extension

URI：

```text
https://a2amesh.dev/extensions/runtime-selection/v1
```

Schema：

```json
{
  "runtime": "hermes",
  "profile": "default",
  "workingDirectoryRef": "repo:a2amesh",
  "timeoutSeconds": 1800,
  "requestedTools": ["repository.read", "repository.patch", "test.run"]
}
```

约束：

- `runtime` 必须在目标 Card/Peer 允许列表；
- `workingDirectoryRef` 是服务端配置别名，不是调用者提供的绝对路径；
- `requestedTools` 只能缩小权限，不能扩大 Peer 本地策略；
- 标准客户端不发送扩展时使用默认 Runtime；
- 不允许通过扩展传任意 argv、环境变量或 shell。

---

## 10. Execution Progress Extension

URI：

```text
https://a2amesh.dev/extensions/execution-progress/v1
```

### 10.1 扩展协商

客户端请求使用标准服务参数：

```http
A2A-Extensions: https://a2amesh.dev/extensions/execution-progress/v1
```

多个 URI 使用逗号分隔。Runtime Selection 同样通过 `A2A-Extensions` 声明。非必需扩展未声明时服务端回退标准行为；若未来 Card 将某扩展标记 `required=true` 而客户端未声明，返回 `ExtensionSupportRequiredError`。

放置于标准 `TaskStatusUpdateEvent.status.message`：

```json
{
  "taskId": "task-123",
  "contextId": "ctx-123",
  "status": {
    "state": "TASK_STATE_WORKING",
    "timestamp": "2026-08-14T02:30:00Z",
    "message": {
      "messageId": "progress-42",
      "role": "ROLE_AGENT",
      "extensions": [
        "https://a2amesh.dev/extensions/execution-progress/v1"
      ],
      "parts": [
        {
          "mediaType": "application/vnd.a2amesh.progress+json",
          "data": {
            "phase": "tool_running",
            "summary": "正在运行测试",
            "stepId": "step-test",
            "tool": "pytest",
            "progress": {"current": 34, "total": 120, "unit": "tests"},
            "attempt": 1,
            "heartbeat": false,
            "source": "runtime_reported",
            "eventSequence": 42
          }
        }
      ]
    }
  }
}
```

### 10.2 Phase

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

`thinking` 不作为标准 phase 输出。无法确认模型内部状态时使用 `runtime_running`。

### 10.3 约束

- `source` 为 `runtime_reported` 或 `supervisor_inferred`；
- 总量未知时省略 determinate progress；
- `heartbeat=true` 表示存活事件，不表示阶段变化；
- `eventSequence` 只在 A2AMesh 增强路径用于服务端排序、去重和水位检测；V1.6 不提供客户端 replay cursor；
- Progress 是非必需扩展，官方客户端忽略后仍能处理 TaskState。

---

## 11. 错误对象

错误使用官方 A2A Error 类型和对应 Binding 表示。项目扩展诊断只放内部日志，外部响应包含稳定错误类型、脱敏 message 和可选 retry guidance。

必须覆盖：

```text
TaskNotFoundError
TaskNotCancelableError
PushNotificationNotSupportedError
UnsupportedOperationError
ContentTypeNotSupportedError
InvalidAgentResponseError
ExtendedAgentCardNotConfiguredError
ExtensionSupportRequiredError
VersionNotSupportedError

```

字段或状态非法使用底层 Binding 的 invalid request/invalid params 语义；临时基础设施失败使用 HTTP 503、gRPC `UNAVAILABLE` 或 JSON-RPC system error，不伪造成 A2A 专用错误名。

调用方身份无效、Task 不存在和不可访问在 V1 单 Mesh 下仍不得泄露凭据或内部路由。

---

## 12. 校验与生成规则

- `src/a2amesh/protocol/types.py` 只 re-export 官方类型；
- Card builder 使用官方类型构造并序列化；
- 测试 fixture 从官方 Proto/SDK 生成；
- 项目自有 Schema 仅放 `schemas/extensions/`；
- CI 对 Card、Message、Task、Artifact、事件做 parse → serialize → parse；
- 未知标准字段按 SDK 规则处理，不在手写 Pydantic 中静默丢失；
- 每个 Card `supportedInterfaces` 都运行同一语义测试。
- Card builder 输入来自已签名且 active 的受信配置；同一 agentId 的 publisher ownership、config/card generation、fencing token 和目标 delivery profile 必须通过启动校验。完整生命周期以《受信配置与变更治理设计》为准。

---

## 13. 验收用例

1. 官方 SDK 解析 public Card 和 Progress Extension Card。
2. Card 缺少必要字段时启动失败，不发布半合法 Card。
3. heartbeat 不改变 Card version/ETag。
4. 同 messageId 同 payload 返回同 Task；不同 payload 拒绝。
5. 非法 TaskState 和终态回退被拒绝。
6. Runtime Selection 不能传绝对路径、任意 argv 或越权 Tool。
7. Progress Event 可被官方 SDK 解析，忽略扩展后仍是合法 `TASK_STATE_WORKING`。
8. **TEST-IDENTITY-001**：三类 Credential 映射、alias 和伪造 caller 字段被拒绝。
9. **TEST-TENANT-001**：Card 不发布 tenant，非空 tenant 在任何副作用前拒绝。
10. **TEST-ARTIFACT-001**：大 Artifact 不进入 Redis，只有完成 size/SHA-256/mediaType 校验的 AVAILABLE 元数据可附加 Task。
11. 日志和协议对象不包含 NKey seed、Token、环境变量或思维链。
12. v0.3 `message/send` fixture 不能被误当 v1 成功门禁。
13. **TEST-CARD-OWNER-001**：双 instance、lease 过期和旧实例恢复时只有 active generation 的最新 fencing publisher 可更新 Card，presence 仍分别可见。
14. **TEST-CARD-PROFILE-001**：以同一Agent依次构造CORE、INTEROP、EXTENDED candidate generation。CORE public Card只能声明JSON-RPC/SSE及已交付标准能力，gRPC interface、Push capability和任何MCP interface均absent；仅修改bundle声明而缺`TEST-GRPC-001`/Push门禁报告、required READY或有效GateEvidenceRecord时，Card publisher必须NACK且既有public bytes不变。INTEROP全部门禁PASS/0-skip并激活后，官方SDK解析的Card才可新增gRPC interface及实际Push capability；EXTENDED的MCP仍通过独立发现，任何generation都不得把MCP伪装为A2A interface。降级/回滚使用更高generation并重新门禁，旧Card缓存按generation/etag失效；声明集合与实际监听/官方黑盒逐项一致才PASS。
---

## 14. G0 对象与发布冻结合同

1. public Card 不发布私有 NATS interface/NKey；内部 Registry Card 仅向已认证 Peer 返回 route、Binding schema minor 和 capability 摘要。
2. CORE 对 Task 六操作提供 JSON-RPC/SSE 成功路径；Push 四操作在 capability=false 时返回 `PushNotificationNotSupportedError`；Extended Card 未启用时返回标准不支持错误。
3. `CANCELED` 的重复 Cancel 返回当前 Task；`COMPLETED/FAILED/REJECTED` Cancel 返回 `TaskNotCancelableError`。
4. 终态人工重试创建新 Task，通过 metadata 关联旧 Task；不能迁出终态。
5. Progress Extension 不定义客户端 replay cursor，重要事实必须进入 Task/Artifact 快照。

验收增加：

- **TEST-CARD-PUBLIC-001**：以同一Agent生成一份经官方SDK校验的public Agent Card和一份仅存私有Registry的route metadata。递归检查public Card canonical ProtoJSON、HTTP Agent Card响应、JSON-RPC discovery和缓存副本：只允许剖面声明的标准接口/扩展，不得出现私有NATS Binding URI、subject/stream/consumer、NKey/credentialId/secretRef/token、内网地址、instanceId或内部capability route；字符串值与字段名都扫描。向name/description/skills/extensions/unknown field分别注入上述私有标记，publisher必须在网络发送/缓存前拒绝并写脱敏审计。正例同时断言已认证Peer通过`card.get`取得的public Card仍无私有字段，而独立Registry metadata可按授权返回NATS route/schema minor；未认证caller不可取得该metadata。全部正反例通过且public bytes由官方SDK重新解析一致才PASS。
- **TEST-TASK-STATE-001**：覆盖全部Task状态迁移、终态不可迁出、Cancel幂等与不可取消错误矩阵。
- **TEST-PROFILE-OPS-001**：覆盖CORE/INTEROP/EXTENDED剖面×11操作×capability正反例，未启用能力返回对应标准不支持错误。

---

## 15. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [A2A协议与NATS集成适配设计 V1.6](A2AMesh_A2A协议与NATS集成适配设计_V1.6.md)
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
- [MCP Specification 2026-07-28 Release](https://github.com/modelcontextprotocol/modelcontextprotocol/releases/tag/2026-07-28)
