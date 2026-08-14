# A2AMesh 编排器、Runtime 与工具适配设计 V1.5
> 文档ID：`A2AM-EXEC-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：编排、Runtime Adapter、Tool、MCP与Workspace
> 目标读者：Agent、Runtime、工具/MCP、后端、测试
> 评审状态：文档自检通过；四Runtime与MCP黑盒待完成
> 最后更新：2026-08-14
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 替代版本：V1.4
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh 编排器、Runtime Adapter、任务执行器、工具注册、MCP 接入、工作目录策略、并发容量、输出归一化和失败处理规则，使 Hermes、Codex、Claude Code、OpenCode 能在同一 A2A Task 模型下运行。

Task 生命周期由《任务生命周期与长任务运行时设计》负责；协议对象由《Agent Card 与协议对象规范》负责。本文只定义“如何规划、选择和执行”，不复制 Runtime 内部 Agent Loop。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立编排、Adapter、Tool Policy、MCP、工作目录、资源限制和验收规则 |
| V1.1 | 2026-08-14 | 补齐MCP 2026-07-28 Client/Server Bridge、Runtime验收ID和交叉引用 |
| V1.2 | 2026-08-14 | 补齐MCP messageId强制幂等与OAuth Authorization Server部署契约 |
| V1.3 | 2026-08-14 | 增加Principal能力授权、副作用适配器与有界公平准入合同 |
| V1.4 | 2026-08-14 | 统一 Runtime/Tool policy 配置 generation、Artifact 输出和 UNKNOWN 对账运维边界 |
| V1.5 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，Runtime 与 Tool 合同不变 |

## 1.2 当前实现

| 能力 | 当前状态 | 证据 |
|---|---|---|
| Planner/Dispatcher/Tracker/Aggregator | 已实现基础骨架 | `src/a2amesh/orchestrator/` |
| Runtime Adapter registry | 已实现 | `runtime/adapters/` |
| Hermes/Codex/Claude/OpenCode argv | 部分实现，版本需逐项验证 | 各 adapter 文件 |
| subprocess stdout/stderr/cancel | 部分实现 | `runtime/executor.py` |
| AgentRuntime NATS handler | 已实现私有协议 | `runtime/agent.py` |
| Tool registry/builtin/MCP connector | 部分实现 | `tools/` |
| TaskSupervisor/Progress/lease | 目标设计 | 尚无对应模块 |
| 官方 A2A 对象输出 | 目标设计 | 当前为项目 Pydantic 模型 |

---

# 2. 设计原则

1. Runtime Adapter 只负责本地 Runtime 差异，不实现 A2A 状态机。
2. 编排器产生计划，不直接拼接任意 shell。
3. TaskSupervisor 管理进程、lease、heartbeat、cancel；Adapter 不重复管理。
4. 工具采用本地 allowlist、schema 和 capability grant；V1 不建设用户/RBAC，但认证成功不等于有权调用任意 Agent/Skill/Tool/Workspace。
5. 工作目录使用配置别名，调用方不能传任意绝对路径。
6. Runtime CLI 版本和参数会漂移，启动时探测、CI fixture 和真机测试必须锁定。
7. 结构化 RuntimeEvent 优先；纯文本只能降级为 `runtime_running`。
8. 所有外部副作用必须声明重试安全等级。
9. 编排失败不应破坏已完成子任务事实。
10. Observer 建议与执行解耦，避免自动反馈环。
11. 所有任务在排队前执行有界准入；队列超限、等待超时和服务不可用使用不同错误。
12. 所有外部副作用通过 SideEffectAdapter 和持久 ledger 执行；`UNKNOWN` 不得自动重放。
13. Runtime/Tool/workspace/Artifact policy 只从同一 active signed config generation 读取，未知或过期 generation fail closed。
14. 大型输出必须经 Artifact Broker finalize；Runtime 本地路径、临时 signed URL 和未验证 blob 不能直接成为 Task Artifact。

---

# 3. 组件模型

```text
A2A Message/Task
   ▼
