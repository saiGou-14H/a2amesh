# ADR-039：C2 步骤3A0 AuthProof Replay Claim 基础设施

> 状态：**Verified for C2 §9.3 步骤3A0 AuthProof replay claim**
> 日期：2026-08-20
> 适用范围：C2 / 《A2AMesh 开发实施计划》§9.3 步骤3 的 **A0 `claim_auth_request`**
> 实现成熟度：一 Key AuthProof replay claim 已实现、完成精确门禁并通过完整范围独立复审；不等于完整 `claim_message` 或 Redis State Service 整体验收。
> 上游已验证 Key builder：`work/c2-state-key-builder`，代码 `eb254c0e24da2d1200475b1d2a8a07b323134658` / tree `f0df9a059e415f36569f5565e1b692c887c2c519`。

## 1. 目的、范围与权威边界

C2 §9.3 的下一项同时列出 `claim_auth_request` 与 `claim_message.lua`。入口顺序、跨实例 replay 和原子业务 claim 是强约束；因此不能把 Gateway 进程内去重、单客户端缓存或纯 Python 合同误称为 State replay claim。

本 ADR 只冻结可独立线性化的 A0：对已经通过密码学与路由前置验证的受保护 State 请求，以 Redis 原子 `SET ... NX PX` 写入一个短期 replay tombstone。A0 成功是进入任意业务 State mutation 的必要前置，但**不是** Task、dedupe、admission、outbox 或 dispatch 的提交。

本 ADR 不覆盖或修改下列权威文档：

- Redis schema/原子函数：`docs/specs/A2AMesh_Redis状态平面与数据设计_V1.6.md`；
- A2A 对象和 opaque ID 语义：`docs/specs/A2AMesh_AgentCard与协议对象规范_V1.6.md`；
- 入口、幂等与脱敏 wire 语义：`docs/specs/A2AMesh_接口请求与响应标准_V1.6.md`；
- 内部 AuthProof/可信 AuthContext：`docs/specs/A2AMesh_A2A协议与NATS集成适配设计_V1.6.md`；
- 实施阶段：`docs/specs/A2AMesh_开发实施计划.md`。

### 1.1 本决策的权威输入快照

| 别名 | 路径 | SHA-256 | 行数 |
|---|---|---|---:|
| `REDIS` | `docs/specs/A2AMesh_Redis状态平面与数据设计_V1.6.md` | `2b066f1602b79c79bac49966934622e06f8340c399795970e8e5e62b0c8cbbab` | 1135 |
| `PLAN` | `docs/specs/A2AMesh_开发实施计划.md` | `b85e73d6c0356f6e0b9f9c36fe107ef65aee37e3424f394ad373b0fab741665f` | 1184 |
| `OBJECT` | `docs/specs/A2AMesh_AgentCard与协议对象规范_V1.6.md` | `6bee086bbdddc70d9dd7995a8b43d7c52999184b8e557768e9ab94b202d656e4` | 553 |
| `API` | `docs/specs/A2AMesh_接口请求与响应标准_V1.6.md` | `3d331e9f9745c3f66da7d73e62a451a2ffb2691ada66db06bc374bbe9a4363a3` | 634 |
| `NATS` | `docs/specs/A2AMesh_A2A协议与NATS集成适配设计_V1.6.md` | `bf4404611ada44299de2027f42f2e14bb2b2cf1edae759884a7e15937f66da8f` | 813 |

本表是 ADR-039 创建时输入快照，不是滚动 manifest；后续实施状态记录不得回写、重算或替换本表。

### 1.2 实现 checkpoint、门禁与独立复审证据

截至 2026-08-20 17:45（Asia/Shanghai）本证据写回时，本 ADR 的 A0 已在连续代码链的最终精确树闭合：

