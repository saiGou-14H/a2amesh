# A2AMesh Artifact 与对象存储设计 V1.2
> 文档ID：`A2AM-ARTIFACT-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：Artifact blob、上传会话、对象键、完整性、访问票据、保留、删除、备份与恢复
> 目标读者：架构、Gateway、后端、Runtime、存储、安全、测试、运维
> 评审状态：G0 候选自检完成；首轮独立复审问题已纳入修复，关闭复审待完成；代码、故障注入与交付剖面验收未完成
> 最后更新：2026-08-14
> 替代版本：V1.1；旧版位于 `docs/archive/v1.5/`，不得作为当前实现合同
> 适用产品版本：A2AMesh V1
> 协议基线：A2A v1.0.1（协商值 `1.0`）
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 首次版本：V1.0
> 维护方式：版本化不可变文档；后续修订递增版本

---

## 1. 文档目的

本文档定义 A2AMesh 大型 Artifact 的 blob 生命周期和访问合同。A2A `Artifact`/`Part` 的协议字段仍以《Agent Card 与协议对象规范》为准；本文只负责对象存储、上传会话、完整性校验、下载授权、保留删除和恢复，不重新定义官方对象。

当前代码尚未实现本合同。本文中的状态、接口、指标和门禁均为目标设计，不表示对象存储已部署或数据已通过恢复验证。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 Artifact blob 权威边界、上传/完成/下载/删除流程、故障语义和恢复门禁 |
| V1.1 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，Artifact 领域合同不变 |
| V1.2 | 2026-08-14 | 闭合 G0：稳定 URI、finalize/delete/download 竞态、授权保留和共同恢复点 |

---

## 2. 权威边界

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

## 3. 核心决策

| ADR | 决策 | 原因 |
|---|---|---|
| ADR-ART-001 | Object Store 是 blob 唯一权威，Redis 只存元数据 | 避免大对象挤占状态平面并保留可验证引用 |
| ADR-ART-002 | signed URL 只作为短期传输能力，不是持久 URI | URL 泄漏和过期不应改变 Artifact 身份 |
| ADR-ART-003 | 完成操作必须 HEAD/校验 size、SHA-256、media type | 上传成功响应和 ETag 不能证明内容完整性 |
| ADR-ART-004 | Artifact 元数据、Task 关联和 outbox 原子提交 | 防止 Task 已引用但 blob 未验证，或事件领先快照 |
| ADR-ART-005 | 删除分为逻辑删除和异步物理删除 | 支持并发读、审计、重试和恢复 |
| ADR-ART-006 | 未启用对象存储时只允许受控 inline Artifact | 不以本地临时目录或 Redis 作为隐式降级存储 |

---

## 4. 数据模型

### 4.1 ArtifactRecord

| 字段 | 规则 |
|---|---|
| `artifactId` | 服务端生成的不透明 ID，在 Mesh 内稳定 |
| `taskId` | 所属 Task，不允许跨 Task 重新挂载 |
| `ownerPrincipal` | 从 Task 固化继承，不接受请求体覆盖 |
| `stableUri` | 固定 `a2amesh://artifacts/<artifactId>`，不是 signed URL |
| `taskOwnerSnapshotRef` | Task 热状态清理后仍可校验 owner/capability 的最小墓碑或归档引用 |
| `objectKey` | 服务端生成，不含原始文件名、Principal、workspace 路径或用户文本 |
| `contentSha256` | 小写十六进制 SHA-256，完成前为期望值，完成后为已验证值 |
| `sizeBytes` | 非负整数，必须等于对象存储 HEAD 结果 |
| `mediaType` | 规范化 MIME；缺失或不可信时使用 `application/octet-stream` |
| `originalName` | 可选展示字段，清理路径字符、控制字符并限制长度；不参与 object key |
| `status` | `PENDING_UPLOAD/AVAILABLE/QUARANTINED/DELETING/DELETED/FAILED` |
| `configGeneration` | 创建时使用的 Artifact policy generation |
| `createdAt/availableAt/deletedAt` | 服务端 UTC 时间 |
| `version` | State CAS 版本，所有状态变化单调递增 |

#### 4.1.1 `ArtifactAccessTombstone` 与 `ArtifactHold`

Task 热快照清理时，State 原子创建最小 `ArtifactAccessTombstone(taskId,ownerPrincipalHash,ownerAliasGeneration,grantDigest,createdAt,expiresAt,status)`，并与 Artifact 同周期保留。它只证明历史 owner/归属，**不能单独授权下载**。每次访问仍要求：当前 Credential 有效且解析到同一 Canonical Principal、当前 capability/policy 允许、Artifact AVAILABLE 且无即时撤销。

