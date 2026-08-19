# ADR-038：Redis V1 Key builder 命名空间、组件编码与 bootstrap 模板

> 状态：**Verified for C2 §9.3 步骤2 Key builder bootstrap**
> 日期：2026-08-19
> 适用范围：C2 / 《A2AMesh 开发实施计划》§9.3 步骤2
> 实现成熟度：纯 Key builder bootstrap 已实现、完成门禁并通过精确代码树独立复审；不等于 Redis State Service 整体验收
> 上游候选：`work/c2-state-redis-config-client-fix2`，`HEAD=803c43a728d6a9a95c53c29a6fc37088a61e9587`，`TREE=cf63586560483925567c656cd08e10c5a4f14565`

## 1. 决策目的与权威边界

本 ADR 补齐当前 V1.6 Redis 专项只给出 Key 示例、但未冻结通用 builder API、`mesh_id` Unicode 规范化、组件 codec 和模板闭集的问题。它只约束 C2 实现，不修改已发布的版本化专项，不得覆盖以下权威：

- Redis Key、字段、原子函数和索引：`docs/specs/A2AMesh_Redis状态平面与数据设计_V1.6.md`；
- 官方对象和 ID 语义：`docs/specs/A2AMesh_AgentCard与协议对象规范_V1.6.md`；
- 当前阶段和顺序：`docs/specs/A2AMesh_开发实施计划.md`。

后续发布 Redis 设计新版本时，应把本 ADR 已通过独立复审的部分吸收进新版本；在此之前，本文件是步骤2的实现级补充合同，不是 Redis State Service 整体验收报告。

### 1.1 冻结证据

| 别名 | 路径 | SHA-256 | 行数 |
|---|---|---|---:|
| `REDIS` | `docs/specs/A2AMesh_Redis状态平面与数据设计_V1.6.md` | `2b066f1602b79c79bac49966934622e06f8340c399795970e8e5e62b0c8cbbab` | 1135 |
| `PLAN` | `docs/specs/A2AMesh_开发实施计划.md` | `4bb6b98f8b5812b79931ec914a8135be45be8c7f6888cf3f9b5c57d8d5a1857f` | 1183 |
| `OBJECT` | `docs/specs/A2AMesh_AgentCard与协议对象规范_V1.6.md` | `6bee086bbdddc70d9dd7995a8b43d7c52999184b8e557768e9ab94b202d656e4` | 553 |
| `API` | `docs/specs/A2AMesh_接口请求与响应标准_V1.6.md` | `3d331e9f9745c3f66da7d73e62a451a2ffb2691ada66db06bc374bbe9a4363a3` | 634 |

本表固定 ADR 创建时的权威输入快照，不是当前文件的滚动 manifest；后续实施状态回写不得重算本表并形成 `PLAN`↔ADR 自引用。

### 1.2 实现 checkpoint、门禁与独立复审证据

截至 2026-08-20 02:05（Asia/Shanghai），本 ADR 的纯 Key builder bootstrap 已在以下精确代码树闭合：

- 代码提交：`eb254c0e24da2d1200475b1d2a8a07b323134658`；代码 tree：`f0df9a059e415f36569f5565e1b692c887c2c519`；parent：`599b61f0df0b70f23d790aaf76ee868b74b8edad`；分支：`work/c2-state-key-builder`。
- 代码范围：`src/a2amesh/state/key_builder.py`、`src/a2amesh/state/__init__.py`、`tests/unit/state/test_key_builder.py`，其中格式 checkpoint 的提交相对 parent 仅改变 `key_builder.py` 与 `test_key_builder.py`，两文件 AST 等价。
- 精确门禁：touched-file `ruff format --check` 为 `3 files already formatted`；`ruff check src tests` 为 `All checks passed!`；focused 为 `73 passed`；全量 `pytest -q -W error` 为 `641 passed, 8 skipped`；独立 NFC/恶意输入/typed codec/17-template/import-purity probe 通过。
- 独立复审：`deleg_e66f62a0`，`status=completed`、`exit_reason=completed`，复审同一 HEAD/tree，`VERDICT=PASS`，`P1=0 P2=0 P3=0`，结束时工作树 clean。