- A0 实现提交：`50ea076acdbc46953c5b397e730ac00d8ce823dc`；tree：`1757a34d642893518f3dad5a0624f1e04d30d114`；parent（本 ADR 合同提交）：`0f585f09eeccc684ae8d2b64e3f1224360ffe82a`。
- CI schema 修复且最终验证的代码提交：`aeacd758c2146b3cc4bd52bec4c37b8c6568fd21`；tree：`ce49c3b7f0177cfcd6f63507c40ffd562326af61`；parent：`50ea076acdbc46953c5b397e730ac00d8ce823dc`；分支：`work/c2-state-claim-contract-p1-ci-schema`。
- 闭合 payload 是 `0f585f0..aeacd75` 的完整 17 文件、`782 insertions(+), 21 deletions(-)`；它覆盖 Lua、runner、资源打包、unit/真实 Redis integration、CI contract 和最终 GitHub Actions Redis service schema 回归，不把仅 `HEAD^..HEAD` 的两文件修复误作完整 A0 审阅。
- 精确本地门禁：`actionlint 1.7.12` 从 SHA-256 为 `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8` 的官方 archive 重解包后通过；隔离 pinned Redis 默认命令为 `["redis-server"]`；A0 focused Redis/`SCRIPT FLUSH`/CI-contract 为 `18 passed`；`core-gates` 为 `8/8`；全量 pytest 为 `661 passed, 8 skipped`；结束时精确代码树 clean。
- 权威完整范围独立复审：`deleg_166bd3c3`，`status=completed`、`exit_reason=completed`；自行绑定并复算同一 `HEAD/tree/parent`、完整 17 文件范围与 clean 状态，明确 `VERDICT=PASS`、`P1=0 P2=0 P3=0`。
- 保留但不扩权的审阅历史：`deleg_8b868b58` 因 API timeout/`max_iterations` 无明确 verdict，故为 `INCONCLUSIVE`；Codex CLI read-only preflight 因 401 无 verdict，故为 `INCONCLUSIVE`；`deleg_56e9f2bb` 的 `PASS` 只审 `HEAD^..HEAD` 的 CI schema 修复，不能替代本节完整范围 verdict。

以上 Verified 只覆盖受信入口后的一 Key `claim_auth_request` replay tombstone、其 Lua/runner/resource/测试和 CI schema 修复。它不覆盖 `claim_message`、Task/dedupe/admission/outbox/dispatch、Credential/Alias/Grant、业务结果 ledger、外部 wire mapping、多 Key Redis Cluster、Redis restart、NATS、生产 ACL/部署或生产就绪；这些边界仍由第4节、第5节和 `UD-039-001`～`UD-039-008` 约束。

### 1.3 已有规范事实

| 分类 | 规范事实 | 证据 |
|---|---|---|
| NF | 每个受保护 State RPC 在业务函数前调用 `claim_auth_request`；输入包含 signer、requestId、authProofDigest、configGeneration、expiresAt、operation、target、replySubject 与已验证标记。 | `REDIS` §6.18，L818-L820 |
| NF | Replay Key 是 `auth:replay:<signerHash>:<requestIdHash>`，值记录 authProof digest/config generation，TTL 是 `expiresAt + clockSkew`。 | `REDIS` §5.18，L442-L450 |
| NF | replay Key 已存在时，无论 digest 同/异都不进入业务；内部可记 `AUTH_PROOF_REPLAYED`，所有 wire 统一映射 `AUTH_PROOF_INVALID`。 | `REDIS` §6.18，L818-L820；`API` §19.1，L593-L603 |
| NF | 认证入口必须验证 signer 授权、签名、issuedAt/expiresAt、requestId/deadline、target Subject 和 reply inbox；payload 自报身份不能覆盖可信身份。 | `NATS` §6.5，L297-L310；`NATS` §16.7，L757-L761 |
| NF | response 丢失时使用新的 requestId/issuedAt/AuthProof，但保持业务幂等 ID；replay 防护不是业务结果缓存。 | `NATS` §16.7，L757-L761；`API` §16，L530-L548 |
| TR | 跨 State 实例同 requestId 同/异 digest replay 必须拒绝，且不进入业务函数。 | `REDIS` §14，L1095-L1097；`API` §19.2，L605-L610 |
| IE | ADR-038 的 verified builder 已注册 `AUTH_REPLAY`，输出同一 mesh hash-tag 下的 exact UTF-8 bytes；其自身不执行 Redis I/O。 | `docs/decisions/ADR-038_Redis_Key_Builder_V1.md` §3.4，L115-L141；`src/a2amesh/state/key_builder.py` L19-L242 |

## 2. 决策：A0 受信入口与原子语义

### 2.1 受信边界与前置条件

只有 State Service 的受信入口可以调用 A0。入口先完成以下全部可能失败的检查，失败时**不得调用 Redis 脚本、不得创建 replay Key，也不得进入业务函数**：

1. 输入结构/版本和空 tenant；
2. signer 对 operation、target 和 replySubject 的授权；
3. AuthProof 密码学验证、issuedAt/expiresAt、requestId、deadline 与 reply inbox 绑定；
4. 根据受信配置得到的 active `configGeneration`；
5. canonical signer/request ID 的 SHA-256/base64url hash 与 authProof 的 lowercase SHA-256 digest。

A0 的 Python 调用输入只接受已校验的：

```text
signerHash: KeyPart(SHA256_BASE64URL)
requestIdHash: KeyPart(SHA256_BASE64URL)
authProofDigest: lowercase SHA-256 hex
configGeneration: positive JSON-safe integer
replayExpiresAtMs: absolute trusted State-time millisecond deadline
stateNowMs: trusted State-time millisecond snapshot
```

