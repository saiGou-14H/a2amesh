# A2AMesh Artifact 与对象存储设计 V1.1
> 文档ID：`A2AM-ARTIFACT-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：Artifact blob、上传会话、对象键、完整性、访问票据、保留、删除、备份与恢复
> 目标读者：架构、Gateway、后端、Runtime、存储、安全、测试、运维
> 评审状态：文档自检通过；对象存储实现、故障演练与恢复验收待完成
> 最后更新：2026-08-14
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 首次版本：V1.0
> 替代版本：V1.0
> 维护方式：版本化不可变文档；后续修订递增版本

---

# 1. 文档目的

本文档定义 A2AMesh 大型 Artifact 的 blob 生命周期和访问合同。A2A `Artifact`/`Part` 的协议字段仍以《Agent Card 与协议对象规范》为准；本文只负责对象存储、上传会话、完整性校验、下载授权、保留删除和恢复，不重新定义官方对象。

当前代码尚未实现本合同。本文中的状态、接口、指标和门禁均为目标设计，不表示对象存储已部署或数据已通过恢复验证。

## 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 Artifact blob 权威边界、上传/完成/下载/删除流程、故障语义和恢复门禁 |
| V1.1 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，Artifact 领域合同不变 |

---

# 2. 权威边界

| 数据 | 权威来源 | 其他系统职责 |
|---|---|---|
| blob 字节 | Object Store | Redis、JetStream、日志不得保存副本 |
| Artifact 稳定元数据 | Redis State Service | Task 快照只引用 `artifactId/uri/hash/size/mediaType/status` |
| 上传会话与完成幂等 | Redis State Service | Object Store 临时对象只作为待验证输入 |
| 短期上传/下载 URL | 签名服务即时生成 | 永不持久化到 Redis、Task、日志或审计正文 |
| Task 关联和事件顺序 | Redis 原子 mutation + outbox | Relay 只发布已提交事件 |
| 访问授权 | Canonical Principal + capability grant + Task ownership | 对象存储策略不接受客户端自报身份 |
| 保留/删除策略 | 当前 active trusted config generation | Object lifecycle 只能执行已激活策略 |

V1 不建设通用文件盘、跨 Mesh 共享、用户目录或任意 URL 代理。客户端提交的外部 URL 不能直接成为可信 Artifact；需要导入时必须由受控 fetcher 按同一大小、协议、域名、内容和恶意文件校验流程落入 Mesh 自有对象存储。

---

# 3. 核心决策

| ADR | 决策 | 原因 |
|---|---|---|
| ADR-ART-001 | Object Store 是 blob 唯一权威，Redis 只存元数据 | 避免大对象挤占状态平面并保留可验证引用 |
| ADR-ART-002 | signed URL 只作为短期传输能力，不是持久 URI | URL 泄漏和过期不应改变 Artifact 身份 |
| ADR-ART-003 | 完成操作必须 HEAD/校验 size、SHA-256、media type | 上传成功响应和 ETag 不能证明内容完整性 |
| ADR-ART-004 | Artifact 元数据、Task 关联和 outbox 原子提交 | 防止 Task 已引用但 blob 未验证，或事件领先快照 |
| ADR-ART-005 | 删除分为逻辑删除和异步物理删除 | 支持并发读、审计、重试和恢复 |
| ADR-ART-006 | 未启用对象存储时只允许受控 inline Artifact | 不以本地临时目录或 Redis 作为隐式降级存储 |

---

# 4. 数据模型

## 4.1 ArtifactRecord

| 字段 | 规则 |
|---|---|
| `artifactId` | 服务端生成的不透明 ID，在 Mesh 内稳定 |
| `taskId` | 所属 Task，不允许跨 Task 重新挂载 |
| `ownerPrincipal` | 从 Task 固化继承，不接受请求体覆盖 |
| `objectKey` | 服务端生成，不含原始文件名、Principal、workspace 路径或用户文本 |
| `contentSha256` | 小写十六进制 SHA-256，完成前为期望值，完成后为已验证值 |
| `sizeBytes` | 非负整数，必须等于对象存储 HEAD 结果 |
| `mediaType` | 规范化 MIME；缺失或不可信时使用 `application/octet-stream` |
| `originalName` | 可选展示字段，清理路径字符、控制字符并限制长度；不参与 object key |
| `status` | `PENDING_UPLOAD/AVAILABLE/QUARANTINED/DELETING/DELETED/FAILED` |
| `configGeneration` | 创建时使用的 Artifact policy generation |
| `createdAt/availableAt/deletedAt` | 服务端 UTC 时间 |
| `version` | State CAS 版本，所有状态变化单调递增 |

## 4.2 状态机

```text
PENDING_UPLOAD ── verified finalize ──▶ AVAILABLE
      │                    │
      │ expiry/fatal       └─ policy/scan failure ─▶ QUARANTINED
      ▼
    FAILED

