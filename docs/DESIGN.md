# A2AMesh 设计文档（完整版）

> 对称 A2A Agent Mesh：多台异构机器的 AI Agent（Hermes / Codex / OpenCode / Claude Code）经公网 NATS 注册中心互联，任意 agent 调度任意 agent，全程 A2A 语义，NAT 友好（零隧道、零入站）。
> 本文档完整自包含，可直接作为实现规范。

## 目录

1. 概述
2. 快速开始
3. 可行性分析
4. 需求
5. 总体架构
6. 目录结构
7. 配置（完整文件）
8. 协议层
9. 契约层（JSON Schema 完整定义）
10. 数据模型（pydantic 完整定义）
11. 传输层实现
12. 运行时实现（adapters / executor / agent）
13. 工具生态实现
14. 会话与记忆
15. 编排器实现
16. 网关 / Ingress / UI
17. 流程与状态机
18. 安全
19. 部署
20. 可观测
21. 测试用例
22. 实现计划（可度量验收）
23. 附：与 Solace Agent Mesh 对比

---

## 1. 概述

**A2AMesh** = 一个公网 NATS 注册中心 + 一群对称 peer agent + A2A 协议 + JSON Schema 契约 + 多运行时 + 工具生态 + 编排器 + 网关/UI。

- 实现语言：Python 3.11+（`nats-py` / `pydantic` / `fastapi`）；前端 Vue3
- 唯一公网组件：NATS Server
- 每个 agent：纯 Python 进程，Windows/Linux 原生（无 WSL、无 Docker）

---

## 2. 快速开始（最小可运行路径）

```bash
# ① 公网机启动 NATS（含 JetStream）
docker run -d --name a2amesh-nats -p 4222:4222 \
  -v $PWD/nats.conf:/etc/nats/nats.conf \
  nats:latest -c /etc/nats/nats.conf

# ② 每台机器安装
pip install a2amesh

# ③ 初始化（生成 NKey + 骨架 + 配置）
a2amesh init --name win1 --nats wss://<公网IP>:4222
a2amesh bootstrap        # 生成 NKey seed，写入 .env，打印 public key 供管理员登记

# ④ 启动 agent（每台）
a2amesh agent start

# ⑤ 任意机器验证
mesh list                        # 看到所有在线 agent
mesh call win2 "执行 dir 并报告"   # 调度 win2
```

**验收（hello mesh）**：两台机器启动后，`mesh list` 互相可见，`mesh call` 能拿到对方输出。

---

## 3. 可行性分析

| 维度 | 结论 | 依据 |
|---|---|---|
| 技术 | ✅ 可行 | NATS/CNCF 成熟；nats-py 纯 Python 跨平台；四 CLI 命令已核实 |
| 风险 | 可控 | NATS 公网安全、LLM 结构化输出、Windows subprocess/pty（P1 实测） |
| 工作量 | ~3500 行 Py + ~500 行前端 | 1–2 周 MVP，3–4 周完整 |
| 规模 | 几十~上百 agent | NATS ~百万 msg/s |

---

## 4. 需求

| # | 需求 |
|---|------|
| R1 | 唯一公网组件 = 注册中心，纯基础设施 |
| R2 | 对称 peer：任意 agent 可调度任意 agent |
| R3 | 全 A2A 语义（message/send、message/stream、tasks/get、tasks/cancel、流式） |
| R4 | NAT 友好：只出网，零入站 |
| R5 | 多运行时（Hermes/Codex/OpenCode/Claude Code） |
| R6 | 跨平台（Linux 原生 + Windows 原生） |
| R7 | 工具生态支持自定义 |
| R8 | 编排器 + 网关 + UI |

---

## 5. 总体架构

```
NATS Server（公网：注册 + RPC + 流式 + JetStream KV/ObjectStore）
  ▲ 出网连入 × N
Agent A / B / C（对称 peer：serve + dispatch + discover + executor + tools）
  ├ Orchestrator（可选 peer 角色）
  └ Gateway + Ingress + UI（公网机，FastAPI + Vue3）
```

分层与单向依赖：

```
contracts  ← schemas
a2anats    ← contracts
tools      ← contracts
memory     ← contracts
runtime    ← a2anats + tools + memory
orchestrator ← a2anats + contracts
gateway    ← a2anats + contracts
cli        ← 以上全部
```

---

## 6. 目录结构

```
a2amesh/
├── pyproject.toml
├── nats/nats.conf
├── src/a2amesh/
│   ├── __init__.py               # 版本
│   ├── cli.py                    # a2amesh init/bootstrap/agent/ingress/orchestrator
│   ├── config.py                 # 配置加载 + pydantic 校验
│   ├── logging_setup.py          # JSON 结构化日志
│   ├── schemas/                  # 8 个 JSON Schema（§9）
│   ├── contracts/models.py       # pydantic 模型（§10）
│   ├── a2anats/
│   │   ├── errors.py             # JsonRpcError + 错误码
│   │   ├── client.py             # MeshClient
│   │   └── server.py             # MeshServer
│   ├── runtime/
│   │   ├── adapters/{base,hermes,codex,claude,opencode,registry}.py
│   │   ├── executor.py
│   │   └── agent.py              # AgentRuntime
│   ├── tools/{base,decorator,registry}.py + builtin/ + mcp/connector.py
│   ├── memory/store.py           # 会话/记忆 KV 封装
│   ├── orchestrator/{planner,dispatcher,tracker,aggregator,orchestrator}.py
│   └── gateway/{api,a2a_http,ws}.py + ui/
├── skills/mesh-skill/SKILL.md
└── tests/{unit,integration,e2e}/
```