`ArtifactHold(holdId,artifactId,reason,sourceCaseId/sourceTaskId,createdBy,createdAt,expiresAt,status,digest)` 为 append-only 受审计保留锁。活跃 reconciliation evidence、法律/安全 hold、活跃 Task/Artifact 引用通过反向索引进入 retention lock；创建/续期/释放均使用 capability、Idempotency-Key 和 expected Artifact version。Reaper 只能在所有 hold/ref 为零且 `minimumDeleteAt` 已过时删除。

### 4.2 状态机

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

### 4.3 Object key

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

## 5. 接口合同

所有业务接口使用现有 A2A Bearer Credential 解析 Canonical Principal，并执行 Task ownership 与 Artifact capability；运维修复使用独立机器 Credential 和 `ops.artifact.*` capability。mutating 请求必须携带稳定 `Idempotency-Key`。

| 方法 | 路径 | 作用 | 成功结果 |
|---|---|---|---|
| `POST` | `/api/a2amesh/v1/artifact-uploads` | 创建上传会话和短期上传 URL | `201`，返回 `uploadId/artifactId/uploadUrl/expiresAt` |
| `PUT` | `<signed upload URL>` | 客户端直传临时对象 | Object Store 响应，不经过 Gateway body 转发 |
| `PUT` | `/api/a2amesh/v1/artifact-uploads/{uploadId}/completion` | 验证并完成上传 | `200`，返回稳定 Artifact 元数据 |
| `POST` | `/api/a2amesh/v1/artifacts/{artifactId}/download-tickets` | 创建短期下载 URL | `201`，返回 `downloadUrl/expiresAt` |
| `DELETE` | `/api/a2amesh/v1/artifacts/{artifactId}` | 请求逻辑和物理删除 | `202`，返回 `DELETING` |
| `GET` | `/api/a2amesh/v1/artifacts/{artifactId}` | 获取稳定元数据，不返回 signed URL | `200` |
| `POST` | `/api/a2amesh/v1/artifacts/{artifactId}/holds` | 创建保留锁 | `201`，返回 immutable holdId/digest |
| `PUT` | `/api/a2amesh/v1/artifacts/{artifactId}/holds/{holdId}` | 只延长 active hold | `200`，返回新 expiry/digest |
| `DELETE` | `/api/a2amesh/v1/artifacts/{artifactId}/holds/{holdId}` | 释放 hold，保留历史记录 | `200`，返回 RELEASED hold |
| `POST` | `/api/a2amesh/v1/sources/{sourceType}/{sourceId}/versions/{sourceVersion}/artifact-ref-commits` | 受信source owner提交canonical source版本及其完整Artifact ref集合；一次可原子修改多个Artifact | `200`，返回typed source commit exact result |

### 5.1 创建上传会话

请求至少包含 `taskId/sizeBytes/contentSha256/mediaType`，可带清理后的 `originalName`。State Service 原子校验：

- Task 存在且 caller 有写入该 Task Artifact 的能力；
- Task 尚允许 Artifact 更新；终态 Task 只允许本文明确的对账追加路径；
- `sizeBytes` 不超过 active Artifact policy 和当前 Principal/Task 配额；
- SHA-256 格式、MIME 白名单、文件数量和累计大小合法；
- Object Store/签名服务健康，且当前 config generation 已激活。

上传 URL 默认 10 分钟，绑定 `PUT`、临时 object key、content length、content type 和 checksum；不得授权列目录、覆盖其他 key 或改变 ACL。

### 5.2 完成上传

completion 不接受客户端自报“已成功”作为事实。服务端必须：

1. 读取上传会话并校验 owner、expiry、idempotency key 和 session version。
2. 对临时对象执行 HEAD，核对 size、对象存储服务端计算/验证的 SHA-256、media type 和加密状态；ETag 和客户端可写 metadata 不作为完整性事实。
3. 执行配置要求的恶意文件/内容策略；异步扫描期间保持 `PENDING_UPLOAD`，不能提前附加 Task。
4. 以 copy/move-if-absent 写入正式 key，再次 HEAD 验证；Object Store provider 必须通过 read-after-write、version/delete-marker fixture；失败时不提交 Task 引用。
5. 调用 State `finalize_artifact`，再次校验 expected Task/Artifact version、Task 状态、cancelRequested、owner/fencing 和 policy generation，再原子写 `AVAILABLE` 元数据、Task Artifact 引用、Task version/eventSequence 和 outbox。
6. Relay 发布标准 `TaskArtifactUpdateEvent`；重复 completion 返回同一 Artifact，不重复事件。