AVAILABLE / QUARANTINED ── delete request ──▶ DELETING ── object absent ──▶ DELETED
                                      └──── retryable storage failure ────┘
```

规则：

1. 只有 `AVAILABLE` 可生成下载票据或附加到对外 Task Artifact。
2. `QUARANTINED` 不可下载，必须保留原因码和脱敏扫描证据摘要。
3. `FAILED` 不自动复用同一 object key；重试创建新 upload session，可复用稳定 `artifactId` 仅限服务端明确的恢复操作。
4. `DELETED` 是终态，不允许原地转回 `AVAILABLE`。业务需要重新提供内容时必须创建新 `artifactId` 并引用恢复审计；整批灾难恢复若回到删除前恢复点，必须重放删除 tombstone，不能让已删除对象复活。
5. 所有状态变化都通过 State Service CAS，并写对应 outbox；Projector 不得反向覆盖。

## 4.3 Object key

正式对象键固定由服务端生成：

```text
mesh/<meshId>/artifacts/<yyyy>/<mm>/<artifactId>/<contentSha256>
```

临时上传键：

```text
mesh/<meshId>/uploads/<yyyy>/<mm>/<uploadId>
```

`meshId` 来自 active trusted config；`artifactId/uploadId` 必须通过字符集和长度校验。原始文件名、Task 文本、Principal、绝对路径和客户端提供的 key 均不得进入对象键。正式 key 不覆盖已有对象；hash 相同也不进行跨 Principal/Task 的隐式去重。

---

# 5. 接口合同

所有业务接口使用现有 A2A Bearer Credential 解析 Canonical Principal，并执行 Task ownership 与 Artifact capability；运维修复使用独立机器 Credential 和 `ops.artifact.*` capability。mutating 请求必须携带稳定 `Idempotency-Key`。

| 方法 | 路径 | 作用 | 成功结果 |
|---|---|---|---|
| `POST` | `/api/a2amesh/v1/artifact-uploads` | 创建上传会话和短期上传 URL | `201`，返回 `uploadId/artifactId/uploadUrl/expiresAt` |
| `PUT` | `<signed upload URL>` | 客户端直传临时对象 | Object Store 响应，不经过 Gateway body 转发 |
| `PUT` | `/api/a2amesh/v1/artifact-uploads/{uploadId}/completion` | 验证并完成上传 | `200`，返回稳定 Artifact 元数据 |
| `POST` | `/api/a2amesh/v1/artifacts/{artifactId}/download-tickets` | 创建短期下载 URL | `201`，返回 `downloadUrl/expiresAt` |
| `DELETE` | `/api/a2amesh/v1/artifacts/{artifactId}` | 请求逻辑和物理删除 | `202`，返回 `DELETING` |
| `GET` | `/api/a2amesh/v1/artifacts/{artifactId}` | 获取稳定元数据，不返回 signed URL | `200` |

## 5.1 创建上传会话

请求至少包含 `taskId/sizeBytes/contentSha256/mediaType`，可带清理后的 `originalName`。State Service 原子校验：

- Task 存在且 caller 有写入该 Task Artifact 的能力；
- Task 尚允许 Artifact 更新；终态 Task 只允许本文明确的对账追加路径；
- `sizeBytes` 不超过 active Artifact policy 和当前 Principal/Task 配额；
- SHA-256 格式、MIME 白名单、文件数量和累计大小合法；
- Object Store/签名服务健康，且当前 config generation 已激活。

上传 URL 默认 10 分钟，绑定 `PUT`、临时 object key、content length、content type 和 checksum；不得授权列目录、覆盖其他 key 或改变 ACL。

## 5.2 完成上传

completion 不接受客户端自报“已成功”作为事实。服务端必须：

1. 读取上传会话并校验 owner、expiry、idempotency key 和 session version。
2. 对临时对象执行 HEAD，核对 size、服务端 SHA-256 元数据、media type 和加密状态；ETag 不作为完整性 hash。
3. 执行配置要求的恶意文件/内容策略；异步扫描期间保持 `PENDING_UPLOAD`，不能提前附加 Task。
4. 以 copy/move-if-absent 写入正式 key，再次 HEAD 验证；失败时不提交 Task 引用。
5. 调用 State `finalize_artifact`，原子写 `AVAILABLE` 元数据、Task Artifact 引用、Task version/eventSequence 和 outbox。
6. Relay 发布标准 `TaskArtifactUpdateEvent`；重复 completion 返回同一 Artifact，不重复事件。

如果对象已存在但 Redis 未提交，恢复任务必须按 uploadId/objectKey/hash 对账后继续 finalize；不能仅因 key 存在就认定上传属于当前 caller。

## 5.3 下载票据

下载前重新校验：Artifact=`AVAILABLE`、Task ownership/capability、Credential 状态、对象存在和策略允许。下载 URL 默认 5 分钟，只授权 `GET/HEAD` 单对象；响应使用安全 `Content-Disposition`。URL、签名 query 和访问密钥不进入 Task、日志、Trace、事件或持久审计。

## 5.4 删除

删除请求先通过 State CAS 把状态改为 `DELETING` 并写 outbox，再由 Artifact Reaper 删除对象。对象确认不存在后写 `DELETED`。重复删除返回当前状态；存储超时保持 `DELETING` 并重试，不把“请求已接受”写成“已删除”。依法/策略保留、对账证据引用或活跃 Task 依赖存在时返回冲突，不绕过保留锁。

---

# 6. Inline 与降级规则

- 单个 inline Part 默认上限 1 MiB，单 Task inline 总量和 Context 累计量由 active Artifact policy 定义。
- Object Store 未启用时，Card/配置不得宣传大 Artifact；超过 inline 上限返回 `413 Payload Too Large`、A2A InvalidArgument 或对应 gRPC `RESOURCE_EXHAUSTED`，不得写临时本地文件作为长期结果。
- Object Store 已启用但不可用时，新上传会话和下载票据返回可重试 `503/UNAVAILABLE`；已有 Task 查询仍返回稳定元数据，但不能伪造可下载 URL。
- Runtime 产生必须持久化的大结果但 finalize 未成功时，Task 不得提交成功终态。可在 deadline 内重试存储；超时后 Task 失败并保留可诊断原因，不把未验证本地路径作为 Artifact。
- 删除服务不可用只造成 `DELETING` backlog 和告警，不恢复对外下载权限。

---

# 7. 安全与隐私

1. bucket/container 默认私有，禁止公共读和匿名列目录。
2. 服务端加密为必选；跨主机备份使用独立加密密钥和最小权限 Credential。
3. 签名服务只接收稳定 object key 和授权上下文，不能接收任意 bucket/key。
4. 所有 URI 只允许配置的 Object Store scheme/host；阻止 `file://`、环回、链路本地、云 metadata 和重定向 SSRF。
5. 原始文件名只用于安全展示；响应必须设置 `nosniff`，可执行内容默认 attachment 下载。
6. 日志只记录 `artifactId/taskId/objectKeyHash/size/result/requestId`，不记录 signed URL、文件内容和 Credential。
7. `QUARANTINED` 对象只允许隔离扫描服务和具备 `ops.artifact.quarantine.read` 的独立运维 Credential 访问。