---

## 7. 配置（完整文件）

### 7.1 pyproject.toml

```toml
[project]
name = "a2amesh"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "nats-py>=2.6.0",
  "pydantic>=2.6.0",
  "jsonschema>=4.21.0",
  "a2a-sdk>=0.1.0",
  "fastapi>=0.110.0",
  "uvicorn[standard]>=0.29.0",
  "aiofiles>=23.0.0",
  "python-dotenv>=1.0.0",
  "opentelemetry-api>=1.24",
  "opentelemetry-sdk>=1.24",
]

[project.scripts]
a2amesh = "a2amesh.cli:main"
mesh = "a2amesh.runtime.cli_mesh:main"

[project.optional-dependencies]
test = ["pytest", "pytest-asyncio"]
```

### 7.2 nats/nats.conf

```conf
port: 4222
server_name: a2amesh
http: 8222

jetstream {
  store_dir: "/data"
  max_memory_store: 256MB
  max_file_store: 2GB
}

tls {
  cert_file: "/etc/nats/cert.pem"
  key_file:  "/etc/nats/key.pem"
}

authorization {
  users = [
    { user: "win1", nkey: "U...", permissions: {
        subscribe: ["a2a.rpc.win1", "a2a.cards.win1", "$SRV.INFO", "_INBOX.>", "a2a.stream.win1.>"],
        publish:   ["a2a.stream.win1.>", "a2a.rpc.*", "a2a.cards.*", "$SRV.PING", "_INBOX.>",
                    "$KV.sess.win1.>", "$KV.mem.win1.>", "$KV.mem.shared.>"]
    }},
    { user: "win2", nkey: "U...", permissions: { /* 同理 */ } },
    { user: "linux", nkey: "U...", permissions: { /* 同理 */ } }
  ]
}
```

### 7.3 agents.yaml

```yaml
nats:
  url: wss://mesh.example.com:4222
  nkey_seed_env: A2AMESH_NKEY_SEED

agent:
  name: win1
  description: "Windows 1 worker"
  default_runtime: hermes
  workdir: "C:\\work"
  runtimes: [hermes, codex, claude, opencode]
  tools_dir: ./tools
  public_tools: [read_file, db_query]
  session_ttl_seconds: 86400
  task_timeout_seconds: 600

mcp: []        # [{type: stdio, command: ...} | {type: http, url: ...}]

observability:
  otlp_endpoint: ""
  log_level: INFO
```

### 7.4 .env（bootstrap 生成）

```ini
A2AMESH_NKEY_SEED=SUAA...     # 每 agent 唯一，绝不出本机
A2AMESH_NATS_URL=wss://mesh.example.com:4222
A2AMESH_AGENT_NAME=win1
```

---

## 8. 协议层

### 8.1 Subject

| Subject | 类型 | 作用 |
|---|---|---|
| `$SRV.PING` / `$SRV.INFO` / `$SRV.STATS` | req-reply | 发现 |
| `a2a.rpc.<agent>` | req-reply | A2A 方法入口 |
| `a2a.cards.<agent>` | req-reply | 拉取 AgentCard |
| `a2a.stream.<agent>.<task_id>` | pub-sub | 流式事件 |
| `a2a.dlq.<agent>` | pub-sub | 死信 |
| `$KV.sess.<agent>.<sessionId>` | KV | 会话 |
| `$KV.mem.<agent>.<key>` | KV | agent 长期记忆 |
| `$KV.mem.shared.<key>` | KV | 团队共享记忆 |

### 8.2 方法

`message/send`、`message/stream`、`tasks/get`、`tasks/cancel`、`tools/call`（扩展）

### 8.3 请求 / 响应 / 错误

```json
{ "jsonrpc": "2.0", "id": "8f3a", "method": "message/send",
  "params": { "message": { "role": "user", "parts": [{ "kind": "text", "text": "修复 auth 模块" }] },
              "metadata": { "runtime": "codex", "workdir": "/repo", "sessionId": "s1" } } }

{ "jsonrpc": "2.0", "id": "8f3a", "result": { "id": "task-1", "status": { "state": "completed" }, "artifacts": [ ... ] } }

{ "jsonrpc": "2.0", "id": "8f3a", "error": { "code": -32000, "message": "runtime not available: codex" } }
```

错误码：`-32601` 方法不存在、`-32602` 参数无效、`-32000` 运行时/工具不可用、`-32001` 超时、`-32002` 取消、`-32003` 权限不足。

### 8.4 流式事件（A2A 标准，NATS 与 SSE 同一对象）

| SSE event | JSON 数据 |
|---|---|
| `task-id` | `{"kind":"task-id","id":"task-1","contextId":"ctx-1"}` |
| `status-update` | `{"kind":"status-update","taskId":"task-1","contextId":"ctx-1","status":{"state":"working"},"final":false}` |
| `artifact-update` | `{"kind":"artifact-update","taskId":"task-1","contextId":"ctx-1","artifact":{"artifactId":"a1","parts":[...]},"append":false}` |
| `message-update` | `{"kind":"message-update","taskId":"task-1","contextId":"ctx-1","message":{"role":"agent","parts":[...]}}` |

