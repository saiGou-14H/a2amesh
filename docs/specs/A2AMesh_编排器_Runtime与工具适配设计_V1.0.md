# A2AMesh 编排器、Runtime 与工具适配设计 V1.0

---

# 1. 文档目的

本文档定义 A2AMesh 编排器、Runtime Adapter、任务执行器、工具注册、MCP 接入、工作目录策略、并发容量、输出归一化和失败处理规则，使 Hermes、Codex、Claude Code、OpenCode 能在同一 A2A Task 模型下运行。

Task 生命周期由《任务生命周期与长任务运行时设计》负责；协议对象由《Agent Card 与协议对象规范》负责。本文只定义“如何规划、选择和执行”，不复制 Runtime 内部 Agent Loop。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立编排、Adapter、Tool Policy、MCP、工作目录、资源限制和验收规则 |

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
4. 工具采用本地 allowlist 和 schema；V1 不建设用户/RBAC。
5. 工作目录使用配置别名，调用方不能传任意绝对路径。
6. Runtime CLI 版本和参数会漂移，启动时探测、CI fixture 和真机测试必须锁定。
7. 结构化 RuntimeEvent 优先；纯文本只能降级为 `runtime_running`。
8. 所有外部副作用必须声明重试安全等级。
9. 编排失败不应破坏已完成子任务事实。
10. Observer 建议与执行解耦，避免自动反馈环。

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

## 12.3 重试

只有 `retry_safe=true` 且工具自身接受 idempotency key 时自动重试。文件 patch、部署、发布、发送消息默认不可盲目重试。

---

# 13. MCP 接入

MCP Connector 把已配置 MCP Server 的 Tool 映射到 ToolRegistry：

- Server 必须由本地配置声明；
- Tool schema 在注册时缓存并带 server generation；
- 调用设置 timeout、size limit 和审计；
- MCP HTTP Server 位于 NAT 后时仍不可被公网直接访问，应由本地 Peer 调用；
- `hermes mcp serve` 的能力边界不能被误认为远程文件/终端服务；
- MCP Tool 不自动成为 A2A AgentSkill。

---

# 14. 并发与容量

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
```

调度使用 semaphore；排队 Task phase=queued。队列长度和最老等待时间进入监控。取消排队 Task 不启动 Runtime。

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
└── observer/
    ├── consumer.py
    ├── rules.py
    └── policy.py
```

---

# 18. 验收用例

1. 非法/循环 Plan 在分发前拒绝。
2. Agent/Runtime/Skill/容量选择结果稳定且可解释。
3. 四个 Runtime 的固定版本 smoke test 通过。
4. argv 不使用 shell 拼接，workdir 无法逃逸 allowlist。
5. Runtime 60 秒静默仍有 heartbeat/cancel。
6. workspace 并发写被串行，只读可并行。
7. Tool schema、风险和 retry_safe 均生效。
8. MCP Server 未配置时不能被远程请求临时注入。
9. Aggregator 保留来源并显式报告冲突。
10. Observer 建议不能直接改变 Task，反馈环被阻断。