如果对象已存在但 Redis 未提交，恢复任务必须按 uploadId/objectKey/hash 对账后继续 finalize；不能仅因 key 存在就认定上传属于当前 caller。

### 5.3 下载票据

下载前通过 expected Artifact version 的 State 操作重新校验：Artifact=`AVAILABLE`、当前 Credential 有效、当前 Principal 与 Task/`ArtifactAccessTombstone` 历史 owner 相同、当前 capability/policy 允许、对象存在且无即时撤销。`taskOwnerSnapshotRef` 只恢复归属证据，不能替代当前 Credential/grant。若状态已进入 DELETING/DELETED 则拒绝签发。下载 URL 默认 5 分钟，只授权 `GET/HEAD` 单对象；响应使用安全 `Content-Disposition`。URL、签名 query 和访问密钥不进入 Task、日志、Trace、事件或持久审计。删除受理前已签 URL 最多继续有效一个 URL TTL；需要即时撤销的策略必须使用代理下载而非直签 URL。

### 5.4 删除

删除请求先通过 State CAS 原子读取 Task/case/evidence 反向索引、全部 active ArtifactHold、minimumDeleteAt 和 policy；任一锁存在返回 409/423且不改变状态。无锁才把状态改为 `DELETING` 并写 outbox，再由 Artifact Reaper 删除对象。Reaper 删除前再次校验 Artifact version、hold/ref、policy generation 和无迟到 finalize，按 provider policy 处理历史 version/delete marker；确认目标版本不可读后写 `DELETED`。重复删除返回当前状态；存储超时保持 `DELETING` 并重试，不把“请求已接受”写成“已删除”。finalize 与 delete 以 Artifact version CAS 线性化，迟到 copy 只能成为受控 orphan，不能恢复 AVAILABLE。

### 5.5 Hold 与 typed reference

hold API只允许独立机器Credential的`ops.artifact.hold`，或signed config明列的Reconciliation/Security service Principal；业务Task Credential不能自授legal hold。source-centric commit API只允许对应TASK/CASE/EVIDENCE source owner service或`ops.artifact.ref`，sourceType closed enum固定`TASK|CASE|EVIDENCE`。sourceVersion是1..2^53-1的JSON整数，path只接受无加号、无前导零的canonical十进制；expectedSourceVersion首版显式null，后续必须恰为sourceVersion-1。path三元组是授权和版本事实，body不得重复或覆盖sourceType/sourceId/sourceVersion；每次必须提交canonical正文、完整新ref集合和expected source version。不得先让新source可见再补索引，也不存在按目标Artifact独立删除ref的HTTP入口。

typed ref item固定五字段`artifactId,refType,refId,sourceVersion,digest`，唯一键为`artifactId+refType+refId`。同一commit中每项`refType/refId`必须逐字节等于path的sourceType/sourceId，`sourceVersion`必须数值等于canonical path解析值，digest必须等于该目标`ArtifactRecord.contentSha256`；按`(refType,refId,artifactId)`各字段UTF-8严格升序且不得重复。`refs[]`是该source version的完整新集合，空数组表示以新source version移除全部旧引用。State同时维护`artifact:<artifactId>:refs[<refType>:<refId>]`和`artifact:ref-source:<refType>:<refId>`，不得使用task-centric混合member猜测依赖。

所有能使TASK/CASE/EVIDENCE source变为VISIBLE的writer必须调用同一`commit_typed_source_and_refs`原子Function，并提交source canonical bytes/digest、完整五字段refs、Idempotency-Key/requestDigest、expected source/Artifact versions；函数在同一CAS更新source正文/visibility、Artifact version、retention lock、双向索引、AuditEnvelopeV1和outbox。source在该CAS前不可见；source writer不能先append后补ref。若source已VISIBLE但缺ref，不允许按Artifact路径修补，必须提交新sourceVersion或进入受审repair。hold记录append-only，release/expiry只改状态；renew只能延长。Artifact已DELETING/DELETED时禁止新增hold/ref。

Reaper/delete与hold/ref mutation使用同一Artifact version CAS：锁先提交则delete返回409/423；DELETING先提交则后续source-commit失败且source保持不可见/原版本不变，调用方必须重试到新Artifact/sourceVersion；Redis Function任意crash点都不能形成source已VISIBLE但retention index缺失的半状态。外部Object Store在CAS前已验证的正式blob若随后State提交失败只能成为受控orphan，由inventory/Reaper隔离，不得反向制造source或ref。