流式链路：外部 client `message/stream`（Accept: text/event-stream）→ Ingress 订阅 `a2a.stream.<agent>.<task_id>` → 每消息按 `kind` 映射 SSE event 名转发。

---

## 9. 契约层（JSON Schema 完整定义）

### 9.1 part.json

```json
{ "$id": "https://a2amesh.dev/schemas/part.json",
  "oneOf": [
    { "type": "object", "required": ["kind", "text"],
      "properties": { "kind": { "const": "text" }, "text": { "type": "string" } } },
    { "type": "object", "required": ["kind", "file"],
      "properties": { "kind": { "const": "file" },
        "file": { "type": "object", "required": ["name"],
          "properties": { "name": { "type": "string" }, "mimeType": { "type": "string" }, "uri": { "type": "string" } } } } },
    { "type": "object", "required": ["kind", "data"],
      "properties": { "kind": { "const": "data" }, "data": { "type": "object" } } }
  ] }
```

### 9.2 message.json

```json
{ "$id": "https://a2amesh.dev/schemas/message.json",
  "type": "object", "required": ["role", "parts"],
  "properties": { "role": { "enum": ["user", "agent"] },
                  "parts": { "type": "array", "items": { "$ref": "part.json" } } } }
```

### 9.3 artifact.json

```json
{ "$id": "https://a2amesh.dev/schemas/artifact.json",
  "type": "object", "required": ["artifactId"],
  "properties": { "artifactId": { "type": "string" },
                  "parts": { "type": "array", "items": { "$ref": "part.json" } } } }
```

### 9.4 task.json

```json
{ "$id": "https://a2amesh.dev/schemas/task.json",
  "type": "object", "required": ["id", "status"],
  "properties": {
    "id": { "type": "string" },
    "status": { "type": "object", "required": ["state"],
      "properties": { "state": { "enum": ["submitted", "working", "input-required", "completed", "failed", "canceled"] } } },
    "history": { "type": "array", "items": { "$ref": "message.json" } },
    "artifacts": { "type": "array", "items": { "$ref": "artifact.json" } } } }
```

### 9.5 agent-card.json

```json
{ "$id": "https://a2amesh.dev/schemas/agent-card.json",
  "type": "object", "required": ["name", "description"],
  "properties": {
    "name": { "type": "string" }, "description": { "type": "string" },
    "capabilities": { "type": "object", "properties": {
      "runtimes": { "type": "array", "items": {
          "type": "object", "required": ["name", "available"],
          "properties": { "name": { "enum": ["hermes", "codex", "claude", "opencode"] }, "available": { "type": "boolean" } } } },
      "default_runtime": { "type": "string" },
      "tools": { "type": "array", "items": {
          "type": "object", "required": ["name", "description", "parameters"],
          "properties": { "name": { "type": "string" }, "description": { "type": "string" },
            "parameters": { "type": "object" }, "risk": { "enum": ["low", "medium", "high"] } } } } } },
    "skills": { "type": "array", "items": {
        "type": "object", "required": ["id", "name"],
        "properties": { "id": { "type": "string" }, "name": { "type": "string" }, "description": { "type": "string" } } } } } }
```

### 9.6 plan.json

```json
{ "$id": "https://a2amesh.dev/schemas/plan.json",
  "type": "object", "required": ["task_id", "steps"],
  "properties": { "task_id": { "type": "string" },
    "steps": { "type": "array", "items": {
      "type": "object", "required": ["id", "target", "prompt", "status"],
      "properties": { "id": { "type": "string" },
        "depends_on": { "type": "array", "items": { "type": "string" } },
        "target": { "type": "string" },
        "runtime": { "enum": ["hermes", "codex", "claude", "opencode"] },
        "prompt": { "type": "string" },
        "status": { "enum": ["pending", "running", "succeeded", "failed"] } } } } } }
```

### 9.7 tool.json

```json
{ "$id": "https://a2amesh.dev/schemas/tool.json",
  "type": "object", "required": ["name", "description", "parameters"],
  "properties": { "name": { "type": "string" }, "description": { "type": "string" },
    "parameters": { "type": "object" }, "source": { "enum": ["builtin", "custom", "runtime", "mcp"] },
    "risk": { "enum": ["low", "medium", "high"] }, "public": { "type": "boolean" } } }
```

### 9.8 rpc.json

```json
{ "$id": "https://a2amesh.dev/schemas/rpc.json",
  "type": "object", "required": ["jsonrpc", "id", "method"],
  "properties": { "jsonrpc": { "const": "2.0" }, "id": { "type": "string" },
    "method": { "enum": ["message/send", "message/stream", "tasks/get", "tasks/cancel", "tools/call"] },
    "params": { "type": "object" } } }
```

---

## 10. 数据模型（pydantic v2 完整定义）