Orchestrator
 ├ Planner      → ExecutionPlan / Step DAG
 ├ Dispatcher   → target Agent + runtime + tool policy
 ├ Tracker      → subtask states / deadlines / attempts
 └ Aggregator   → final Message/Artifact
       │
       ▼
TaskSupervisor
 ├ RuntimeAdapter
 ├ ToolRegistry/MCP
 ├ CapabilityPolicy / AdmissionController
 ├ SideEffectAdapter
 ├ ArtifactClient
 ├ ProcessExecutor
 └ RuntimeEvent stream
```

---

# 4. ExecutionPlan

## 4.1 数据模型

```json
{
  "planId": "plan-01H...",
  "rootTaskId": "task-01H...",
  "objective": "Run tests, fix failures and produce a report.",
  "strategy": "sequential-with-parallel-analysis",
  "steps": [
    {
      "stepId": "inspect",
      "objective": "Inspect repository and test configuration.",
      "dependsOn": [],
      "requiredSkills": ["repository-engineering"],
      "preferredRuntime": "hermes",
      "workingDirectoryRef": "repo:a2amesh",
      "retryPolicy": "READ_ONLY_SAFE",
      "timeoutSeconds": 300
    },
    {
      "stepId": "test",
      "objective": "Run the baseline test suite.",
      "dependsOn": ["inspect"],
      "requiredSkills": ["repository-engineering"],
      "preferredRuntime": "codex",
      "retryPolicy": "WORKSPACE_CONTROLLED",
      "timeoutSeconds": 900
    }
  ]
}
```

## 4.2 计划约束

- Step ID 在 Plan 内唯一；
- DAG 无环；
- dependsOn 必须存在；
- 每个 Step 有 timeout、retryPolicy、结果契约；
- 默认最大 fan-out、深度和并发由配置限制；
- 计划中不放 NATS subject、secret、绝对路径或任意 argv；
- 修改同一 workspace 的 Step 默认串行；只读分析可并行；
- Plan 变更产生新 revision，已执行 Step 不被悄悄改写。

---

# 5. Planner

Planner 输入：

```text
root Message
Agent Card registry snapshot
runtime/tool availability
workspace aliases
capacity/health
policy limits
```

输出经过 deterministic validator：

1. schema 校验；
2. DAG 校验；
3. Skill/Runtime 是否存在；
4. workspace 是否允许；
5. retryPolicy 与副作用是否一致；
6. fan-out/depth/step 数是否超限；
7. deadline 总和是否合理。
8. Principal capability 是否允许目标 Agent、required skill、Tool risk 和 workspace alias。

LLM 产生的计划永远不能绕过 validator。

---

# 6. Dispatcher

## 6.1 Agent 选择

候选 Agent 必须同时满足：

- Card 包含 required skill；
- 支持所需 input/output mode；
- presence online；
- Runtime available；
- capacity 未满；
- workspace alias 存在；
- 本地工具策略允许。
- Canonical Principal 的有效 capability grant 覆盖 target Agent、operation/skill、Tool risk 和 workspace alias。

评分建议：

```text
score = skillMatch
      + runtimePreference
      + locality
      + availableCapacity
      - recentFailurePenalty
      - queuePenalty