runner 在调用脚本前计算 `replayTtlMs = replayExpiresAtMs - stateNowMs`；仅 `1..9007199254740991` 的整数有效。`replayExpiresAtMs` 必须已包含权威规定的 clock-skew 窗口。脚本永远不用 caller wall clock、不会接受 raw signer/requestId、不会接受可覆盖的 per-request mesh ID。

### 2.2 线性化结果

A0 只返回受信内部的 closed 结果：

```text
CLAIMED
REPLAYED
CORRUPT_REPLAY_KEY
```

| 结果 | 线性化语义 | 后续行为 |
|---|---|---|
| `CLAIMED` | 该 replay Key 首次被写入，TTL 从本次 State-time snapshot 起算 | 可进入一个业务函数；业务函数的成功/失败不改变 A0 结果 |
| `REPLAYED` | Key 已是 STRING；**不读取、不比较、不回显**旧 digest 或 generation，也不刷新 TTL | 不进入任何业务函数；受信适配层可记录 `AUTH_PROOF_REPLAYED`，wire 固定映射 `AUTH_PROOF_INVALID` |
| `CORRUPT_REPLAY_KEY` | Key 存在但 type 不是 STRING | fail closed、零业务写入，按 State storage fault 处理；不得伪装为可继续的 auth retry |

业务函数在 A0 `CLAIMED` 后拒绝能力、大小、admission、generation 或其他前置条件时，A0 tombstone 仍保留到自己的 TTL。合法 transport retry 必须使用新 requestId/AuthProof；它随后依靠 `messageId` 等业务幂等键处理业务结果。这是两个独立线性化点，不把 A0 当业务 rollback 或结果 ledger。

### 2.3 Redis 脚本 ABI 与加载

A0 使用计划所列 Lua 源文件和 Redis `SCRIPT LOAD` + `EVALSHA`，而不是把任意 Lua 文本暴露给调用方：

```text
resource: a2amesh.state.scripts/claim_auth_request.lua
script logical name: a2am.claim_auth_request.v1
transport command: EVALSHA
```

`SCRIPT LOAD` 返回 Redis 协议规定的 SHA-1 source identifier。runner 本地用 SHA-1（仅作 Redis 脚本识别，`usedforsecurity=False`）从 exact UTF-8 source 计算并比对返回值；业务、身份和数据完整性摘要仍只使用 SHA-256。

ABI 固定如下：

| 位置 | 值 | 验证 |
|---|---|---|
| `KEYS[1]` | 由 `RedisKeyBuilder.render(KeyKind.AUTH_REPLAY, ...)` 产生的 bytes Key | exact builder output；唯一 `{mesh-tag}` |
| `ARGV[1]` | `authProofDigest` | lowercase 64-char SHA-256 hex |
| `ARGV[2]` | `configGeneration` | canonical positive decimal JSON-safe integer |
| `ARGV[3]` | `replayTtlMs` | canonical positive decimal JSON-safe integer |
| return `1` | `CLAIMED` | script 在 `SET ... NX PX` 返回 OK 后返回 |
| return `0` | `REPLAYED` | existing STRING；不得更新 value/TTL |
| return `-1` | `CORRUPT_REPLAY_KEY` | existing non-STRING；零写入 |

脚本在唯一写之前用 `TYPE KEYS[1]` 预检；只允许 `none` 或 `string`。写入的 exact bytes 是：

```text
v1:<authProofDigest>:<configGeneration>
```

该值不是 wire response，也不允许普通 caller 读取。它只满足 `DATA-AUTH-REPLAY-001` 的 digest/generation 事实需求。脚本不会执行网络、时间、签名、权限、业务 Key、TTL refresh、`SCRIPT FLUSH` 或其他 Redis 管理命令。

`NOSCRIPT` 证明当前 `EVALSHA` 没有执行；runner 可以在相同 key/args 下 reload source 后**最多重试一次**。普通连接错误、timeout、取消或未知 Redis 错误无法证明脚本未提交，必须原样 fail closed，不能自动重试 A0 或进入业务函数。生产 ACL 不授予业务调用方 `SCRIPT FLUSH`；测试可在隔离 Redis 中验证一次 `NOSCRIPT` reload。

A0 只有一个 Key，因此不构成多 Key Cluster 原子性或 `CROSSSLOT` 验收。后续 `claim_message` 的每个 Key 必须通过同一个 `RedisKeyBuilder` 生成并在调用前验证共享同一 mesh hash-tag。

## 3. 实现切片与测试合同

第一个代码 checkpoint 只允许创建以下闭集：