```python
# contracts/models.py
from pydantic import BaseModel
from typing import Literal, Optional

class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str

class FilePart(BaseModel):
    kind: Literal["file"] = "file"
    file: dict            # {name, mimeType?, uri?}

class DataPart(BaseModel):
    kind: Literal["data"] = "data"
    data: dict

Part = TextPart | FilePart | DataPart

class Message(BaseModel):
    role: Literal["user", "agent"]
    parts: list[Part]

class Artifact(BaseModel):
    artifactId: str
    parts: list[Part] = []

class TaskStatus(BaseModel):
    state: Literal["submitted", "working", "input-required", "completed", "failed", "canceled"]

class Task(BaseModel):
    id: str
    status: TaskStatus
    history: list[Message] = []
    artifacts: list[Artifact] = []

class RuntimeCapability(BaseModel):
    name: Literal["hermes", "codex", "claude", "opencode"]
    available: bool = True

class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict
    source: Literal["builtin", "custom", "runtime", "mcp"] = "custom"
    risk: Literal["low", "medium", "high"] = "low"
    public: bool = True

class Skill(BaseModel):
    id: str
    name: str
    description: str = ""

class AgentCard(BaseModel):
    name: str
    description: str
    capabilities: dict = {}
    skills: list[Skill] = []

class Step(BaseModel):
    id: str
    target: str
    prompt: str
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    depends_on: list[str] = []
    runtime: Optional[Literal["hermes", "codex", "claude", "opencode"]] = None

class Plan(BaseModel):
    task_id: str
    steps: list[Step]
```

pydantic v2 的 `.model_json_schema()` 导出 JSON Schema，与 §9 的 `schemas/*.json` 在 CI 中做一致性断言。

---

## 11. 传输层实现

### 11.1 errors.py

```python
class JsonRpcError(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message
        super().__init__(message)

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
UNAVAILABLE = -32000
TIMEOUT = -32001
CANCELED = -32002
FORBIDDEN = -32003
```

### 11.2 client.py

```python
import json, asyncio
from uuid import uuid4
from a2amesh.contracts.models import AgentCard, Task, Message

class MeshClient:
    def __init__(self, nc):
        self.nc = nc

    async def discover(self) -> list[AgentCard]:
        resp = await self.nc.request("$SRV.INFO", b"", timeout=5)
        # 解析 NATS Services INFO 响应 -> [AgentCard]
        return [AgentCard(**c) for c in json.loads(resp.data)]

    async def get_card(self, agent: str) -> AgentCard:
        resp = await self.nc.request(f"a2a.cards.{agent}", b"", timeout=5)
        return AgentCard(**json.loads(resp.data))

    async def send_message(self, agent: str, message: Message, *,
                           runtime=None, workdir=None, session_id=None,
                           timeout=600) -> Task:
        req = {"jsonrpc": "2.0", "id": uuid4().hex, "method": "message/send",
               "params": {"message": message.model_dump(),
                          "metadata": {"runtime": runtime, "workdir": workdir, "sessionId": session_id}}}
        resp = await asyncio.wait_for(
            self.nc.request(f"a2a.rpc.{agent}", json.dumps(req).encode(), timeout=timeout), timeout)
        data = json.loads(resp.data)
        if "error" in data:
            raise JsonRpcError(data["error"]["code"], data["error"]["message"])
        return Task(**data["result"])

    async def get_task(self, agent: str, task_id: str) -> Task:
        req = {"jsonrpc": "2.0", "id": uuid4().hex, "method": "tasks/get",
               "params": {"id": task_id}}
        resp = await self.nc.request(f"a2a.rpc.{agent}", json.dumps(req).encode(), timeout=10)
        return Task(**json.loads(resp.data)["result"])

    async def cancel(self, agent: str, task_id: str) -> None:
        req = {"jsonrpc": "2.0", "id": uuid4().hex, "method": "tasks/cancel",
               "params": {"id": task_id}}
        await self.nc.request(f"a2a.rpc.{agent}", json.dumps(req).encode(), timeout=10)

    async def call_tool(self, agent: str, tool: str, arguments: dict) -> dict:
        req = {"jsonrpc": "2.0", "id": uuid4().hex, "method": "tools/call",
               "params": {"tool": tool, "arguments": arguments}}
        resp = await self.nc.request(f"a2a.rpc.{agent}", json.dumps(req).encode(), timeout=60)
        data = json.loads(resp.data)
        if "error" in data:
            raise JsonRpcError(data["error"]["code"], data["error"]["message"])
        return data["result"]

    async def broadcast(self, prompt: str, runtime=None) -> list[Task]:
        agents = await self.discover()
        return await asyncio.gather(
            *(self.send_message(a.name, Message(role="user", parts=[TextPart(text=prompt)]),
                                runtime=runtime) for a in agents))
```

### 11.3 server.py