```

同分使用稳定 agentId 排序，避免调度抖动。

## 6.2 分发

- 为每 Step 创建子 Task/Message；
- `contextId` 与根任务一致；
- metadata 记录 planId/stepId/rootTaskId，但不覆盖标准字段；
- 使用 stable messageId 实现重试幂等；
- dispatch 前调用 State `authorize_capability` 和 `admit_task`，拒绝时不创建执行 attempt；
- timeout 后只按 retryPolicy 重试；
- Step 结果通过 Artifact/Message 返回，不读 Peer 内部文件。

---

# 7. Tracker 与 Aggregator

## 7.1 Tracker

跟踪：

```text
PENDING / DISPATCHED / WORKING / WAITING / COMPLETED / FAILED / CANCELED / SKIPPED
```

这些是编排 Step 状态，不是 A2A TaskState。Tracker 从 Redis Task 快照和事件更新，不持有唯一内存事实。

## 7.2 失败策略

| 策略 | 行为 |
|---|---|
| FAIL_FAST | 任一关键 Step 失败，取消未开始依赖项 |
| CONTINUE_INDEPENDENT | 独立分支继续，最终部分成功 |
| RETRY_SAFE | 仅满足 retryPolicy 的 Step 重试 |
| REQUIRE_INPUT | 根 Task 进入 INPUT_REQUIRED |

## 7.3 Aggregator

- 验证所有必要 Step 终态；
- 按 Plan 顺序合并摘要，不按到达顺序；
- 保留 Artifact 来源 stepId/agentId/attempt；
- 冲突结果不静默覆盖，生成 conflict section；
- 大文件只聚合引用；
- 根 Task 终态由 Application Core 写入。

---

# 8. Runtime Adapter 契约

## 8.1 接口

```python
class RuntimeAdapter(Protocol):
    name: str

    async def probe(self) -> RuntimeProbe: ...
    def build_invocation(self, request: RuntimeRequest) -> RuntimeInvocation: ...
    async def parse_event(self, line: bytes, stream: str) -> RuntimeEvent | None: ...
    def classify_exit(self, result: ProcessResult) -> RuntimeOutcome: ...
```

数据：

```python
@dataclass(frozen=True)
class RuntimeRequest:
    prompt: str
    working_directory: Path
    timeout_seconds: int
    output_mode: str
    profile: str | None
    approved_tools: tuple[str, ...]

@dataclass(frozen=True)
class RuntimeInvocation:
    argv: tuple[str, ...]
    cwd: Path
    env_allowlist: Mapping[str, str]
    stdin: bytes | None
    structured_output: bool
```

## 8.2 Probe

启动时执行：

- executable 路径；
- `--version`；
- 关键 `--help` 解析；
- profile/config 可读性；
- 简单无副作用 smoke test；
- 结果写 presence/runtime health。

probe 失败不应导致整个 Peer 退出；对应 Runtime 标为 unavailable。

## 8.3 版本锁定

配置示例：

```yaml
runtimes:
  hermes:
    executable: hermes
    expected_version: "*"
    default_profile: default
  codex:
    executable: codex
    expected_version: ">=0.1"
  claude:
    executable: claude
  opencode:
    executable: opencode
```

实际 argv 由 Adapter 代码和该版本 fixture 决定；文档不承诺未验证参数永久稳定。

## 8.4 SideEffectAdapter

RuntimeAdapter 只处理 CLI/进程差异；会改变 workspace、系统或外部 provider 的动作还必须通过独立副作用契约：

```python
class SideEffectAdapter(Protocol):
    async def prepare(self, request: EffectRequest) -> PreparedEffect: ...
    async def apply(self, prepared: PreparedEffect) -> EffectResult: ...
    async def reconcile(self, effect: EffectRecord) -> EffectResult: ...
    async def compensate(self, effect: EffectRecord) -> EffectResult: ...