---

# 8. 清理、保留与恢复

## 8.1 默认策略

| 对象 | 默认策略 |
|---|---|
| 未完成临时上传 | 24 小时后 Reaper 清理 |
| `FAILED` 临时对象 | 24 小时内清理，保留脱敏失败审计 |
| `QUARANTINED` | 默认 7 天后按策略删除，安全事件可延长 |
| `AVAILABLE` | 至少与 Task Artifact 元数据同周期；默认 30 天，受保留锁约束 |
| `DELETED` tombstone | Redis 保留至少 30 天，防止旧事件/URL复活 |
| 下载票据 | 仅内存/短 TTL 状态，过期即失效，不备份 |

所有数值由签名 trusted config 明确给出；变更只影响新对象，缩短既有对象保留期必须经过独立迁移/审计，不能静默批量删除。

## 8.2 Orphan 对账

每日 inventory 比较：Object Store 正式对象、临时对象、Redis ArtifactRecord 和 Task 引用。处理规则：

- 有临时对象无有效 upload session：超过宽限期删除并审计；
- 有正式对象无 ArtifactRecord：隔离，不自动附加任意 Task；
- 有 `AVAILABLE` 记录但对象缺失/hash 不符：标记 `QUARANTINED` 或 `FAILED`、阻止下载、P1 告警；
- Task 引用不存在/非 AVAILABLE Artifact：一致性错误，不能由 Projector 猜测修复。