以上 Verified 只覆盖纯、无 I/O 的 Key builder bootstrap。它不覆盖 Redis client/config、Lua/Redis Function、`claim_auth_request`、`claim_message`、原子多 Key mutation、Redis Cluster/runtime、NATS、重启/并发或生产部署；这些边界仍由本 ADR 第6节和 `UD-KB-001`～`UD-KB-005` 约束。

## 2. 已有规范事实

| 分类 | 规范事实 | 证据 |
|---|---|---|
| NF | V1 Key 前缀为 `a2am:v1:{<mesh-tag>}:...`；`{default}`只是示例，实际值来自受信配置 `mesh_id` | `REDIS` §5，L95-L97 |
| NF | V1 是单 Mesh；tenant 不进入 Principal 或 Redis Key | `REDIS` §5.8，L269-L276；`OBJECT` §3.7 |
| NF | Principal 原文不得进入 Redis Key；Key 使用 SHA-256/base64url hash | `REDIS` §5.8，L269-L275 |
| NF | `agentId` 的生产语法为 `^[a-z0-9][a-z0-9-]{0,62}$` | `OBJECT` §3.5，L184-L188；`API` §2.5，L103-L107 |
| NF | Task ID 由服务端生成；Message ID 是业务幂等输入 | `REDIS` §3，L43-L52；`OBJECT` §2，L51-L55 |
| NF | 大版本不得原地改变 Key 语义；新大版本使用新前缀，例如 `a2am:v2:` | `REDIS` §12，L1022-L1030 |
| TR | 步骤2必须先于 `claim_auth_request` 和 `claim_message.lua` | `PLAN` §9.3，L530-L536 |
| TR | 同 messageId 的并发、payload conflict、跨实例 AuthProof replay 和原子多 Key mutation 是后续验收要求 | `PLAN` §9.4，L557-L584 |

## 3. 决策

### 3.1 命名空间和 schema version

1. builder 固定输出 `bytes`，前缀固定为 UTF-8：

   ```text
   a2am:v1:{<normalizedMeshId>}:
   ```

2. 公共 API 只接受 schema version `v1`；未知版本 fail closed。调用方不能传任意前缀、tenant 或 suffix。
3. `mesh_id` 只来自部署级受信配置，不接受 per-request override。
4. `mesh_id` 使用 Unicode NFC 规范化。规范化后的值必须：
   - 是 exact `str`，非空；
   - 不超过 128 个 Unicode code point（沿用步骤1 `RedisConfig` 的已有限制并把线性化点冻结在 builder）；
   - 可按 strict UTF-8 编码；
   - 不含 Unicode control character、任何 whitespace、`{` 或 `}`。
5. NFC 等价输入必须生成完全相同的 bytes；builder 保存并使用规范化值，不回写环境变量。
6. 冒号可以出现在 `mesh_id` 中，因为 Redis hash tag 由唯一一对花括号界定；花括号本身禁止。该选择只影响可读 prefix，不开放任意组件拼接。

### 3.2 组件类型与 codec

renderer 不接受 raw `str` component。每个动态组件必须先构造不可变 `KeyPart`，并携带下列 closed codec 之一；模板同时校验 component 名称和 codec：

| Codec | 规则 | 首批用途 |
|---|---|---|
| `SAFE_TOKEN` | exact `str`，ASCII `[A-Za-z0-9._~-]{1,128}` | 服务端生成的 task/context ID、当前 delivery profile 接受的 message ID |
| `AGENT_ID` | `^[a-z0-9][a-z0-9-]{0,62}$` | target/owner agent |
| `SHA256_BASE64URL` | canonical、无 padding、解码后恰 32 bytes，即 43 个 base64url 字符 | caller/principal/signer/request hash |
| `TASK_STATE` | `^TASK_STATE_[A-Z][A-Z0-9_]{0,51}$` | Task state index；业务层仍须验证为官方已知状态 |
| `POSITIVE_SEQUENCE` | exact `int`，`1..9007199254740991`，编码为无前导零十进制 ASCII | outbox eventSeq |