```

- `prepare` 生成稳定 effectId/requestHash/provider idempotency key，并先写 ledger；
- `apply` 只能在有效 lease/fencing 和 `APPLYING` 状态执行；
- `reconcile` 必须查询 provider 或本地不可变回执，不得根据异常类型猜测成功/失败；
- `compensate` 仅在 Tool 明确支持且 capability grant 允许时调用；
- adapter 缺少 reconciliation 能力时，timeout/断线统一落 `UNKNOWN` 并原子创建 case 转人工；Runtime/Adapter 无权自行裁决 case。完整运维流程见《人工对账与运维操作设计》。

---

# 9. ProcessExecutor

## 9.1 启动

- `asyncio.create_subprocess_exec`，禁止 `shell=True`；
- argv 每项独立传递；
- cwd 必须从 alias 解析且位于允许根目录；
- env 从最小 allowlist 构造；
- stdout/stderr 独立读取并限长；
- 设置进程组；
- 记录 pid 仅用于本机监控，不对外暴露。

## 9.2 输出

- 解码失败使用 replacement 并计数；
- 每行/块设置最大字节；
- 合并窗口限制事件频率；
- secret pattern 和路径脱敏；
- stderr 不自动等同失败，以 exit code/adapter outcome 为准；
- 最终结果从结构化输出优先提取。
- 超过 inline 上限的结果通过 ArtifactClient 创建上传会话并完成 size/SHA-256/media type finalize；成功前不提交稳定 Artifact，也不把本地绝对路径写入 Task。

## 9.3 取消

Linux：SIGTERM process group → grace → SIGKILL。Windows：CTRL_BREAK/Job Object → grace → terminate tree。必须 await process exit 和 pipe close。

---

# 10. Runtime 特定规则

## 10.1 Hermes

- 使用指定 profile；
- prompt 通过安全参数/stdin；
- 若 Hermes 暴露结构化事件则映射 Tool/Model phase；
- 不重复 source profile 环境；
- 远端不允许调用 Hermes 的消息发送能力绕过 A2AMesh 审计。

## 10.2 Codex / Claude Code / OpenCode

- 分别维护独立 Adapter 和版本 fixture；
- 非交互模式必须在无 PTY 环境验证；
- 若 CLI 必须 PTY，明确使用受控 PTY Executor；
- approval/sandbox 模式由 Peer 配置，调用者不能降低；
- 输出 JSONL 时优先结构化解析；
- 不把供应商内部事件直接暴露为 A2A 标准字段。

---

# 11. Workspace

## 11.1 Alias

```yaml
workspaces:
  repo:a2amesh:
    linux: /root/a2amesh
    windows: C:\\work\\a2amesh
    mode: read-write
    allowed_agents: [linux-main, windows-a]
```

V1 不建设用户权限，但 workspace 仍需静态本地 allowlist。

## 11.2 隔离

- resolve 后检查 realpath 位于根目录；
- 阻止 `..`、symlink escape、UNC/设备路径；
- 并发写使用 workspace lock；
- 高风险任务可复制 worktree/临时目录；
- 清理临时目录时校验 owner marker；
- 不允许调用方指定任意本机路径。

---

# 12. Tool Registry

## 12.1 Tool 描述

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    risk: Literal["READ_ONLY", "WORKSPACE_WRITE", "SYSTEM_WRITE", "EXTERNAL_SIDE_EFFECT"]
    retry_safe: bool
    supports_idempotency_key: bool
    supports_reconciliation: bool
    supports_compensation: bool
    timeout_seconds: int
```

## 12.2 策略

| 风险 | 默认 |
|---|---|
| READ_ONLY | 允许已注册工具 |
| WORKSPACE_WRITE | 仅允许 workspace 内，记录 diff/审计 |
| SYSTEM_WRITE | 默认拒绝 |
| EXTERNAL_SIDE_EFFECT | 默认拒绝或人工确认 |

Tool 输入必须 JSON Schema 校验。禁止通过一个 `shell(command: string)` 绕过工具粒度；如保留 shell，只允许固定模板或受控本地管理员模式，不公开成通用 Skill。

Tool registry、risk、timeout、workspace alias、SideEffectAdapter 和 Runtime 版本范围来自同一 active config generation。进程启动前再次核对 generation；配置撤销后不得沿用旧 grant/policy 启动新副作用。

## 12.3 重试

只有 `retry_safe=true`、`supports_idempotency_key=true` 且 ledger 已证明上一 attempt 未应用或 provider 返回同一结果时才自动重试。文件 patch、部署、发布、发送消息默认不可盲目重试；任何 `UNKNOWN` 必须先 reconcile。