HTTP body字段恰为`schemaVersion,commitId,canonicalSourceJson,sourceDigest,refs,expectedSourceVersion,expectedArtifactVersions`，`Idempotency-Key`只来自header；adapter把path三元组、header、body和认证观察值注入内部request，body出现sourceType/sourceId/sourceVersion/idempotencyKey/authProof/requestDigest均拒绝。`expectedArtifactVersions[]`每项恰为`artifactId,expectedVersion`，按artifactId UTF-8严格升序且不得重复，其artifactId集合必须恰等于当前source oldRefs与请求newRefs的并集，不能少报被移除目标或多报无关Artifact。

内部`TypedSourceCommitRequestV1`字段恰为`schemaVersion,commitId,sourceType,sourceId,sourceVersion,canonicalSourceJson,sourceDigest,refs,expectedSourceVersion,expectedArtifactVersions,idempotencyKey,requestDigest,authProof`。State要求canonicalSourceJson已经是RFC8785 UTF-8 exact bytes且`sourceDigest=lowerhex(SHA-256(exact bytes))`，source version连续；refs遵守上述五字段/path绑定并逐项匹配目标contentSha256。`requestDigest=lowerhex(SHA-256(RFC8785(request排除requestDigest,authProof)))`，因此path source tuple和header Idempotency-Key明确进入digest。commit幂等scope exact bytes为`UTF8(sourceType)+0x00+UTF8(sourceId)+0x00+UTF8(sourceVersion)+0x00+UTF8(sourceOwnerPrincipalHash)+0x00+UTF8(Idempotency-Key)`；commitId必须等于`lowerhex(SHA-256(UTF8("a2amesh-typed-source-commit-v1")+0x00+scopeBytes))`。

Function先读取当前source forward set得到oldRefs，以old/new并集为touched Artifact set并在一个CAS中：校验source owner/capability、path tuple、版本/canonical bytes及expected集合；要求所有new目标非DELETING/DELETED且content digest匹配；写新source version VISIBLE/current pointer；按差集增删forward/reverse ref和retention lock；每个touched Artifact version恰递增一次；最后写source-commit exact result/audit/outbox。DELETE/Reaper先赢则commit零写入且source不可见；commit先赢则DELETE返回409/423。

`TypedSourceCommitResultV1`字段恰为`schemaVersion,commitId,sourceType,sourceId,sourceVersion,sourceDigest,refDigests,artifactVersions,resultDigest`；`refDigests[]`每项恰为`artifactId,refType,refId,sourceVersion,digest`并与新refs同序，`artifactVersions[]`每项恰为`artifactId,version`并按artifactId排序、恰覆盖touched set。resultDigest为排除自身后RFC8785 SHA-256。同commitId/scope/requestDigest/exact bytes逐字节返回原result，异digest/字段/版本零写入；Function提交前、提交后、响应丢失均不得出现VISIBLE source+missing ref。

---

## 6. Inline 与降级规则

- 单个 inline Part 默认上限 1 MiB，单 Task inline 总量和 Context 累计量由 active Artifact policy 定义。
- Object Store 未启用时，Card/配置不得宣传大 Artifact；超过 inline 上限返回 `413 Payload Too Large`、A2A InvalidArgument 或对应 gRPC `RESOURCE_EXHAUSTED`，不得写临时本地文件作为长期结果。
- Object Store 已启用但不可用时，新上传会话和下载票据返回可重试 `503/UNAVAILABLE`；已有 Task 查询仍返回稳定元数据，但不能伪造可下载 URL。
- Runtime 产生必须持久化的大结果但 finalize 未成功时，Task 不得提交成功终态。可在 deadline 内重试存储；超时后 Task 失败并保留可诊断原因，不把未验证本地路径作为 Artifact。
- 删除服务不可用只造成 `DELETING` backlog 和告警，不恢复对外下载权限。

---

## 7. 安全与隐私