## 8.3 备份与恢复

Object Store 备份、版本清单和 Redis 恢复点必须属于同一恢复批次，至少每 15 分钟形成异机加密恢复点。恢复顺序：恢复配置/密钥引用、恢复 Object Store、恢复 Redis、运行全量 inventory/hash 抽样、再开放下载和新上传。恢复门禁同时满足总体 RTO/RPO；只恢复 Redis 或只恢复 bucket 均不能宣称业务恢复完成。

---

# 9. 可观测与告警

Artifact 域必须提供状态/字节、upload/finalize/download/delete 结果、Object Store 延迟、完整性失败、orphan、Reaper backlog 和恢复点信号。精确指标名、低基数标签和告警 ID 以《统计、审计与运行监控规则》为准：`AVAILABLE` 对象缺失/hash 不符、持续不可用、Reaper 超限或恢复失败映射 `OBS-ALERT-028`；orphan/quarantine/下载拒绝异常映射 `OBS-ALERT-030`。任何观测数据都不得包含 artifactId、object key、文件名或 signed URL 作为 metric label。

---

# 10. 失败矩阵

| 故障 | 行为 | 禁止行为 |
|---|---|---|
| signed URL 生成失败 | 不创建或回滚 upload session，返回 unavailable | 返回无签名内部地址 |
| 上传中断/过期 | 保持待清理，客户端创建新 session | 延长已泄漏 URL 或复用其他 caller session |
| HEAD/hash/size 不一致 | `QUARANTINED/FAILED`，不附加 Task | 仅信客户端 checksum 或 ETag |
| finalize 后 Relay 故障 | Redis/Task 已权威，outbox 重投 | Runtime 直接发布替代事件 |
| Redis 提交失败 | 不宣布 AVAILABLE；保留对象待恢复对账 | 只在 Task JSON 中写 URI |
| Object Store 读故障 | 元数据仍可查询，下载返回可重试错误 | 把历史 signed URL 当稳定地址 |
| 删除 API 超时 | 状态保持 `DELETING`，异步重试 | 立即写 `DELETED` |
| 配置 generation 漂移 | 新操作 fail closed，已签 URL 仅活到原 TTL | 混用不同 bucket/policy generation |

---

# 11. 验收标准

- **TEST-ARTIFACT-001**：create/upload/completion/download/delete 全流程幂等，跨 Principal/Task 访问被拒绝。
- **TEST-ARTIFACT-INTEGRITY-001**：size、SHA-256、media type 任一不符均不能形成 Task Artifact。
- **TEST-ARTIFACT-ATOMIC-001**：finalize 任意故障点不产生“Task 已引用但 blob 不可用”或重复 Artifact event。
- **TEST-ARTIFACT-URL-001**：signed URL 短 TTL、method/key 绑定且不进入 Redis、日志、Trace 和 Task。
- **TEST-ARTIFACT-ORPHAN-001**：临时/正式孤儿、缺失对象和 hash 损坏均按规则隔离或清理。
- **TEST-ARTIFACT-FAILURE-001**：Object Store 禁用、不可用、慢响应和恢复时的 inline/503/终态语义正确。
- **TEST-ARTIFACT-DR-001**：Redis + Object Store 一致恢复，hash 抽样、RTO/RPO 和保留锁门禁通过。
- **TEST-ARTIFACT-SEC-001**：无公共 bucket、目录遍历、SSRF、跨对象覆盖、signed URL/secret 泄漏。

---

# 12. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.5](A2AMesh_业务与总体架构设计_V1.5.md)
- [AgentCard与协议对象规范 V1.5](A2AMesh_AgentCard与协议对象规范_V1.5.md)
- [Redis状态平面与数据设计 V1.5](A2AMesh_Redis状态平面与数据设计_V1.5.md)
- [任务生命周期与长任务运行时设计 V1.5](A2AMesh_任务生命周期与长任务运行时设计_V1.5.md)
- [受信配置与变更治理设计 V1.1](A2AMesh_受信配置与变更治理设计_V1.1.md)
- [统计审计与运行监控规则 V1.5](A2AMesh_统计审计与运行监控规则_V1.5.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