---

# 13. MCP 接入

## 13.1 规范与传输基线

- MCP Specification 固定为 `2026-07-28`；Python SDK 固定为 `mcp==2.0.0`，升级需兼容性评审；
- MCP Python SDK v2 的高级 Server API 为 `mcp.server.mcpserver.MCPServer`，低级 API 为 `mcp.server.lowlevel.Server`；不得照搬 v1 旧教程中的 `mcp.server.fastmcp` 导入路径；
- Client 支持 `stdio` 和 `Streamable HTTP`；新实现不采用旧 HTTP+SSE transport；
- stdio 一行一个 UTF-8 JSON-RPC 消息，server stdout 禁止输出非 MCP 内容；
- Streamable HTTP 使用单一 `https://<host>/mcp` endpoint，每个消息独立 POST，必须发送 `MCP-Protocol-Version: 2026-07-28`；
- Server 校验 `Origin`，本地 HTTP 只绑定 loopback；生产公网 endpoint 使用 TLS 与 MCP OAuth 2.1。

## 13.2 MCP Client Connector

Peer 的 MCP Client 把已配置 Server 映射到 ToolRegistry：

- Server 只能由本地配置声明，远程请求不能临时注入 URL、command、env；
- 完成 initialize/capability negotiation 后再调用 `tools/list`、`resources/list`、`prompts/list`；
- Tool 名使用 `<serverAlias>__<toolName>` 命名空间，schema 缓存带 server generation；
- 对 Tool input/output JSON Schema、`x-mcp-header`、响应 media/size 做校验；
- 调用有 deadline、取消、输出上限、重启上限和审计；
- Streamable HTTP OAuth token 存 Secret Store，不透传 A2A Bearer 或调用方 Token；
- NAT 后的 MCP HTTP Server 只能由所在 Peer 本地访问，Windows 不开放公网入站。

## 13.3 MCP Server Bridge

公网 Linux 可运行 `mcp_gateway`，固定 endpoint：

```text
https://mcp.<baseDomain>/mcp
https://mcp.<baseDomain>/.well-known/oauth-protected-resource
```

Bridge 是 MCP OAuth 2.1 Resource Server，校验 token audience/resource；V1 只使用部署级 scope `a2amesh.invoke`，不建设用户、角色或 Permission Center。Server 声明 `tools` 与 `resources` capability，不声明 prompts/sampling/elicitation。

### 13.3.1 Authorization Server 部署契约

Authorization Server 是**外部部署依赖**，A2AMesh 不自行签发 Token。参考地址固定为 `https://auth.<baseDomain>`，生产配置必须提供：

```yaml
mcp_oauth:
  issuer: "https://auth.example.com"
  resource: "https://mcp.example.com/"
  required_scope: "a2amesh.invoke"
  grant_types: ["client_credentials"]
  allowed_algorithms: ["RS256", "ES256"]
  access_token_max_ttl_seconds: 900
  jwks_cache_seconds: 300
```

Bridge 在 `/.well-known/oauth-protected-resource` 发布 RFC 9728 metadata，并通过 issuer 的 RFC 8414/OIDC metadata 发现 token/JWKS endpoint。V1 只允许 machine-to-machine `client_credentials`，不实现用户登录、Authorization Code、refresh token 或动态权限。

Token 必须校验 `iss`、resource-bound `aud`、`exp/nbf/iat`、`client_id`、scope、`kid` 和签名算法。Principal 初值为 `mcp:<sha256(issuer)>:<client_id>`。AS/JWKS 暂不可用时：已缓存 key 只可验证尚未过期且 `kid` 已知的 Token；未知 `kid`、过期 cache 超出 15 分钟或新 Token 一律 503/401，绝不跳过签名。A2AMesh 不向下游转发该 Token。

## 13.4 MCP 到 A2A Core 映射

