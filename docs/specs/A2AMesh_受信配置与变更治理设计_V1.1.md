# A2AMesh 受信配置与变更治理设计 V1.1
> 文档ID：`A2AM-CONFIG-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：签名配置 bundle、generation、校验、暂存、激活、回滚、撤销、组件就绪与 Card publisher ownership
> 目标读者：架构、后端、Gateway、Peer、安全、测试、运维
> 评审状态：文档自检通过；配置控制器、签名轮换与故障演练待完成
> 最后更新：2026-08-14
> 适用产品版本：A2AMesh V1
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 首次版本：V1.0
> 替代版本：V1.0
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh 所有“受信配置”的统一生命周期。Credential/Alias、capability grant、Card publisher 候选者、delivery profile、Artifact policy、Runtime/Tool policy 必须来自同一可验证 generation，不能由各组件分别读取未签名 YAML 后自行解释。

任一 Mesh 同时只能存在一个单一 active generation。`STAGED` 仅用于无流量校验，任何请求、授权、Card、Artifact 或 Runtime 执行都不得把 staged 内容与 active 内容混合使用。

本文不建设用户、角色、组织、通用 RBAC 或强制 Web 管理后台。配置运维通过独立机器 Credential、最小 `ops.config.*` capability 和 API/CLI 完成。当前代码尚未实现本合同，所有 READY、ACTIVE、回滚和故障结论均待实现验证。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立签名 bundle、单 active generation、两阶段激活、回滚撤销、启动 fail closed 和 publisher lease 合同 |
| V1.1 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，受信配置领域合同不变 |

---

# 2. 权威边界

| 内容 | 权威来源 | 运行时物化 |
|---|---|---|
| bundle 内容与签名 | 不可变版本化配置制品 | 本地只读缓存 + Redis generation 快照 |
| 当前 active generation | Config Controller 的 Redis CAS pointer | 所有组件只消费该 generation |
| Credential/Alias/Grant | active bundle | State Service 原子物化索引 |
| Card publisher 候选者 | active bundle | Redis lease + fencing 选出唯一 active publisher |
| delivery profile 期望 | active bundle | 组件 READY 和兼容门禁决定实际可广告子集 |
| Artifact/Runtime/Tool policy | active bundle | 对应服务按 generation fail closed 执行 |
| secret 值 | OS Secret Store/受保护文件/外部 Secret Manager | bundle 只保存 `secretRef` 和版本约束 |
| 审计 | append-only config audit | 不可由新 bundle 覆盖历史 |

根信任公钥、允许的签名算法和紧急撤销公钥必须通过部署镜像/主机受保护配置固定，不能由待验证 bundle 自己替换自己。密钥轮换采用旧根签新根或离线批准的双签过渡，具体操作必须留审计证据。

---

# 3. Bundle 合同

## 3.1 Envelope

bundle 是 RFC 8785 canonical JSON，使用允许算法的 JWS 签名。至少包含：

```json
{
  "bundleId": "cfg_opaque",
  "generation": 42,
  "previousGeneration": 41,
  "meshId": "default",
  "issuedAt": "2026-08-14T00:00:00Z",
  "notBefore": "2026-08-14T00:05:00Z",
  "expiresAt": "2026-09-13T00:00:00Z",
  "schemaVersion": "1.0",
  "contentSha256": "...",
  "credentials": [],
  "aliases": [],
  "grants": [],
  "cardPublishers": [],
  "deliveryProfile": {},
  "artifactPolicy": {},
  "runtimePolicy": {},
  "toolPolicy": {},
  "secretRefs": []
}
```

规则：

1. `generation` 为正整数且严格大于所有已接受 generation；不能按时间戳猜测新旧。
2. 正常变更的 `previousGeneration` 必须等于当前 active generation；CAS 冲突返回 409。
3. `meshId` 必须与主机启动信任根配置一致；不允许一个 bundle 跨 Mesh 激活。
4. `notBefore < expiresAt`，组件时钟漂移超过门限时不能激活。
5. bundle 不含 Token、NKey seed、私钥、Redis 密码或对象存储密钥，只含 secret reference。
6. 未知必选字段、未知 schema major、重复 ID、alias 环、无目标 Credential、grant 通配缺省和 publisher 空候选均校验失败。
7. 相同 `generation` 只接受相同 `contentSha256`；同 generation 不同内容是安全冲突。

## 3.2 内容最小约束

| 区域 | 必须校验 |
|---|---|
| `credentials` | 稳定 credentialId、类型、Principal、状态、有效期、rotationGroup、secretRef |
| `aliases` | source/target 稳定、单跳、无环、不可改指历史 owner |
| `grants` | Principal、targetAgent、operation/skill/toolRisk/workspaceAlias 全维明确，无隐式通配 |
| `cardPublishers` | agentId、允许 instanceId 候选、lease TTL、Card 输入引用 |
| `deliveryProfile` | `CORE/INTEROP/EXTENDED` 目标子集、依赖组件和门禁证据引用 |
| `artifactPolicy` | inline/对象上限、MIME、URL TTL、保留、bucket alias、扫描策略 |
| `runtimePolicy` | Runtime 版本范围、并发、超时、workspace alias、SideEffectAdapter |
| `toolPolicy` | registry、risk、参数 schema、网络/文件边界、side-effect 分类 |

配置只能请求发布能力，不能替代实现/兼容验收。即使 bundle 请求 `INTEROP`，Card 也只能在对应组件 READY 且实施计划门禁证据有效后广告 gRPC/Push；MCP 永远不作为 A2A Binding。

---

# 4. 生命周期

## 4.1 状态机

```text
submitted ── verify/schema/semantic ──▶ VALIDATED ── persist immutable ──▶ STAGED
    └──────────────── failure ────────▶ REJECTED