```python
import json
from a2amesh.a2anats.errors import JsonRpcError, METHOD_NOT_FOUND

class MeshServer:
    def __init__(self, nc, agent_name: str, handler):
        self.nc, self.name, self.handler = nc, agent_name, handler

    async def start(self):
        await self.nc.add_service(self.name, version="1.0.0")
        await self.nc.subscribe(f"a2a.rpc.{self.name}", cb=self._on_rpc)
        await self.nc.subscribe(f"a2a.cards.{self.name}", cb=self._on_card)

    async def _on_card(self, msg):
        await msg.respond(json.dumps(self.handler.card().model_dump()).encode())

    async def _on_rpc(self, msg):
        req = json.loads(msg.data)
        try:
            result = await self._dispatch(req["method"], req.get("params", {}), msg)
            await msg.respond(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}).encode())
        except JsonRpcError as e:
            await msg.respond(json.dumps({"jsonrpc": "2.0", "id": req.get("id"),
                                          "error": {"code": e.code, "message": e.message}}).encode())

    async def _dispatch(self, method, params, msg):
        if method == "message/send":    return await self.handler.handle_task(params)
        if method == "message/stream":  return await self.handler.handle_task_stream(params, msg)
        if method == "tasks/get":       return await self.handler.get_task(params)
        if method == "tasks/cancel":    return await self.handler.cancel(params)
        if method == "tools/call":      return await self.handler.call_tool(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method}")

    async def publish_stream(self, task_id: str, event: dict):
        await self.nc.publish(f"a2a.stream.{self.name}.{task_id}",
                              json.dumps(event).encode())
```

---

## 12. 运行时实现

### 12.1 adapters/base.py

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class TaskResult:
    ok: bool
    output: str

class AgentAdapter(ABC):
    name: str
    @abstractmethod
    def command(self, prompt: str, workdir: str | None, opts: dict) -> list[str]: ...
    @abstractmethod
    def resume_command(self, session_id: str, prompt: str, workdir: str | None, opts: dict) -> list[str]: ...
    def parse(self, stdout: bytes, stderr: bytes, rc: int) -> TaskResult:
        return TaskResult(ok=(rc == 0),
                          output=(stdout.decode(errors="replace") or stderr.decode(errors="replace")).strip())
```

### 12.2 四个 adapter

```python
class HermesAdapter(AgentAdapter):
    name = "hermes"
    def command(self, prompt, workdir, opts): return ["hermes", "chat", "-q", prompt]
    def resume_command(self, sid, prompt, workdir, opts): return ["hermes", "--resume", sid, "-q", prompt]

class CodexAdapter(AgentAdapter):
    name = "codex"
    def command(self, prompt, workdir, opts):
        cmd = ["codex", "exec"]
        if opts.get("full_auto"): cmd.append("--full-auto")
        return cmd + [prompt]
    def resume_command(self, sid, prompt, workdir, opts):
        return ["codex", "--resume", sid, "exec", prompt]

class ClaudeAdapter(AgentAdapter):
    name = "claude"
    def command(self, prompt, workdir, opts):
        cmd = ["claude", "-p", prompt]
        if opts.get("max_turns"): cmd += ["--max-turns", str(opts["max_turns"])]
        if opts.get("output_json"): cmd.append("--output-format json")
        return cmd
    def resume_command(self, sid, prompt, workdir, opts):
        return self.command(prompt, workdir, opts) + ["--resume", sid]

class OpenCodeAdapter(AgentAdapter):
    name = "opencode"
    def command(self, prompt, workdir, opts):
        cmd = ["opencode", "run", prompt]
        if opts.get("model"): cmd += ["--model", opts["model"]]
        return cmd
    def resume_command(self, sid, prompt, workdir, opts):
        return ["opencode", "-s", sid, "run", prompt]
```

### 12.3 adapters/registry.py

```python
import shutil

ADAPTERS = [HermesAdapter(), CodexAdapter(), ClaudeAdapter(), OpenCodeAdapter()]

def detect_adapters() -> dict[str, AgentAdapter]:
    """按本机 PATH 探测已安装的运行时"""
    return {a.name: a for a in ADAPTERS if shutil.which(a.command_bin(a.name))}
```

### 12.4 executor.py

```python
import asyncio

class Executor:
    def __init__(self, adapters, default, timeout=600):
        self.adapters, self.default, self.timeout = adapters, default, timeout

    async def run(self, runtime, prompt, workdir, opts=None, session_id=None,
                  on_stream=None, cancel_evt=None) -> TaskResult:
        adapter = self.adapters[runtime]
        cmd = adapter.resume_command(session_id, prompt, workdir, opts or {}) if session_id \
              else adapter.command(prompt, workdir, opts or {})
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=workdir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        chunks = []
        try:
            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout)
                if not line:
                    break
                chunks.append(line)
                if on_stream:
                    await on_stream({"kind": "artifact-update",
                                     "artifact": {"artifactId": "a1", "parts": [{"kind": "text", "text": line.decode(errors="replace")}]}})
                if cancel_evt and cancel_evt.is_set():
                    proc.kill()
                    return TaskResult(ok=False, output="canceled")
        except asyncio.TimeoutError:
            proc.kill()
            return TaskResult(ok=False, output="timeout")
        out, err = await proc.communicate()
        full = b"".join(chunks) or out
        return adapter.parse(full, err, proc.returncode)
```

### 12.5 agent.py（AgentRuntime）

```python
import os, asyncio, json
from uuid import uuid4
import nats
from a2amesh.contracts.models import AgentCard, Skill, RuntimeCapability, ToolSpec
from a2amesh.a2anats.client import MeshClient
from a2amesh.a2anats.server import MeshServer
from a2amesh.runtime.executor import Executor
from a2amesh.runtime.adapters.registry import detect_adapters
from a2amesh.tools.registry import ToolRegistry