```text
src/a2amesh/state/scripts/claim_auth_request.lua
src/a2amesh/state/scripts/__init__.py
src/a2amesh/state/script_runner.py
tests/unit/state/test_auth_replay_claim.py
tests/integration/state/test_auth_replay_claim_redis.py
```

以及为 Lua wheel/sdist resource、lazy facade export、测试 fixture 所必需的最小打包/测试配置。它不得在同一 checkpoint 引入 `claim_message.lua`、Task writer、grant/credential reader、NATS handler、dispatch worker 或生产部署代码。

必须通过的 RED→GREEN 测试：

1. Key 只能来自 `KeyKind.AUTH_REPLAY`，纯 Key builder output 是 bytes，raw signer/requestId 不进入 Key；
2. 首次调用只创建一个 STRING，保存 exact `v1:<digest>:<generation>`，并返回 `CLAIMED`；
3. 同 signer/requestId 的同/异 digest 都返回 `REPLAYED`，value 与 TTL 均不改变；
4. non-STRING collision 返回 `CORRUPT_REPLAY_KEY` 且不写业务 Key；
5. 过期/零/超 JSON-safe TTL、非法 digest/generation/KeyPart 在 Redis I/O 前拒绝，错误不回显攻击性值；
6. 两个独立 runner/client 至少 100 个并发请求只得到一个 `CLAIMED`；
7. 隔离 Redis `SCRIPT FLUSH` 触发一次 `NOSCRIPT` 后，runner reload 并只执行一次合法 A0；
8. source resource 同时包含在 sdist/wheel，SHA-1 source identity 与 Redis `SCRIPT LOAD` 返回值一致；
9. Redis unavailable、普通 command error、timeout/cancellation 不自动重试，业务 callback 不可被触发；
10. wire mapping仍由未来 Binding 层实施；本切片仅测试内部结果，绝不宣称 HTTP/gRPC/JSON-RPC/NATS 端到端完成。

集成测试使用临时、隔离的 Redis 实例；具体镜像 digest/端口/生命周期必须由测试 fixture 显式记录。没有成功的真实 Redis gate 时，任何 fake pool 或 unit test 均不能被表述为 Redis 原子性 PASS。

## 4. 明确不在本切片解决的步骤3问题

| ID | 未决/后续项 | 本切片处理 |
|---|---|---|
| `UD-039-001` | 官方 opaque `messageId/contextId` 的可逆 Key codec；ADR-038 的 `SAFE_TOKEN` 仅是 bootstrap profile。 | 不使用这些 ID；完整 `claim_message` 前必须冻结。 |
| `UD-039-002` | Task/Context/dispatch ID 的 State CSPRNG/ULID 格式、碰撞和 response-lost 绑定。 | 不生成 ID。 |
| `UD-039-003` | `claim_message` 的 Credential/Alias/Grant 同代际复核 Key 与 Lua/Redis ABI。 | 入口只完成 A0，不伪造 A1 授权。 |
| `UD-039-004` | global/per-Principal enqueue sequence counter、FIFO score、queue admission 限额和 selector。 | 不写 admission。 |
| `UD-039-005` | `caller:<principalHash>:tasks` 与 `principal:<principalHash>:tasks` 的唯一写路径。 | 不写 caller Task index。 |
| `UD-039-006` | 初始 `taskVersion/eventSeq`、outbox head/due 初值及业务 exact-result ledger。 | 不写 Task/outbox；A0 不是业务结果缓存。 |
| `UD-039-007` | `BLOCKED_ADMISSION` 的 dispatch contract 与 `dispatch:due` 后续 selector。 | 不写 dispatch。 |
| `UD-039-008` | 多 Key same-slot、MOVED/ASK/CROSSSLOT、Redis persistence/restart 和 production ACL/deployment。 | 只证明一 Key A0，保留后续 gate。 |

## 5. 非目标和验收边界

即使 A0 已在本 ADR 第1.2节所绑定的精确代码树获得 Verified，也不得宣称已经完成：

- 完整 C2 §9.3 步骤3 或 `claim_message.lua`；
- `resolve_principal`、Credential/Alias/Grant、capability 或 admission；
- Task/dedupe/Context/index/outbox/dispatch 原子提交；
- A2A/MCP/Gateway/NATS handler、外部 wire error mapping或 response bytes ledger；
- 多 Key Redis Cluster、Redis restart、NATS/JetStream、生产 ACL、三机部署或生产就绪。

后续 A1 必须从本 ADR 的 A0 确定线性化点开始，单独冻结完整 write set、ID/codec、admission、outbox/dispatch初值和响应丢失合同，并在新的精确 commit/tree 上重新门禁与独立复审。