边界规则：

- `bool` 不是 integer；subclass 或 hostile object 不得通过 exact-type gate。
- 错误只包含模板名、字段名和期望 codec，不回显 component 原值。
- `SAFE_TOKEN` 是当前 C2 bootstrap delivery profile，不宣称是官方 A2A 对 opaque ID 的通用语法。超出该字符集的官方 `messageId/taskId/contextId` 在未来冻结可逆 codec 前 fail closed，禁止 builder 自行 percent/base64 编码。
- Principal、Signer 或 Request 原文永远不能通过 `SAFE_TOKEN` 代替 hash；模板必须要求 `SHA256_BASE64URL`。
- builder 不负责 Principal 规范化、hash 计算、Task ID 生成或业务状态校验；它只验证已经由上游权威逻辑产生的 encoded part。

### 3.3 最小 API

```text
KeyPart.safe_token(value)
KeyPart.agent_id(value)
KeyPart.sha256_base64url(value)
KeyPart.task_state(value)
KeyPart.positive_sequence(value)

RedisKeyBuilder(mesh_id, schema_version="v1")
RedisKeyBuilder.render(kind: KeyKind, **parts: KeyPart) -> bytes
```

必须满足：

- `KeyKind` 是 closed enum；
- 每种 kind 要求 exact component name set；missing、extra、unknown kind、codec mismatch 全部 fail closed；
- 无 `raw_suffix`、`join`、任意 segment list 或模板字符串入口；
- 无 Redis I/O、Lua、ID 生成、Cluster 路由或业务 mutation；
- 所有输出只有一个由 builder 生成的 `{mesh-tag}`，动态 component 不能包含 `:`, `{`, `}`。

### 3.4 Bootstrap template closed set

本步骤只注册下一消费者 `claim_auth_request` / `claim_message` 原子写入所需且已在 `REDIS` §5 明确定义的模板：

| `KeyKind` | Exact suffix | Parts |
|---|---|---|
| `AUTH_REPLAY` | `auth:replay:<signerHash>:<requestIdHash>` | `signer_hash=SHA256_BASE64URL`, `request_id_hash=SHA256_BASE64URL` |
| `DEDUPE` | `dedupe:<callerHash>:<targetAgentId>:<messageId>` | `caller_hash=SHA256_BASE64URL`, `target_agent_id=AGENT_ID`, `message_id=SAFE_TOKEN` |
| `TASK` | `task:<taskId>` | `task_id=SAFE_TOKEN` |
| `TASKS_UPDATED` | `tasks:updated` | none |
| `TASKS_STATE` | `tasks:state:<state>` | `state=TASK_STATE` |
| `CONTEXT_TASKS` | `context:<contextId>:tasks` | `context_id=SAFE_TOKEN` |
| `CALLER_TASKS` | `caller:<principalHash>:tasks` | `principal_hash=SHA256_BASE64URL` |
| `AGENT_TASKS` | `agent:<agentId>:tasks` | `agent_id=AGENT_ID` |
| `OUTBOX_EVENT` | `outbox:event:<taskId>:<eventSeq>` | `task_id=SAFE_TOKEN`, `event_seq=POSITIVE_SEQUENCE` |
| `OUTBOX_DUE` | `outbox:due` | none |
| `OUTBOX_TASK` | `outbox:task:<taskId>` | `task_id=SAFE_TOKEN` |
| `ADMISSION_GLOBAL` | `admission:global` | none |
| `ADMISSION_PRINCIPAL` | `admission:principal:<principalHash>` | `principal_hash=SHA256_BASE64URL` |
| `ADMISSION_PRINCIPALS` | `admission:principals` | none |
| `ADMISSION_PRINCIPAL_FIFO` | `admission:principal:<principalHash>:fifo` | `principal_hash=SHA256_BASE64URL` |
| `ADMISSION_TASK` | `admission:task:<taskId>` | `task_id=SAFE_TOKEN` |
| `DISPATCH` | `dispatch:<taskId>` | `task_id=SAFE_TOKEN` |