class AgentRuntime:
    def __init__(self, cfg):
        self.cfg = cfg

    async def start(self):
        seed = os.environ[self.cfg.nats.nkey_seed_env]
        self.nc = await nats.connect(self.cfg.nats.url, nkeys_seed=seed)
        self.tools = ToolRegistry()
        self.tools.load_builtin()
        self.tools.load_custom(self.cfg.agent.tools_dir)
        await self.tools.connect_mcp(self.cfg.mcp)
        self.executor = Executor(detect_adapters(), self.cfg.agent.default_runtime,
                                 timeout=self.cfg.agent.task_timeout_seconds)
        self.server = MeshServer(self.nc, self.cfg.agent.name, handler=self)
        self.client = MeshClient(self.nc)
        await self.server.start()
        await self._publish_card()
        asyncio.create_task(self._heartbeat_loop())

    def card(self) -> AgentCard:
        return AgentCard(
            name=self.cfg.agent.name,
            description=self.cfg.agent.description,
            capabilities={"runtimes": [RuntimeCapability(name=k).model_dump() for k in self.executor.adapters],
                          "default_runtime": self.cfg.agent.default_runtime,
                          "tools": [t.model_dump() for t in self.tools.list()]},
            skills=[Skill(id=t.name, name=t.name, description=t.description)
                    for t in self.tools.list() if t.public])

    async def _publish_card(self):
        await self.nc.publish(f"a2a.cards.{self.cfg.agent.name}",
                              json.dumps(self.card().model_dump()).encode())

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(15)
            await self._publish_card()

    async def handle_task(self, params) -> dict:
        from a2amesh.contracts.models import Message, TextPart
        msg = Message(**params["message"])
        meta = params.get("metadata") or {}
        runtime = meta.get("runtime") or self.cfg.agent.default_runtime
        if runtime not in self.executor.adapters:
            raise JsonRpcError(-32000, f"runtime not available: {runtime}")
        task_id = uuid4().hex
        text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
        res = await self.executor.run(runtime, text, meta.get("workdir"), meta,
                                      session_id=meta.get("sessionId"))
        return {"id": task_id,
                "status": {"state": "completed" if res.ok else "failed"},
                "artifacts": [{"artifactId": "a1", "parts": [{"kind": "text", "text": res.output}]}]}

    async def handle_task_stream(self, params, req_msg) -> dict: ...   # 见 §17.3
    async def get_task(self, params) -> dict: ...
    async def cancel(self, params) -> dict: ...
    async def call_tool(self, params) -> dict:
        return await self.tools.call(params["tool"], params.get("arguments", {}))
```

---

## 13. 工具生态实现

```python
# tools/base.py
from dataclasses import dataclass, field
from typing import Callable, Awaitable

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[dict]]
    source: str = "custom"
    risk: str = "low"
    public: bool = True

# tools/decorator.py
def tool(name, description, parameters, risk="low", public=True):
    def deco(fn):
        _REGISTRY.register(Tool(name=name, description=description, parameters=parameters,
                                risk=risk, public=public, source="custom", handler=fn))
        return fn
    return deco

# tools/registry.py
import jsonschema

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool):
        self._tools[t.name] = t

    def load_builtin(self):
        from a2amesh.tools import builtin
        builtin.register_all(self)

    def load_custom(self, tools_dir):
        import importlib, sys
        for f in tools_dir.glob("*.py"):
            spec = importlib.util.spec_from_file_location(f.stem, f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f.stem] = mod
            spec.loader.exec_module(mod)      # @tool 装饰器自动注册

    async def connect_mcp(self, configs):
        from a2amesh.tools.mcp.connector import connect
        for c in configs:
            for t in await connect(c):
                self.register(t)

    def list(self) -> list[ToolSpec]:
        return [ToolSpec(name=t.name, description=t.description, parameters=t.parameters,
                         source=t.source, risk=t.risk, public=t.public) for t in self._tools.values()]

    async def call(self, name, arguments) -> dict:
        t = self._tools.get(name)
        if not t:
            raise JsonRpcError(-32000, f"tool not found: {name}")
        jsonschema.validate(arguments, t.parameters)
        return await t.handler(**arguments)
```

builtin 工具：`read_file / write_file / run_shell / http_request / put_artifact / get_artifact / memory_get / memory_set / memory_shared_get / memory_shared_set`。

---

## 14. 会话与记忆

### 14.1 三层模型

| 层 | 作用域 | KV subject | 生命周期 |
|---|---|---|---|
| 会话（短期） | 单 agent 单 session | `$KV.sess.<agent>.<sessionId>` | TTL 可配 |
| agent 长期 | 单 agent 跨会话 | `$KV.mem.<agent>.<key>` | 持久 |
| 团队共享 | 跨 agent | `$KV.mem.shared.<key>` | 持久 |

### 14.2 memory/store.py

```python
class MemoryStore:
    def __init__(self, js, agent):
        self.js, self.agent = js, agent
        self.sess = js.KeyValue(f"sess.{agent}")
        self.mem = js.KeyValue(f"mem.{agent}")
        self.shared = js.KeyValue("mem.shared")

    async def session_append(self, session_id, message: dict):
        key = session_id
        hist = await self.sess.get(key) or []
        hist.append(message)
        await self.sess.put(key, json.dumps(hist))

    async def session_get(self, session_id) -> list[dict]:
        return json.loads((await self.sess.get(session_id)) or "[]")

    async def session_close(self, session_id):
        await self.sess.delete(session_id)

    async def mem_get(self, key):  return await self.mem.get(key)
    async def mem_set(self, key, value):  await self.mem.put(key, value)
    async def shared_get(self, key):  return await self.shared.get(key)
    async def shared_set(self, key, value):  await self.shared.put(key, value)