STAGED ── required components READY + CAS ──▶ ACTIVE ── newer activation ──▶ SUPERSEDED
   └──────────────── validation/timeout ────▶ REJECTED

ACTIVE(g42) ── activate g43 with rollbackOfGeneration=42 ──▶ ROLLED_BACK(g42) + ACTIVE(g43)
```

`ROLLED_BACK` 标记被回滚离场的 generation；承载恢复内容的新 generation 仍是 `ACTIVE`。active pointer 不允许降回较小 generation。任何恢复旧内容都必须创建、签名、校验和激活更高 generation，并记录 `rollbackOfGeneration`、来源 content hash 和原因。

## 4.2 Validate

Config Controller 验证签名链、canonical hash、schema、时间窗、meshId、generation/CAS、引用完整性、alias/grant/publisher 约束、secretRef 可解析性和目标组件兼容范围。validate 是只读操作，不写运行时索引。

## 4.3 Stage

stage 把已验证 bundle 作为不可变记录写入 State，并发布 `ConfigStaged`。各组件读取候选 generation，在不改变流量的情况下检查：

- 本组件认识 schema 和所有必选字段；
- secretRef 存在且权限正确，但不把 secret 返回 Controller；
- 外部依赖可达、目标 bucket/Runtime/tool registry 可验证；
- 从 active 到 staged 的迁移可执行且不会破坏历史 Task；
- 本地缓存落盘并验证签名/hash。

组件写带 TTL 的 READY/NACK：`componentType/instanceId/generation/contentSha256/status/reasonCode/observedAt/expiresAt`。自由文本只能用于诊断，Controller 只根据稳定 reason code 裁决。

## 4.4 Activate

激活必须满足：

1. bundle 仍在时间窗内，签名和 hash 未变化。
2. `previousGeneration` 仍等于 active pointer。
3. 交付剖面所需组件类型至少一个健康实例 READY；State、Gateway/Core 和 Config Controller 自身为必选。
4. 影响 Agent 的 Peer/Runtime、Card publisher、Artifact Store 等按 bundle dependency matrix READY。
5. 无 NACK、无缺失 secretRef、无未处理迁移冲突。

Controller 以单个 Redis CAS 原子写 active pointer、activation audit 和 outbox。各服务观察 active event 后只启用该 generation；不能组合 `generation 41` 的 Credential 与 `generation 42` 的 grant。

## 4.5 Supersede、rollback 与 revocation

- 新 generation 激活后，旧 generation 标记 `SUPERSEDED`，保留至少 24 小时只读回滚窗口；历史 Task 继续记录创建时 generation。
- rollback 生成更高 generation，内容可引用旧 bundle 的 hash，但需重新校验当前 secretRef、时间和组件 READY。
- Credential/grant 紧急撤销必须使用签名的更高 generation 或签名 emergency revocation bundle；不允许手工改 Redis hash。
- 撤销立即阻止新请求和新副作用，但不改写历史 Task owner；正在执行任务按风险策略继续、取消或进入对账。
- 已泄漏根签名密钥属于 P0，停止新激活并切换预置紧急信任根；不能通过被泄漏密钥自签“修复”。

---

# 5. API 与 CLI

运维入口只绑定内网/受控管理网络，使用独立机器 Credential 和 capability；V1 不要求 Web UI。

| 方法 | 路径 | capability |
|---|---|---|
| `POST` | `/ops/v1/config-bundles/validations` | `ops.config.validate` |
| `POST` | `/ops/v1/config-bundles/{bundleId}/stages` | `ops.config.stage` |
| `POST` | `/ops/v1/config-bundles/{bundleId}/activations` | `ops.config.activate` |
| `POST` | `/ops/v1/config-bundles/{generation}/rollbacks` | `ops.config.rollback` |
| `POST` | `/ops/v1/config-bundles/{generation}/revocations` | `ops.config.revoke` |
| `GET` | `/ops/v1/config-bundles` | `ops.config.read` |
| `GET` | `/ops/v1/config-status` | `ops.config.read` |

mutating 请求必须携带 `Idempotency-Key`、期望 `activeGeneration` 和变更原因码。重复相同请求返回原结果；同 key 不同 body 返回 409。CLI 仅是 API 客户端：

```text
a2amesh ops config validate <bundle>
a2amesh ops config stage <bundle-id>
a2amesh ops config activate <bundle-id> --expect-generation <n>
a2amesh ops config rollback <generation> --reason <code>
a2amesh ops config revoke <generation> --reason <code>
a2amesh ops config status
```

CLI 不直接写 Redis、配置目录或组件本地缓存。

---

# 6. Card publisher ownership

bundle 为每个 `agentId` 声明一个或多个允许 publisher instance 候选。active publisher 由 Redis lease/fencing 选择：

```text
candidate READY for active generation
→ acquire card-publisher:<agentId> lease
→ obtain monotonically increasing fencing token
→ build/validate Card from the same config generation
→ upsert_card(agentId, instanceId, cardGeneration, configGeneration, fencingToken)
```

规则：

1. State 只接受 active generation、允许候选、有效 lease 和最新 fencing token。
2. lease 默认 15 秒，5 秒续约；失联后必须等 lease 过期，新 publisher 才能取得更高 fencing token。
3. 旧 publisher 恢复后只能更新 presence，不能覆盖 Card。
4. Card generation 与 config generation 分开单调递增，但每次 Card 记录必须保存来源 config generation/content hash。
5. publisher failover 不自动扩大 delivery profile；新实例只能发布 active generation 已允许且门禁已通过的能力。
6. 所有候选不可用时保留最后一个已验证 Card 快照并标记发布能力 degraded；不能由任意 Peer 接管。

---

# 7. 启动、缓存和失效行为

- 组件启动必须从受保护本地缓存读取最后一个 active bundle，重新验证签名、meshId、hash、expiry 和 active pointer。
- 能访问 Controller/State 时，必须确认本地 generation 未被撤销；缓存与 active pointer 不一致时不接受新业务。
- Controller 暂时不可用但最后 active bundle 未过期、未撤销且 State 可确认 pointer 时，可继续既有 generation；记录 degraded 告警。
- bundle 过期、签名无效、meshId 不匹配、secretRef 缺失或 generation 未知时 fail closed：Gateway/Core 停止新任务和新副作用，只保留最小健康、只读查询和运维修复入口。
- 不得回退读取未签名 `.env`/YAML 来恢复业务；bootstrap 连接地址和根公钥不属于业务 bundle，但也必须受主机权限保护。
- READY 有 TTL；实例失联后 Controller 不把旧 READY 当作下一次激活证据。

---

# 8. 审计与保留

append-only 审计至少包含：`auditId/action/bundleId/generation/contentSha256/previousGeneration/operatorPrincipal/idempotencyKey/result/reasonCode/requestId/traceId/timestamp`。签名原文、secret 值和 Credential 不进入日志。bundle 制品、签名、validate 报告、READY/NACK 摘要、激活/回滚/撤销记录至少保留 365 天并进入加密异机备份。

任何 active pointer 修复都必须通过受控恢复命令生成审计，禁止 Redis CLI 手改。灾难恢复后先验证信任根和全部 bundle hash，再恢复 active pointer，最后允许组件 READY。

---

# 9. 失败矩阵

| 故障 | 行为 | 禁止行为 |
|---|---|---|
| 签名/hash/schema 无效 | `REJECTED`，不 stage | 忽略未知必选字段继续运行 |
| CAS generation 冲突 | 409，重新基于当前 active 生成 bundle | 覆盖 active pointer |
| 组件 NACK/READY 超时 | 保持 STAGED 或 REJECTED，不激活 | 部分组件先用新 grant、部分仍用旧 Credential |
| Controller 故障 | 未过期 active generation 可继续；停止新激活 | 组件自行挑最新文件 |
| State 不可写 | 停止激活和 publisher lease 变更 | 仅写本地配置宣布成功 |
| bundle 过期 | 停止新任务/副作用，保留最小只读和运维 | 永久使用过期配置 |
| secretRef 不可解析 | 相关组件 NACK；已激活实例按失效策略降级 | 把 secret 写入 bundle |
| publisher split brain | fencing 拒绝旧 token，P1 告警 | 以实例启动时间决定谁覆盖 Card |
| 回滚目标已不兼容 | rollback validation 失败 | 降低 generation 强制指回 |
| 根密钥疑似泄漏 | P0，冻结激活并启用预置紧急根 | 使用同一泄漏密钥签撤销 |

---

# 10. 可观测与告警

配置域必须提供 bundle 状态/激活耗时、active generation、签名/expiry、组件 READY/NACK、generation mismatch、撤销传播和 publisher lease/fencing 信号。精确指标名、标签和阈值以《统计、审计与运行监控规则》为准：签名/expiry/generation/READY 故障映射 `OBS-ALERT-026`，publisher split brain/旧 fencing/无候选映射 `OBS-ALERT-027`。Principal、bundleId 和 content hash 不作为 metric label。

---

# 11. 验收标准

- **TEST-CONFIG-SIGN-001**：canonical hash、允许算法、签名链、meshId、时间窗和 schema 失败均拒绝。
- **TEST-CONFIG-CAS-001**：并发 stage/activate、同 generation 异内容、previousGeneration 漂移均返回确定冲突。
- **TEST-CONFIG-ATOMIC-001**：Credential/Alias/Grant/Profile/Policy 不出现跨 generation 混用。
- **TEST-CONFIG-READY-001**：必选组件 READY/NACK/TTL 和 delivery profile 广告门禁正确。
- **TEST-CONFIG-ROLLBACK-001**：rollback 使用更高 generation，旧内容重新校验且全程可审计。
- **TEST-CONFIG-REVOKE-001**：Credential/grant 撤销阻止新请求，不改写历史 Task owner，传播时限可测。
- **TEST-CARD-OWNER-001**：多候选、lease 过期、网络分区和旧实例恢复时只有最新 fencing publisher 可更新 Card。
- **TEST-CONFIG-STARTUP-001**：Controller outage、过期、撤销、坏缓存和 secretRef 缺失均按规则 fail closed。
- **TEST-CONFIG-DR-001**：信任根、bundle、active pointer 和审计可从异机备份一致恢复。

---

# 12. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.5](A2AMesh_业务与总体架构设计_V1.5.md)
- [AgentCard与协议对象规范 V1.5](A2AMesh_AgentCard与协议对象规范_V1.5.md)
- [Redis状态平面与数据设计 V1.5](A2AMesh_Redis状态平面与数据设计_V1.5.md)
- [编排器 Runtime与工具适配设计 V1.5](A2AMesh_编排器_Runtime与工具适配设计_V1.5.md)
- [Artifact与对象存储设计 V1.1](A2AMesh_Artifact与对象存储设计_V1.1.md)
- [统计审计与运行监控规则 V1.5](A2AMesh_统计审计与运行监控规则_V1.5.md)
- [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)
- [RFC 7515 JSON Web Signature](https://www.rfc-editor.org/rfc/rfc7515)