`REDIS` §5.2 的旧示例 `caller:<principal>:tasks` 与 §5.8 “Principal 原文不进入 Redis Key”冲突时，后者是明确安全规则；本 ADR 将动态字段冻结为 `principalHash`。这不是把 Principal 原文改名，而是要求 canonical SHA-256/base64url part。

所有其他已记录 schema 都属于 registered-but-deferred set。调用方请求未注册 kind 必须失败，不允许通过任意 suffix 临时补洞。

## 4. 明确未决策/后续阻断

| ID | 未决策项 | 影响 |
|---|---|---|
| `UD-KB-001` | `admission:principal:*:fifo` 要求单调 enqueue sequence，但 V1.6 未定义分配该序列的 Redis counter Key | 步骤3实现 claim_message 前必须新增权威原子来源，不能用 wall clock 猜测 |
| `UD-KB-002` | 官方 opaque message/task/context ID 超出 `SAFE_TOKEN` 时的可逆编码尚未冻结 | 当前 bootstrap fail closed；不能宣称覆盖所有官方合法 opaque string |
| `UD-KB-003` | 全量 §5 schema 的 template registry 尚未实现 | 后续按首个消费者分批加入，每批独立 golden fixture 和 review |
| `UD-KB-004` | Redis Cluster 的 MOVED/ASK/CROSSSLOT 运行行为不在 V1 当前范围 | 本步骤只保证一个 builder 的 Key 共享相同 hash tag，不宣称 Cluster 验收 |
| `UD-KB-005` | 服务端 Task/context ID 的具体生成格式未在 Redis 专项冻结 | builder 只接受已生成的 approved part，不生成 ID |

## 5. 测试合同

### 5.1 ASSERT_NOW

1. exact v1 prefix、bytes 类型和每个 bootstrap template 的 golden fixture；
2. composed/decomposed Unicode `mesh_id` 生成相同 bytes；
3. mesh empty/超长/whitespace/control/braces/lone-surrogate/非 str 拒绝；
4. missing/extra/raw component、unknown kind、wrong codec 拒绝；
5. hash canonicality、agentId、TaskState、positive JSON-safe sequence 边界；
6. raw Principal 无法替代 hash，错误不回显恶意原值；
7. 每个输出恰有一个相同 `{mesh-tag}`，不含 `...`、tenant 或 caller-provided suffix；
8. import 和 render 不连接 Redis，不导入 redis-py client。

### 5.2 DECISION_REQUIRED

以下测试在对应决策关闭前不得伪造为绿色能力：

- arbitrary opaque ID 的可逆编码 golden fixtures；
- admission enqueue sequence counter 的原子 fixture；
- Redis Cluster MOVED/ASK/CROSSSLOT；
- 全量 §5 deferred template coverage；
- v2 migration 的 old-read/new-write 运行测试。

## 6. 非目标和验收边界

本 ADR/步骤2不实现也不证明：

- Redis client/config 步骤1独立复审 PASS；
- `claim_auth_request`、`claim_message`、Lua/Redis Function；
- Task/索引/outbox/admission/dispatch 原子性；
- Redis persistence/restart、双实例竞态或 Cluster；
- NATS/JetStream、Gateway、Runtime、三机部署或生产就绪。

代码、提交后门禁和精确 tree 独立复审均完成后，步骤2纯 Key builder bootstrap 已获得自己的 PASS；该 PASS 仍不得外推到上述非目标。