```

### 14.3 会话续接

不把历史文本硬塞 prompt，而是把 `sessionId` 交给运行时原生续聊机制（adapter `resume_command`）。TTL 默认 24h（JetStream KV 的 `ttl` 参数）。

---

## 15. 编排器实现

### 15.1 orchestrator.py

```python
class Orchestrator:
    def __init__(self, client: MeshClient, planner, dispatcher, tracker, aggregator):
        ...

    async def handle(self, prompt: str) -> Task:
        agents = await self.client.discover()
        plan = await self.planner.plan(prompt, agents)
        await self.dispatcher.run(plan)
        return await self.aggregator.collect(plan)
```

### 15.2 planner.py（LLM 结构化输出 + 校验重试）

```python
class Planner:
    def __init__(self, executor: Executor, max_retries=3):
        ...

    async def plan(self, prompt, agents: list[AgentCard]) -> Plan:
        ctx = self._agents_context(agents)          # 拼接各 agent name/description/skills/tools
        for attempt in range(self.max_retries):
            raw = await self.executor.run("claude", self._plan_prompt(prompt, ctx),
                                          None, {"output_json": True, "json_schema": plan_schema})
            try:
                plan = Plan.model_validate_json(raw.output)
                return plan
            except ValidationError as e:
                prompt += f"\n上次输出不合规：{e}\n请重新输出合法 JSON。"
        raise RuntimeError("planner failed after retries")
```

### 15.3 dispatcher.py（拓扑并行）

```python
class Dispatcher:
    async def run(self, plan: Plan):
        ready = [s for s in plan.steps if not s.depends_on]
        pending = {s.id: s for s in plan.steps}
        running = {}
        while ready or running:
            tasks = []
            for s in ready:
                s.status = "running"
                running[s.id] = s
                tasks.append(self._run_step(s))
            if tasks:
                await asyncio.gather(*tasks)
            # 解锁下游
            for sid, s in running.items():
                if s.status in ("succeeded", "failed"):
                    running.pop(sid, None)
                    for other in plan.steps:
                        if sid in other.depends_on and all(
                            pending[d].status == "succeeded" for d in other.depends_on):
                            if other.status == "pending":
                                ready.append(other)
            if not tasks and not ready and running:
                break   # 死锁保护

    async def _run_step(self, s: Step):
        for attempt in range(3):
            try:
                await self.client.send_message(s.target, Message(role="user",
                                              parts=[TextPart(text=s.prompt)]), runtime=s.runtime)
                s.status = "succeeded"; return
            except Exception:
                await asyncio.sleep(2 ** attempt)      # 指数退避
        s.status = "failed"
```

### 15.4 tracker.py / aggregator.py

```python
class Tracker:      # 维护每步 {status, attempt, deadline}，超时标记 failed
class Aggregator:   # 收集各步 artifacts，合并为最终 Task
```

---

## 16. 网关 / Ingress / UI

### 16.1 gateway/api.py（FastAPI）

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/api/agents")                       # -> list[AgentCard]
@app.post("/api/tasks")                       # body {prompt, target?, runtime?} -> Task
@app.get("/api/tasks/{id}")                   # -> Task
@app.post("/api/agents/{name}/tasks")         # 定向派发
@app.get("/api/stream/{task_id}")             # SSE：订阅 a2a.stream.* 转发
@app.get("/api/health")
@app.websocket("/ws")                         # 推送 agent 上下线 + 任务状态
```

### 16.2 gateway/a2a_http.py（标准 A2A ingress）

```python
@app.post("/")                                # JSON-RPC -> 翻译成 a2a.rpc.<target> / orchestrator
@app.get("/.well-known/agent.json")           # ingress AgentCard（skills = 所有 mesh agent）
```

Ingress 翻译：HTTP JSON-RPC → 校验 rpc.json → 按 `params.metadata.target` 路由到 `a2a.rpc.<target>`（无 target 转发 orchestrator）→ 结果回 HTTP；流式订阅 `a2a.stream.*` 转 SSE。

### 16.3 UI（Vue3 五视图）

Agents（在线/运行时/工具/健康）/ Dispatch（选 agent+运行时发任务）/ Tasks（状态+流式日志）/ Orchestrator（DAG 可视化）/ Topology（网格拓扑）。

---

## 17. 流程与状态机

### 17.1 注册序列

`connect(出网,NKey) → add_service(name) → subscribe a2a.rpc/cards → 探测 runtimes+装载 tools → publish AgentCard（初始 + 每 15s 心跳）`

### 17.2 A→B 调度

`A.client.send_message("B", msg) → nc.request("a2a.rpc.B") → B._dispatch("message/send") → handle_task → executor.run → respond(Task)`

### 17.3 流式 + 取消