| MCP Tool/Resource | Core/State 操作 | 规则 |
|---|---|---|
| `mesh_list_agents` | `ListAgents/SearchAgents` | 稳定分页，只返回公开摘要 |
| `mesh_get_agent` | `GetAgentCard` | 返回公开 Card，不返回 NATS/secret |
| `mesh_submit_task` | `SendMessage(returnImmediately=true)` | 返回 `taskId,contextId,state,resourceUri` |
| `mesh_get_task` | `GetTask` | 校验 caller ownership；返回标准 Task 摘要 |
| `mesh_cancel_task` | `CancelTask` | 高风险；本地策略可禁用 |
| `a2amesh://agents/{agentId}/card` | public Card resource | ETag/version 元数据 |
| `a2amesh://tasks/{taskId}` | Task resource | 只读、调用者可见、脱敏 |

MCP Tool 不自动成为 A2A AgentSkill；只有显式配置、schema 审核和风险分类后才可双向发布。Bridge 不提供 `shell(command)`、任意文件 URI、绝对 workdir 或原始 stdout。

### 13.4.1 `mesh_submit_task` 输入契约

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["targetAgentId", "messageId", "text"],
  "properties": {
    "targetAgentId": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,62}$"},
    "messageId": {"type": "string", "minLength": 8, "maxLength": 128},
    "text": {"type": "string", "minLength": 1, "maxLength": 65536},
    "contextId": {"type": "string", "maxLength": 128},
    "runtime": {"type": "string", "enum": ["hermes", "codex", "claude", "opencode"]}
  }
}
```

`messageId` 由 MCP Client 生成并在网络重试时保持不变；MCP JSON-RPC `id`、HTTP request ID、progressToken 或 traceId 均不能代替。Bridge 使用验证后的 Canonical Principal、target、messageId 和规范 payload hash 调 `claim_message`。

payload hash 来自去除 OAuth Token、MCP request id、Trace/progress metadata 后的官方 `SendMessageRequest` ProtoJSON（包含 runtime extension），经 RFC 8785 规范化后 SHA-256。同 Principal/target/messageId/hash 返回原 Task；hash 不同返回 Tool execution error 且不启动 Runtime。

成功 structured result 固定包含 `taskId,contextId,state,resourceUri,deduplicationResult`，其中 deduplicationResult 为 `CREATED` 或 `DUPLICATE_SAME`。

## 13.5 长任务与取消

`mesh_submit_task` 必须快速返回 A2A Task handle，不保持 `tools/call` 数分钟。客户端通过 `mesh_get_task` 或 Task resource 查询；有 MCP progress token 时可发送不包含思维链的阶段通知，但 Redis/A2A Task 仍是权威状态。取消 MCP HTTP SSE 响应只取消当前 MCP request；取消后台 A2A Task 必须显式调用 `mesh_cancel_task`。

## 13.6 安全与故障

- Streamable HTTP 必须校验 Origin、OAuth audience/resource、协议版本、Content-Type/Accept 和请求大小；
- 远程 MCP Client 使用 URL allowlist、DNS/IP 重绑定防护、redirect 限制和连接超时；
- Server list-changed 只使缓存失效，不自动执行新 Tool；
- stdio server 异常退出按受控次数重启，关闭 stdin 后必须回收进程树；
- 任何 MCP 调用失败只改变当前 Tool/Task 状态，不绕过 lease/fencing 或自动重放未知副作用。

---

# 14. 容量与排队

配置：

```yaml
execution:
  max_concurrent_tasks: 4
  max_concurrent_runtime:
    hermes: 2
    codex: 2
    claude: 1
    opencode: 2
  max_plan_steps: 32
  max_fanout: 4
  max_plan_depth: 4
  max_queued_tasks: 64
  max_queued_tasks_per_principal: 8
  queue_deadline_seconds: 120
  max_request_bytes: 1048576
  max_inline_artifact_bytes: 262144
  max_context_bytes: 2097152
