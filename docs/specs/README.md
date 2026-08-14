# A2AMesh V1 设计文档索引

> 生成日期：2026-08-14
> 文档状态：目标设计基线；当前实现状态以《A2AMesh 开发实施计划》为准。
> 参考规范：Knowledge Center 系列设计文档的版本管理、权威边界、状态机、失败矩阵和验收写法。

---

# 1. 文档目的

本目录是 A2AMesh V1 的正式设计入口，面向产品、架构、后端、Agent Runtime、测试、运维和 AI 代码生成器。文档将业务定位、协议对象、NATS Binding、Redis 状态、长任务、Runtime、接口、监控和实施计划拆开维护，避免单一大文档中同一规则多次定义。

本目录是当前唯一权威设计入口。已被替代的综合设计不再保留在工作树中，历史版本通过 Git 追溯；后续实现不得从旧提交复制已被本套专项修正的协议或状态规则。

---

# 2. 文档清单与阅读顺序

| 序号 | 文档 | 权威内容 | 推荐读者 |
|---:|---|---|---|
| 1 | [业务与总体架构设计 V1.0](A2AMesh_业务与总体架构设计_V1.0.md) | 产品定位、边界、物理/逻辑架构、范围、NFR | 全员 |
| 2 | [Agent Card 与协议对象规范 V1.0](A2AMesh_AgentCard与协议对象规范_V1.0.md) | A2A v1 对象、Card、扩展、字段语义 | 协议、后端、测试 |
| 3 | [A2A 协议与 NATS 集成适配设计 V1.0](A2AMesh_A2A协议与NATS集成适配设计_V1.0.md) | 标准 Gateway、NATS Binding、Subject、投递语义 | 架构、后端、运维 |
| 4 | [Redis 状态平面与数据设计 V1.0](A2AMesh_Redis状态平面与数据设计_V1.0.md) | Key、索引、Lua、租约、幂等、保留和恢复 | 后端、DBA、测试 |
| 5 | [任务生命周期与长任务运行时设计 V1.0](A2AMesh_任务生命周期与长任务运行时设计_V1.0.md) | Task 状态机、Supervisor、进度、SSE、Push、恢复 | Runtime、前端、测试 |
| 6 | [编排器、Runtime 与工具适配设计 V1.0](A2AMesh_编排器_Runtime与工具适配设计_V1.0.md) | Plan/Dispatch/Aggregate、Adapter、Tool、MCP | Agent、后端、测试 |
| 7 | [接口请求与响应标准 V1.0](A2AMesh_接口请求与响应标准_V1.0.md) | 外部 A2A、内部 NATS、错误、分页、幂等、示例 | 联调、SDK、测试 |
| 8 | [统计、审计与运行监控规则 V1.0](A2AMesh_统计审计与运行监控规则_V1.0.md) | 指标、日志、Trace、健康、告警、保留 | 运维、测试、架构 |
| 9 | [开发实施计划](A2AMesh_开发实施计划.md) | 当前状态、阶段、文件、任务、门禁、风险和上线 | 项目全员 |

推荐顺序：`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9`。实施人员先阅读 1、9，再按任务读取对应专项。

---

# 3. 权威边界

| 规则 | 权威文档 |
|---|---|
| V1 范围、组件职责和部署拓扑 | 总体架构 |
| Agent Card、Task、Message、Artifact、扩展字段 | 协议对象规范 |
| NATS Subject、Envelope、请求/流语义 | NATS 集成适配 |
| Redis Key、字段、Lua、TTL、索引 | Redis 数据设计 |
| 长任务状态、heartbeat、lease、恢复 | 任务生命周期运行时 |
| 编排、Runtime Adapter、Tool Policy | 编排与 Runtime 适配 |
| 请求头、接口、错误、示例 | 接口标准 |
| 指标、审计、告警、保留 | 监控规则 |
| 当前完成度、实施顺序和退出门禁 | 开发实施计划 |

若专项文档与实施计划冲突：业务规则以专项文档为准，当前完成状态和排期以实施计划为准。

---

# 4. 版本管理规则

1. 文件名带版本号的专项文档发布后视为不可变历史版本。
2. 内容修正需复制最新文件、递增版本号并更新版本说明；不得悄悄改写已发布版本。
3. `A2AMesh_开发实施计划.md` 是持续更新文件，不在文件名中增加版本号；历史由 Git 追踪。
4. 协议规范版本、SDK 版本和项目文档版本彼此独立：本套文档 V1.0 基于 A2A Specification v1.0.1，协商值为 `1.0`。
5. 只有相应验收门禁通过后，README 才能使用“已实现”“已兼容”等表述。

---

# 5. 标识与追踪规范

| 前缀 | 含义 | 示例 |
|---|---|---|
| `BR` | 业务需求 | `BR-005` 长任务可观察 |
| `NFR` | 非功能需求 | `NFR-003` 单次投递不重复执行 |
| `ADR` | 架构决策 | `ADR-004` JetStream 为唯一事件日志 |
| `API` | 接口契约 | `API-A2A-006` SubscribeToTask |
| `DATA` | 数据/Key 契约 | `DATA-TASK-001` Task 快照 |
| `EVT` | 事件契约 | `EVT-PROGRESS-001` Progress Update |
| `OBS` | 指标/告警 | `OBS-ALERT-004` Task lease 过期 |
| `TEST` | 验收用例 | `TEST-A2A-001` 官方 SDK 黑盒 |