```python
async def handle_task_stream(self, params, req_msg) -> dict:
    task_id = uuid4().hex
    await self.server.publish_stream(task_id, {"kind": "task-id", "id": task_id})
    cancel_evt = asyncio.Event()
    self._cancels[task_id] = cancel_evt
    async def on_stream(evt):
        await self.server.publish_stream(task_id, {**evt, "taskId": task_id})
    res = await self.executor.run(runtime, text, workdir, meta,
                                  on_stream=on_stream, cancel_evt=cancel_evt)
    state = "canceled" if cancel_evt.is_set() else ("completed" if res.ok else "failed")
    await self.server.publish_stream(task_id, {"kind": "status-update",
                                               "status": {"state": state}, "final": True})
    return {"id": task_id}
```

### 17.4 状态机

- Task：`submitted → working → completed / failed / canceled / input-required`
- Plan：`submitted → planning → dispatching → running → aggregating → completed / failed / canceled`
- Agent：`offline → registering → online → (心跳超时) offline → 重连 → online`

---

## 18. 安全

### 18.1 NKey 生成

```bash
# 每 agent 生成（seed 自留，public key 交管理员写入 nats.conf）
nsc generate nkey --user
# 或 bootstrap 自动：a2amesh bootstrap → 生成 seed 到 .env，打印 public key 供登记
```

### 18.2 要点

- NATS TLS + NKey + subject ACL（§7.2）
- Ingress mTLS + bearer token
- 工具 risk 分级，high 需审批；`public=false` 不外露；子进程隔离 + 超时 kill
- 边界（gateway/orchestrator）强校验；内部跳过（ACL 可信）
- 仅暴露 4222；8222 绑 127.0.0.1；可选 Tailscale 纵深

---

## 19. 部署

**Linux agent（systemd）：**

```ini
[Unit]
Description=A2AMesh Agent
After=network-online.target
[Service]
EnvironmentFile=/etc/a2amesh/.env
ExecStart=/usr/bin/a2amesh agent start
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
```

**Windows agent（NSSM）：**

```
nssm install A2AMeshAgent "C:\Python311\python.exe" "-m a2amesh agent start"
nssm set A2AMeshAgent AppDirectory C:\a2amesh
nssm start A2AMeshAgent
```

**NATS**：docker（§2）或 systemd。**Gateway/Ingress**：docker 或 systemd（公网机）。

---

## 20. 可观测

- 日志：JSON 行 `{ts, level, logger, agent, task_id?, correlation_id?, msg}`
- 指标：Prometheus（tasks_total, task_duration_seconds, agents_online, tool_calls_total）
- Trace：OTLP（register/dispatch/call span）
- NATS：8222 监控

---

## 21. 测试用例

| 层 | 用例 |
|---|---|
| 单元 | contract 序列化往返；adapter command/resume_command；tool 参数校验；planner validate+retry；错误码映射 |
| 集成 | 2 agent message/send 往返；3 agent 任意调度；tools/call；流式 4 事件顺序；cancel 中断；会话续接 |
| 编排 | 3 步 DAG 并行；依赖解锁；失败重试重指派 |
| E2E | 真机三机互通；网关/UI；外部标准 A2A client 走 ingress |
| 契约 | pydantic schema == schemas/*.json（CI） |

---

## 22. 实现计划（可度量验收）

| 阶段 | 内容 | 可度量验收 |
|---|---|---|
| P0 | NATS + schemas + contracts | docker 起 NATS；`mesh list` 空；契约一致性测试绿 |
| P1 | a2anats + adapters | 两 agent `mesh call B "echo hi"` 返回 B 输出，本机延迟 < 2s |
| P2 | runtime + executor + tools | 3 agent 任意两两调度；`tools/call read_file` 返回文件内容 |
| P3 | 真机 + 安全 | 三机互通；未授权 agent 无法订阅他人 rpc（ACL 测试） |
| P4 | 流式 + 可观测 + 可靠性 | 流式 4 事件按序；cancel < 3s 中断；指标可抓取 |
| P5 | Orchestrator | 「win1 写文件、win2 读并汇报」自动拆 2 步正确执行 |
| P6 | Gateway + UI | 网页派发并看实时状态；标准 a2a client 经 ingress 调通 |
| P7 | JetStream 工件 + 权限 + skill | 跨 agent 传文件；high 工具触发审批；Hermes skill 协调全网 |

---

## 23. 附：与 Solace Agent Mesh 对比

| 维度 | A2AMesh | SAM |
|---|---|---|
| 总线 | NATS（轻） | Solace（重） |
| 运行时 | 多运行时 | 仅 ADK |
| 编排 | Orchestrator + LLM DAG | Orchestrator + 确定性 Workflows |
| 网关/UI | HTTP A2A + REST + WS + Vue3 | 内置多网关（含 Slack/Teams） |
| 工具 | builtin/custom/runtime/mcp | 内置 + Python + MCP |
| 流式 | A2A 标准事件 100% 兼容 | 原生 A2A |
| 会话/记忆 | 三层 KV + 运行时续聊 | ADK session/memory |
| JSON Schema 契约 | ✅ | ❌ |
| Windows | 原生 | 需 WSL |
| 外部 A2A agent | 自建免费 | Enterprise 付费 |
| 成熟度 | 待实现 | 成品 |