```

准入先检查 capability、请求/inline Artifact/context 大小、全局/Principal 队列和 Runtime 健康；通过后才进入公平队列。调度采用按 Principal 轮转或加权虚拟时间，再用 Runtime semaphore 控制实际并发。排队 Task phase=`queued`，队列长度、最老等待时间和每 Principal 占用进入监控；取消或 queue deadline 到期必须释放计数且不启动 Runtime。

错误口径：调用方超过自身队列/速率/大小上限返回 HTTP `429`、gRPC `RESOURCE_EXHAUSTED` 或对应 JSON-RPC overload data；全局执行服务、NATS、State 或目标 Runtime 不可用返回 HTTP `503`、gRPC `UNAVAILABLE` 或 system error。不得把所有过载都伪装成内部失败。

同一 workspace 的写任务使用 keyed lock；锁等待计入 deadline，避免死锁时无限挂起。

---

# 15. 观察者与编排干预

Observer 输出为建议对象：

```json
{
  "taskId": "task-...",
  "causeEventSequence": 42,
  "classification": "STALLED_TOOL",
  "recommendation": "cancel-and-request-human",
  "confidence": 0.91,
  "reasonSummary": "Tool phase exceeded configured threshold."
}
```

只有 deterministic policy 可把建议转换为 message/cancel/retry/reassign。Observer 不能直接调用 Dispatcher 或 Redis 写状态。

---

# 16. 失败矩阵

| 失败 | 行为 |
|---|---|
| Planner 输出非法 DAG | 拒绝计划，Task FAILED/INPUT_REQUIRED |
| 无满足 Skill 的 Agent | Task REJECTED 或等待策略 |
| Runtime probe 失败 | 标记 runtime unavailable，重新选 Agent |
| executable 启动失败 | Task FAILED，更新健康指标 |
| stdout 解析失败 | 降级文本并计数，不伪造阶段 |
| workspace 锁超时 | Task FAILED/可安全重试 |
| Tool schema 非法 | 调用前拒绝 |
| Tool 超时 | 取消 Tool/Runtime，按风险决定 Task |
| Capability grant 不匹配/过期 | 排队前拒绝，不产生副作用 |
| Principal 队列满/请求过大 | 429/RESOURCE_EXHAUSTED，附可重试提示 |
| 全局执行面不可用 | 503/UNAVAILABLE，不排队 |
| 副作用响应未知 | effect=UNKNOWN，Task reconciliation required，禁止自动重试 |
| Aggregator 冲突 | 生成冲突 Artifact，不静默选择 |
| Observer 循环 | causeEventSeq/冷却/次数限制阻断 |

---

# 17. 包结构

```text
src/a2amesh/
├── orchestrator/
│   ├── planner.py
│   ├── validator.py
│   ├── dispatcher.py
│   ├── tracker.py
│   ├── aggregator.py
│   └── policy.py
├── runtime/
│   ├── supervisor.py
│   ├── executor.py
│   ├── progress.py
│   ├── workspace.py
│   └── adapters/
├── tools/
│   ├── registry.py
│   ├── policy.py
│   ├── builtin/
│   └── mcp/
├── artifact/
│   └── client.py
└── observer/
    ├── consumer.py
    ├── rules.py
    └── policy.py