需求、接口、数据和测试必须能互相追踪。新增能力至少包含一个需求 ID、一个实现契约和一个验收 ID。

---

# 6. 通用写作规则

1. 每份文档必须包含：目的、版本、边界、核心规则、失败/降级、验收。
2. “当前实现”与“目标设计”分栏；不得用规划描述代码现状。
3. 规范对象以官方 A2A Proto/SDK 为准，不手写平行标准。
4. 示例 ID 使用字符串；时间使用带时区 ISO 8601；持续时间使用毫秒整数。
5. 外部 JSON 使用官方 ProtoJSON 字段名；内部扩展使用 `lowerCamelCase`。
6. V1 是单 Mesh/单信任域部署，不建设 tenant、RBAC 或 Permission Center；仍保留 NKey 身份、调用者归属、Task 所有权和高风险工具策略。
7. 文档不得包含真实 Token、NKey seed、Redis 密码、内部公网地址或用户隐私。
8. Mermaid、JSON、SQL/Lua 伪代码必须配文字说明和验收条件。

---

# 7. 统一状态标签

| 标签 | 含义 |
|---|---|
| 已实现并验证 | 当前代码存在，自动化测试或实机证据通过 |
| 已实现未验证 | 当前代码存在，但没有完整门禁证据 |
| 部分实现 | 仅覆盖目标语义的一部分 |
| 目标设计 | 尚未实现，本文给出实施契约 |
| V2 预留 | V1 不交付，不得提前扩建 |

---

# 8. 需求追踪矩阵

## 8.1 业务需求

| 需求 | 主要设计 | 核心契约 | 验收 ID |
|---|---|---|---|
| BR-001 对称调用 | 总体架构、NATS 适配 | `a2a.v1.rpc.<agentId>` | TEST-MESH-001 三机任意方向调用 |
| BR-002 NAT 零入站 | 总体架构、部署阶段 | Peer 主动 NATS TLS/WSS | TEST-NAT-001 Windows 入站端口扫描 |
| BR-003 标准互操作 | 对象规范、接口标准 | API-A2A-001～011 | TEST-A2A-001 官方 SDK 黑盒 |
| BR-004 多 Runtime | 编排与 Runtime 适配 | RuntimeAdapter/RuntimeProbe | TEST-RUNTIME-001 四 Adapter smoke |
| BR-005 长任务可观察 | 任务生命周期 | EVT-PROGRESS-001、Task heartbeat | TEST-LONG-001 静默任务/取消/SSE |
| BR-006 断线恢复 | 任务生命周期、接口标准 | GetTask + SubscribeToTask | TEST-RECOVERY-001 客户端/Gateway/Peer 断线 |
| BR-007 幂等执行 | Redis 数据、NATS 适配 | DATA-DEDUPE-001、claim_message | TEST-IDEMP-001 100 并发重复提交 |
| BR-008 多 Agent 观察 | 任务生命周期、Runtime 适配 | Observer rules/policy | TEST-OBSERVER-001 防反馈环与只读默认 |
| BR-009 可运维 | 监控规则 | OBS-ALERT-001～015 | TEST-OBS-001 指标/审计/告警/备份 |
| BR-010 可演进 | 全部专项 | 版本化 URI/Key/Envelope | TEST-VERSION-001 升级与旧协议隔离 |

## 8.2 非功能需求

| 需求 | 设计控制 | 验收 ID |
|---|---|---|
| NFR-001 发现与 presence 时效 | 5s heartbeat、15s suspect、30s offline | TEST-PRESENCE-001 |
| NFR-002 路由开销 | Core/NATS latency histogram 与压测 | TEST-PERF-001 |
| NFR-003 至多一次业务执行 | messageId/payloadHash dedupe + lease | TEST-IDEMP-001 |
| NFR-004 流事件顺序 | eventSequence、JetStream、独立 consumer | TEST-STREAM-001 |
| NFR-005 静默任务 heartbeat | 独立 TaskSupervisor heartbeat | TEST-LONG-001 |
| NFR-006 重启一致性 | Redis AOF、durable consumer、fencing | TEST-RECOVERY-001 |
| NFR-007 隐私与脱敏 | Progress/日志/Tool Policy | TEST-SEC-001 |
| NFR-008 不虚假宣称 HA | 单 Linux SPOF 明示、恢复而非 HA | TEST-DOC-001 文档声明检查 |

---

# 9. 文档集验收

- 九份正式文档均存在且链接可达；
- 主章节、版本记录和权威边界完整；
- A2A 版本、方法、状态和 Agent Card 字段一致；
- NATS Subject、Redis Key、Progress Extension 在各文档中一致；
- 无 tenant/RBAC 功能误入 V1；
- 所有 BR/NFR 均可追踪到设计、契约和验收 ID；
- 所有目标能力都有实施阶段和退出门禁；
- 当前 31 passed / 4 skipped 基线不会被写成完整 A2A 兼容证据。