1. bucket/container 默认私有，禁止公共读和匿名列目录。
2. 服务端加密为必选；跨主机备份使用独立加密密钥和最小权限 Credential。
3. 签名服务只接收稳定 object key 和授权上下文，不能接收任意 bucket/key。
4. 所有 URI 只允许配置的 Object Store scheme/host；阻止 `file://`、环回、链路本地、云 metadata 和重定向 SSRF。
5. 原始文件名只用于安全展示；响应必须设置 `nosniff`，可执行内容默认 attachment 下载。
6. 日志只记录 `artifactId/taskId/objectKeyHash/size/result/requestId`，不记录 signed URL、文件内容和 Credential。
7. `QUARANTINED` 对象只允许隔离扫描服务和具备 `ops.artifact.quarantine.read` 的独立运维 Credential 访问。

---

## 8. 清理、保留与恢复

### 8.1 默认策略

| 对象 | 默认策略 |
|---|---|
| 未完成临时上传 | 24 小时后 Reaper 清理 |
| `FAILED` 临时对象 | 24 小时内清理，保留脱敏失败审计 |
| `QUARANTINED` | 默认 7 天后按策略删除，安全事件可延长 |
| `AVAILABLE` | 至少与 Task Artifact 元数据和 owner 授权墓碑同周期；默认 30 天，受保留锁约束 |
| `DELETED` tombstone | Redis 保留至少 30 天，防止旧事件/URL复活 |
| 下载票据 | 仅内存/短 TTL 状态，过期即失效，不备份 |

所有数值由签名 trusted config 明确给出；变更只影响新对象，缩短既有对象保留期必须经过独立迁移/审计，不能静默批量删除。

### 8.2 Orphan 对账

每日 inventory 比较：Object Store 正式对象、临时对象、Redis ArtifactRecord 和 Task 引用。处理规则：

- 有临时对象无有效 upload session：超过宽限期删除并审计；
- 有正式对象无 ArtifactRecord：隔离，不自动附加任意 Task；
- 有 `AVAILABLE` 记录但对象缺失/hash 不符：标记 `QUARANTINED` 或 `FAILED`、阻止下载、P1 告警；
- Task 引用不存在/非 AVAILABLE Artifact：一致性错误，不能由 Projector 猜测修复。

### 8.3 备份与恢复

Object Store备份、版本清单、Redis恢复点、summary DAG/archive transition和delete journal必须属于同一个`DATA-RECOVERY-001` manifest，至少每15分钟形成异机加密恢复点。Object source必须记录实际不可变`backup/snapshot URI、backupId、digest、start/end watermark、completedAt、encryptionKeyVersion`，不能只写inventory ID；Manifest的`summary.rootNodeUri/rootNodeDigest/indexRootDigest`必须递归覆盖Object/Artifact metadata及其它enabled source，archive transition必须给出连续起止watermark、archive exact bytes/digest、transition receipt，delete journal必须给出连续起止sequence/digest并覆盖上一RELEASED点。恢复顺序：验证Manifest双签和summary DAG/所有archive receipt→恢复配置/密钥引用→恢复Object Store snapshot/archive→恢复Redis→重放删除journal→运行全量inventory/hash与summary entry抽样→签Verification/Restore/双Approval/Release receipts，再开放下载和新上传。只恢复Redis、只恢复bucket或只恢复summary root摘要均不能宣称业务恢复完成。

---

## 9. 可观测与告警

Artifact 域必须提供状态/字节、upload/finalize/download/delete 结果、Object Store 延迟、完整性失败、orphan、Reaper backlog 和恢复点信号。精确指标名、低基数标签和告警 ID 以《统计、审计与运行监控规则》为准：`AVAILABLE` 对象缺失/hash 不符、持续不可用、Reaper 超限或恢复失败映射 `OBS-ALERT-028`；orphan/quarantine/下载拒绝异常映射 `OBS-ALERT-030`。任何观测数据都不得包含 artifactId、object key、文件名或 signed URL 作为 metric label。

---

## 10. 失败矩阵

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

## 11. 验收标准