```

---

# 18. 验收用例

- **TEST-PLAN-001**：非法/循环 DAG、超 fan-out/depth 和不存在依赖在分发前拒绝。
- **TEST-DISPATCH-001**：Agent/Runtime/Skill/容量选择稳定可解释，offline/无容量候选被过滤。
- **TEST-RUNTIME-001**：Hermes、Codex、Claude Code、OpenCode 固定版本 probe/smoke 通过。
- **TEST-SEC-001**：argv 不使用 shell 拼接，workdir/symlink 无法逃逸 allowlist，未配置 MCP 不可注入。
- **TEST-LONG-001**：Runtime 静默仍有 heartbeat/cancel。
- **TEST-WORKSPACE-001**：workspace 并发写串行，只读可并行，lock timeout 可诊断。
- **TEST-TOOL-001**：Tool schema、风险和 retry_safe 生效，未知副作用不自动重试。
- **TEST-MCP-001**：stdio/Streamable HTTP initialize、tools/resources、schema、Origin、OAuth、取消、进程回收和长 Task handle 通过。
- **TEST-MCP-IDEMP-001**：缺 messageId 拒绝；同 messageId 同 payload 超时/并发重试返回同 Task；冲突 payload 不执行。
- **TEST-OAUTH-001**：RFC9728/RFC8414 discovery、client_credentials、issuer/audience/scope/TTL、JWKS rotation/outage 和 Token 不透传通过。
- **TEST-IDENTITY-001**：OAuth client_id 映射 Canonical Principal，伪造 principalId/credentialId 被忽略或拒绝。
- **TEST-AGG-001**：Aggregator 保留来源并显式报告冲突。
- **TEST-OBSERVER-001**：Observer 建议不能直接改变 Task，反馈环被阻断。
- **TEST-AUTHZ-001**：Principal/target/operation/skill/tool risk/workspace alias 任一不匹配均在排队前拒绝。
- **TEST-ADMISSION-001**：全局和 Principal 队列、queue deadline、大小上限、公平调度、取消计数回收及 429/503 映射通过。
- **TEST-EFFECT-001**：SideEffectAdapter 的 prepare/apply/reconcile/compensate 与 ledger 状态一致，UNKNOWN 不自动重试。
- **TEST-CONFIG-ATOMIC-001**：Runtime/Tool/workspace/Grant 使用同一 active generation，过期/撤销/漂移在进程或副作用前拒绝。
- **TEST-ARTIFACT-ATOMIC-001**：大型 Runtime 输出完成对象存储校验后才附加 Task，本地路径和 signed URL 不进入协议对象。
- **TEST-RECON-AUTHZ-001**：产生 UNKNOWN 的 Runtime instance 不能使用业务 Credential 自行 resolve case。
---

# 19. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.5](A2AMesh_业务与总体架构设计_V1.5.md)
- [AgentCard与协议对象规范 V1.5](A2AMesh_AgentCard与协议对象规范_V1.5.md)
- [A2A协议与NATS集成适配设计 V1.5](A2AMesh_A2A协议与NATS集成适配设计_V1.5.md)
- [Redis状态平面与数据设计 V1.5](A2AMesh_Redis状态平面与数据设计_V1.5.md)
- [任务生命周期与长任务运行时设计 V1.5](A2AMesh_任务生命周期与长任务运行时设计_V1.5.md)
- [接口请求与响应标准 V1.5](A2AMesh_接口请求与响应标准_V1.5.md)
- [统计审计与运行监控规则 V1.5](A2AMesh_统计审计与运行监控规则_V1.5.md)
- [Artifact与对象存储设计 V1.1](A2AMesh_Artifact与对象存储设计_V1.1.md)
- [受信配置与变更治理设计 V1.1](A2AMesh_受信配置与变更治理设计_V1.1.md)
- [人工对账与运维操作设计 V1.1](A2AMesh_人工对账与运维操作设计_V1.1.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
- [A2A v1.0.1 canonical Proto](https://github.com/a2aproject/A2A/blob/v1.0.1/specification/a2a.proto)
- [A2A Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A Custom Protocol Bindings](https://a2a-protocol.org/latest/topics/custom-protocol-bindings/)
- [MCP Specification 2026-07-28 Release](https://github.com/modelcontextprotocol/modelcontextprotocol/releases/tag/2026-07-28)
- [MCP stdio Transport](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/2026-07-28/docs/specification/2026-07-28/basic/transports/stdio.mdx)
- [MCP Streamable HTTP Transport](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/2026-07-28/docs/specification/2026-07-28/basic/transports/streamable-http.mdx)
- [MCP Python SDK v2.0.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