- **TEST-ARTIFACT-001**：create/upload/completion/download/delete 全流程幂等，跨 Principal/Task 访问被拒绝。
- **TEST-ARTIFACT-INTEGRITY-001**：size、SHA-256、media type 任一不符均不能形成 Task Artifact。
- **TEST-ARTIFACT-ATOMIC-001**：finalize任意故障点不产生“Task已引用但blob不可用”或重复Artifact event；对象验证成功但typed source commit失败只能留下受控orphan，不能生成VISIBLE source/ref半状态。
- **TEST-ARTIFACT-URL-001**：signed URL 短 TTL、method/key 绑定且不进入 Redis、日志、Trace 和 Task。
- **TEST-ARTIFACT-ORPHAN-001**：临时/正式孤儿、缺失对象和 hash 损坏均按规则隔离或清理。
- **TEST-ARTIFACT-FAILURE-001**：Object Store 禁用、不可用、慢响应和恢复时的 inline/503/终态语义正确。
- **TEST-ARTIFACT-DR-001**：Redis + Object Store + Recovery Manifest summary DAG/archive transition一致恢复，递归node/range/count/digest校验、hash抽样、RTO/RPO和保留锁门禁通过；缺summary child、archive exact bytes、transition receipt或只有root/index摘要时保持fail closed。
- **TEST-ARTIFACT-SEC-001**：无公共 bucket、目录遍历、SSRF、跨对象覆盖、signed URL/secret 泄漏。
- **TEST-ARTIFACT-RACE-001**：finalize vs terminal/cancel/delete、download ticket vs delete 的 CAS 线性化稳定。
- **TEST-ARTIFACT-AUTH-RETENTION-001**：Task 热快照清理后仍可按最小 owner 墓碑安全授权，且不扩大可见范围。
- **TEST-ARTIFACT-HOLD-REF-001**：并发create/renew/release/expire hold及TASK/CASE/EVIDENCE source commit与Reaper/delete。正例用一个source-centric commit同时引用两个Artifact，下一sourceVersion以完整集合保留一个/移除一个，再以空refs移除全部；expectedArtifactVersions每次恰覆盖old∪new且每个touched Artifact version只+1。在canonical source/ref/content digest校验、CAS前后、State commit-before-reply、DELETE先/commit先各点杀进程。断言source visibility、五字段typed双向索引、retention lock、Artifact version、source-commit exact result和audit/outbox同CAS，任意时刻不存在VISIBLE source+missing ref；重复幂等逐字节重放且不重复计数。逐项拒绝旧四字段ref、path/ref tuple漂移、重复/乱序、missing/extra expected target、错误contentSha256、target-centric路径、一个target capability越权修改其他Artifact、旧version/digest及DELETING目标；ref移除只能由新sourceVersion完整commit。
- **TEST-OBJECT-CHECKSUM-001**：客户端伪造 metadata/ETag 不能通过服务端 checksum 门禁。
- **TEST-DR-MANIFEST-001**：Object inventory、Redis、JetStream/config/audit水位或Manifest summary DAG/archive transition不一致时不开放业务；覆盖summary node缺失/重叠range/错误indexRootDigest、archive/replay/delete journal缺口和Redis全损外部重建。

---

## 12. G0 Artifact 冻结合同

1. 稳定 URI 固定为 `a2amesh://artifacts/<artifactId>`，signed URL 只是短期解析结果。
2. Object Store 与 Redis 是 saga，不宣称跨系统事务；允许正式 blob orphan，不允许 Task 引用未验证 blob。
3. finalize、terminal、cancel、delete 通过 expected Task/Artifact version 和 fencing 线性化。
4. delete 接受后不再签发新 URL；旧 URL 最多存活一个 TTL，需即时撤销时使用代理下载。
5. Task 热快照 7 天后仍保留与 Artifact 同周期的最小 owner/status 授权墓碑。
6. checksum 必须来自服务端可信校验，Object Store 一致性/versioning 能力必须进入 provider fixture。
7. 恢复使用共同 Recovery Manifest 和长期删除日志，不以单个 bucket/Redis 备份判定成功。
8. owner tombstone 只证明历史归属；当前 Credential、Canonical Principal 与 capability 仍须全部有效。ArtifactHold/依赖索引未清零时禁止进入 DELETING。
9. TASK/CASE/EVIDENCE source正文与五字段typed forward/reverse ref、retention lock、Artifact version必须由source-centric唯一入口和同一`commit_typed_source_and_refs`线性化；path source tuple进入requestDigest，old∪new Artifact expected versions恰覆盖，source在CAS前不可见，append-only正文不得靠事后rollback修复缺ref。

---

## 13. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [AgentCard与协议对象规范 V1.6](A2AMesh_AgentCard与协议对象规范_V1.6.md)
- [Redis状态平面与数据设计 V1.6](A2AMesh_Redis状态平面与数据设计_V1.6.md)
- [任务生命周期与长任务运行时设计 V1.6](A2AMesh_任务生命周期与长任务运行时设计_V1.6.md)
- [受信配置与变更治理设计 V1.2](A2AMesh_受信配置与变更治理设计_V1.2.md)
- [统计审计与运行监控规则 V1.6](A2AMesh_统计审计与运行监控规则_V1.6.md)
- [A2A Specification v1.0.1 Release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)
