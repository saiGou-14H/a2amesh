# A2AMesh 人工对账与运维操作设计 V1.2
> 文档ID：`A2AM-RECON-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：reconciliation case、证据、claim lease、resolution、reopen、effect/Task 原子更新和运维审计
> 目标读者：架构、后端、Runtime、测试、安全、运维
> 评审状态：G0 候选自检完成；首轮独立复审问题已纳入修复，关闭复审待完成；代码、故障注入与交付剖面验收未完成
> 最后更新：2026-08-14
> 替代版本：V1.1；旧版位于 `docs/archive/v1.5/`，不得作为当前实现合同
> 适用产品版本：A2AMesh V1
> 维护者：A2AMesh 项目维护者
> 保密级别：公开项目文档
> 首次版本：V1.0
> 维护方式：版本化不可变文档；后续修订递增版本

---

## 1. 文档目的

本文档把 side-effect ledger 的 `UNKNOWN` 从状态语义闭合到可执行运维流程：自动创建 case、收集可验证证据、租约认领、裁决 `APPLIED/FAILED/COMPENSATED`、原子更新 ledger/Task/outbox，并保留 append-only 审计。

本文不建设通用工单系统、用户/RBAC 平台或强制 Web 控制台。运维 API/CLI 使用独立机器 Credential 和最小 reconciliation capability。当前实现尚未具备本合同，所有 SLA 和对账能力均待代码与 provider 真机验证。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立 case/evidence/claim/resolution/reopen 状态机、权限、原子更新、SLA 和不可变 Task 规则 |
| V1.1 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，对账领域合同不变 |
| V1.2 | 2026-08-14 | 闭合 G0：正交 workflow/escalation/claim、resolution 历史和陈旧 APPLYING 接管 |

---

## 2. 权威边界

| 事实 | 权威来源 |
|---|---|
| 外部副作用执行状态 | Redis side-effect ledger |
| 对账工作流状态 | Reconciliation Case Store（经 State Service 原子函数维护） |
| provider 实际结果 | provider 查询/幂等记录；只作为裁决证据，不直接改 Task |
| 本地执行/补偿事实 | 签名或不可变本地回执 |
| Task 标准终态 | 已提交 Redis Task 快照，不因事后对账被重写 |
| 对账结果通知 | 同一原子 mutation 产生的 outbox 事件 |
| 运维操作历史 | append-only reconciliation audit |

自由文本说明、屏幕截图、聊天消息、单次网络错误或“操作员认为已完成”不能单独作为 resolution 证据。证据可附加受控 Artifact，但必须保存稳定 digest、采集时间和来源类型。

---

## 3. Case 模型

### 3.1 ReconciliationCase

| 字段 | 规则 |
|---|---|
| `caseId` | 服务端生成，不透明且稳定 |
| `taskId/effectIntentId/effectAttemptId/attempt` | 唯一关联一个 UNKNOWN effect attempt |
| `targetAgentId/provider/operation` | 从 ledger 复制的稳定脱敏维度 |
| `risk` | effect 创建时固化的风险等级 |
| `workflowState` | `OPEN/RESOLVED/CLOSED` |
| `escalated/escalatedAt` | SLA 升级维度，不与 workflowState、priority 或 claim 互斥 |
| `currentResolutionId` | 仅 RESOLVED/CLOSED 有值；指向 immutable ResolutionRecord；reopen 时清空 |
| `resolutionHistory` | 按 case revision 排序的 append-only ResolutionRecord ID 历史，reopen/纠错不得覆盖 |
| `reconciliationRequired` | 创建时为 true；由所有关联 case 聚合计算 |
| `priority` | 默认 `P1`，策略可提高，不能由 caller 降低 |
| `claimOwnerHash/claimOwnerInstanceId/claimExpiresAt/claimFencingToken` | owner Principal只来自认证上下文；active claim时owner/expiry非null，fence保留最后tombstone值且永不回退 |
| `claimSlaCycle/claimSlaDueAt/claimedInCurrentOpenCycle` | 每次create/reopen开启新cycle并持久调度15分钟未claim升级；首次ACQUIRE后本cycle永久标记已claim |
| `evidenceIds` | 仅保存引用，证据正文 append-only |
| `revision` | 每次状态变化 CAS 递增 |
| `openedAt/escalatedAt/resolvedAt/closedAt` | 服务端 UTC 时间 |
| `configGeneration` | case 创建时的 policy generation |

同一个 `effectAttemptId` 最多一个 active case。`effectIntentId` 用于跨安全重试稳定 provider idempotency，不能替代 attempt 级 case 身份。重复 UNKNOWN 回调返回原 case，不创建重复告警或多个操作员工作单。

### 3.2 状态机

```text
UNKNOWN effect ── atomic create ──▶ OPEN ── resolve ──▶ RESOLVED ── verify/archive ──▶ CLOSED
                                  │
                                  └─ SLA breach ─▶ escalated=true / priority↑（仍为 OPEN，可独立 claim）

RESOLVED/CLOSED ── new contradictory evidence + authorized reopening ──▶ OPEN (revision + 1)
```

规则：

1. claim 是独立 lease 维度，不改变 workflowState；create/reopen同CAS插入`ESCALATE:<caseId>` due，ACQUIRE/RENEW插入或重排`CLAIM_EXPIRE:<caseId>` due。到期由§5显式scanner或迟到ACQUIRE的同CAS逻辑到期路径写 `ClaimExpired` 审计并清除 active claim，escalated/priority 保留。
2. escalation 只更新 `escalated/priority/escalatedAt`，仍可被具备权限的操作员认领和解决。
3. `RESOLVED` 表示 ledger 已完成原子裁决；`CLOSED` 表示二次核验/归档完成，不改变 resolutionHistory。
4. reopen 必须提供新证据或指出原证据失效并增加 revision；旧 currentResolutionId 在初次 resolve 时已唯一追加到 history，reopen 只清空 currentResolutionId，不得重复追加或删除旧审计。
5. reopen 原子把 `reconciliationRequired` 重新置为 true，但不把已裁决 ledger 倒回 `UNKNOWN`。若新证据支持原裁决，可用同一结果再确认并追加 history；若证明原裁决错误，必须创建受控纠错/补偿 effect attempt，reopened case 引用新 effect 的结果，原 ledger 和旧 resolutionHistory 保持不可变。

### 3.3 `ResolutionRecord` 与迁移矩阵

`ResolutionRecord` 为 immutable：

```text
resolutionId,caseId,caseRevision,result,evidenceIds,evidenceDigest,
effectAttemptId,correctiveEffectAttemptId?,resolvedBy,reasonCode,
idempotencyKeyHash,createdAt,configGeneration,recordDigest
```

`resolve` 先以 `caseId+Idempotency-Key+expectedRevision` 查唯一记录：同 body/digest 返回既有 ResolutionRecord，不同 body 冲突；只在不存在时追加一个 history member。迁移副作用固定：

| 操作 | from→to | claim | escalated/priority | currentResolutionId | history |
|---|---|---|---|---|---|
| resolve | OPEN→RESOLVED | 校验后清除 active claim，旧 token 永久失效 | 保留 | 新 resolutionId | 追加一次 |
| close | RESOLVED→CLOSED | 要求无 active claim；close reviewer 不得与 resolver 相同（高风险必选） | 保留 | 保留 | 不追加 resolution，只写 close audit |
| reopen | RESOLVED/CLOSED→OPEN | 清除 claim并递增 fencing generation；必须重新 claim 才能 resolve | 保留并可提高，不自动降低 | 清空 | 不覆盖旧记录；reopen audit 指向触发 evidence |

close/reopen/resolve 均 revision+1；任何旧 fencing token 在上述迁移后拒绝。首次 resolution、同结果复核和纠错 resolution 都使用新的 immutable record，但同一个幂等请求绝不重复追加。

### 3.4 陈旧 APPLYING 自动建单

Effect Reconciler 扫描 owner lease 已失效且超过 operation policy 的 `APPLYING` effectAttempt。State 以 `effectAttemptId` 唯一约束原子执行 `APPLYING → UNKNOWN + OPEN case + audit/outbox`；重复扫描、多个 Reconciler 或迟到 Runtime completion 只能返回同一 case。若 provider 后续返回确定回执，必须作为 Evidence 进入正常 resolution，不能跳过 case。

---

## 4. Evidence 合同

### 4.1 允许类型

| `type` | 内容 |
|---|---|
| `PROVIDER_QUERY` | 使用 provider reference 查询的当前结果和响应 digest |
| `PROVIDER_IDEMPOTENCY_RECORD` | provider 对 idempotency key 的持久记录 |
| `LOCAL_IMMUTABLE_RECEIPT` | 本地原子日志、签名回执或不可变提交记录 |
| `COMPENSATION_RECEIPT` | provider/本地补偿成功的可验证回执 |

### 4.2 EvidenceRecord

至少包含：

```text
evidenceId, caseId, effectIntentId, effectAttemptId, type, sourceSystem,
providerReferenceHash, idempotencyKeyHash, observedResult,
observedAt, collectedAt, collectorPrincipal, payloadSha256,
sourceVersion, sourceDigest, visibility, refCommitId, artifactId?, signature/keyId?, configGeneration
```

`observedResult` 只能是稳定枚举，例如 `FOUND_APPLIED/FOUND_FAILED/NOT_FOUND/FOUND_COMPENSATED/INCONCLUSIVE`。完整 provider body 如确有保留必要，先脱敏并保存为受控 Artifact；Case 只保存 digest 和 `artifactId`。不得保存 Token、Authorization header、客户数据或未脱敏响应。

证据写入append-only，但对外可见的Evidence不是先写正文再补Artifact ref：`append_reconciliation_evidence`必须把canonical EvidenceRecord、sourceVersion/sourceDigest和完整typed refs交给Artifact §5.5的`commit_typed_source_and_refs`，同一State CAS才将`visibility=VISIBLE`、evidence index、forward/reverse ref、retention lock和audit/outbox提交。CAS前/响应丢失时Evidence保持不可见；同commit重试逐字节返回原结果；source version更新移除ref也必须提交新canonical Evidence，不得独立删索引。纠错通过新增`EvidenceSuperseded`关系，不能覆盖原记录。resolution必须引用足以支持结果的visible evidence ID；自由文本comment只能辅助说明。

---

## 5. Claim lease与持久控制面

`a2a.v1.state.recon.claim`只接受closed `ReconciliationClaimControlRequestV1`。common字段恰为`schemaVersion,operation,caseId,claimOperationId,idempotencyKey,requestDigest,expectedRevision,reasonCode,authProof`；`operation=ACQUIRE|RENEW|RELEASE|EXPIRE|ESCALATE`，未列variant字段必须absent：

- `ACQUIRE`额外恰含`ownerInstanceId,leaseDurationMs`，reasonCode只能`OPERATOR_ACQUIRE`；
- `RENEW`额外恰含`ownerInstanceId,expectedClaimFencingToken,leaseDurationMs`，reasonCode只能`OPERATOR_RENEW`；
- `RELEASE`额外恰含`ownerInstanceId,expectedClaimFencingToken`，reasonCode只能`OPERATOR_RELEASE`；
- `EXPIRE`额外恰含`scanOperationId,candidateToken,observedClaimOwnerHash,observedClaimFencingToken,observedClaimExpiresMs`，reasonCode固定`LEASE_EXPIRED`；
- `ESCALATE`额外恰含`scanOperationId,candidateToken,observedEscalationDueMs`，reasonCode固定`UNCLAIMED_SLA_BREACH`。

operator幂等scope的exact bytes为`UTF8(caseId)+0x00+UTF8(operation)+0x00+UTF8(operatorPrincipalHash)+0x00+UTF8(Idempotency-Key)`，`idempotencyKeyHash=lowerhex(SHA-256(scopeBytes))`，所有五种operation统一使用`claimOperationId=lowerhex(SHA-256(UTF8("a2amesh-recon-claim-operation-v1")+0x00+scopeBytes))`，不存在绕过该公式的例外。

EXPIRE/ESCALATE的`Idempotency-Key`必须逐字节等于scanner candidate内确定性`dueOperationId`，但`claimOperationId`仍按上一段通用公式从`caseId,operation,Reconciliation Service稳定operatorPrincipalHash,dueOperationId`构造的scope派生，**claimOperationId不得等于dueOperationId**。`dueOperationId`只标识稳定due tuple，`claimOperationId`才是`recon.claim`业务CAS/operation ledger的最终身份；scanner owner/instance、scanner lease/fence、`scanOperationId,candidateToken`都不进入两个稳定ID。operator requestDigest是排除`requestDigest,authProof`后的完整request RFC8785 SHA-256；scanner业务requestDigest还排除临时`scanOperationId,candidateToken`，使接管scanner可用新token提交相同due tuple并命中同一claim operation。同operationId/scope/digest逐字节返回首次结果，异digest零写入。

`ReconciliationClaimControlResultV1`字段恰为`schemaVersion,claimOperationId,operation,caseId,workflowState,revision,claimOwnerHash,claimOwnerInstanceId,claimFencingToken,claimExpiresAt,escalated,priority,resultCode,auditEventIds,resultDigest`；nullable owner/expiry显式null，auditEventIds按同CAS产生顺序排列，resultDigest为排除自身后RFC8785 SHA-256。

唯一具名writer及CAS如下：

1. `acquire_reconciliation_claim`要求OPEN、expectedRevision匹配且无未过期owner。若旧claim已按State server time逻辑过期，同一CAS先固化`ClaimExpired`再发新claim；整个复合迁移revision只+1但auditEventIds依次含expired/acquired。它从`claim-fence:<caseId>`取得更高token，写10分钟或policy lease、重排expire due，并令本OPEN cycle的`claimedInCurrentOpenCycle=true`且移除escalate due。
2. `renew_reconciliation_claim`要求OPEN、Principal/instance/token/revision全等且尚未逻辑过期；每次从counter取得更高token、revision+1并重排expire due。旧token立即永久失效。
3. `release_reconciliation_claim`要求current且未过期owner/token；从counter取得更高tombstone fence、清owner/expiry、移除expire due、revision+1，不改变workflow/escalated/priority且不重新插入本cycle escalation。
4. `expire_reconciliation_claim`要求current scanner lease/fence、持久candidate tuple全等且`serverNowMs>=observedClaimExpiresMs=current claimExpiresMs`；从counter取得更高tombstone fence，清owner/expiry、移除expire due、revision+1并只写一次ClaimExpired audit/outbox。
5. `escalate_reconciliation_case`要求current scanner lease/fence、OPEN、该cycle从未claim、未escalated、candidate revision/due全等且serverNow到期；只设置escalated/priority/escalatedAt、移除escalate due、revision+1，不改workflow/history/claim。

`a2a.v1.state.recon.scan-due`只接受同一Reconciliation Service NKey的closed `ReconciliationDueScanRequestV1`，唯一State writer为`scan_reconciliation_due`。common字段恰为`schemaVersion,operation,scanOperationId,ownerInstanceId,idempotencyKey,requestDigest,authProof`；`ACQUIRE_SCANNER`额外恰含`leaseDurationMs`，`RENEW_SCANNER`额外恰含`scannerLeaseId,scannerFencingToken,expectedRevision,leaseDurationMs`，`SCAN_DUE`额外恰含`scannerLeaseId,scannerFencingToken,expectedRevision,limit`。State server time是唯一due判断；limit范围1..1000。scanner幂等scope bytes为`UTF8(operation)+0x00+UTF8(ownerInstanceId)+0x00+UTF8(Idempotency-Key)`，scanOperationId必须等于`lowerhex(SHA-256(UTF8("a2amesh-recon-due-scan-v1")+0x00+scopeBytes))`，requestDigest为排除自身/authProof后的完整request RFC8785 SHA-256。ACQUIRE生成32字节CSPRNG base64url无padding scannerLeaseId并从counter发新fence；RENEW保留leaseId。

`ReconciliationDueScanResultV1`字段恰为`schemaVersion,scanOperationId,operation,scannerLeaseId,scannerFencingToken,revision,leaseExpiresAt,candidates,resultDigest`；每个candidate恰为`dueKind,caseId,observedRevision,observedClaimOwnerHash,observedClaimFencingToken,observedDueMs,dueOperationId,candidateToken`，ESCALATE项两个observed claim字段显式null。candidateToken为32字节CSPRNG base64url无padding并与scan result持久化。dueOperationId输入的observedRevision/observedDueMs/非nulltoken使用无前导零十进制ASCII，null token固定四字节ASCII`null`；exact公式为`lowerhex(SHA-256(UTF8("a2amesh-recon-due-operation-v1")+0x00+UTF8(dueKind)+0x00+UTF8(caseId)+0x00+ASCII(observedRevision)+0x00+ASCII(observedDueMs)+0x00+ASCII(tokenOrNull)))`。同scanOperationId/digest逐字节重放相同候选/token；双scanner只有current lease/fence可扫描或提交EXPIRE/ESCALATE，过期后ACQUIRE_SCANNER发更高fence接管。

- **TEST-RECON-OPERATION-ID-001**：权威字节fixture位于`tests/fixtures/state_contracts/reconciliation_operation_identity_v1.json`：EXPIRE固定`dueOperationId=0a0e1680183d05aaaa9563a84ea2ed9932f7c4e9a9f1eec3a862a765d799f5bf`、最终`claimOperationId=d81b8dda0e3bde3fca24084f7dba551be6bb8775ea157ef5d58593b673756ef7`；ESCALATE固定`dueOperationId=bb3824f9afbe2e17badd1e41d6c88341d001fbe938b312e8798d187784b581a1`、最终`claimOperationId=5d54e6474f78b07cee5bee2ff48f245b221e51974af06e25adb4397639152910`。fixture同时冻结due preimage与claim scope hex；任一语言实现必须逐字节得到相同结果，并验证接管scanner不改变最终ID、同ID异digest零写入。

所有claim/due writer在同一Redis CAS写case、counter/due、operation exact result、audit/outbox；resolve/evidence即使scanner尚未运行也必须按server time拒绝逻辑过期token，resolve/close/reopen原子移除claim due并推进tombstone fence，reopen另启新claimSlaCycle/escalate due。自动 evidence collector使用服务机器身份可追加证据但默认不能执行最终人工resolution，除非确定性自动裁决经过独立门禁并在配置中显式启用。

---

## 6. Resolution 与 Task 不可变规则

### 6.1 结果语义

| resolution | ledger 终态 | 证明要求 |
|---|---|---|
| `APPLIED` | `UNKNOWN → APPLIED` | provider/本地证据确认目标副作用已生效 |
| `FAILED` | `UNKNOWN → FAILED` | provider 明确失败，或可证明未执行且不会晚到执行 |
| `COMPENSATED` | `UNKNOWN → COMPENSATED` | 原操作可能/已经执行，且补偿回执确认已消除业务影响 |

`NOT_FOUND` 单独通常不足以判定 FAILED；必须结合 provider 的一致性窗口、idempotency 语义和不会迟到执行的保证。连接超时、查询超时和无记录但仍在最终一致窗口内继续保持 UNKNOWN。

### 6.2 原子更新

`resolve_reconciliation_case` 必须在单一 State mutation 中：

1. 校验 case workflowState/revision、active claim、fencing token、capability 和 evidence 引用。
2. 初次 resolution 校验 ledger 仍是相同 effectAttempt 的 `UNKNOWN`，且 provider reference/idempotency hash 一致；reopened case 则校验 resolutionHistory 不变，并要求“同结果确认”或新的纠错/补偿 effect 证据。
3. 初次 resolution 写 ledger `APPLIED/FAILED/COMPENSATED`、evidence digest 和 resolvedAt；reopened case 不覆盖原 ledger，只追加确认记录或关联新的纠错/补偿 effect。
4. 写 workflowState=`RESOLVED`、currentResolution、resolutionHistory 引用、revision 和 append-only audit。
5. 重新聚合 Task 所有关联 effect/case；全部解除后把内部 `reconciliationRequired=false`。
6. 增加 Task version/eventSequence，但不改写已提交标准终态。
7. 写 `ReconciliationResolved` outbox；需要对用户展示时只追加脱敏 status message 或 Artifact 引用。

### 6.3 终态 Task

已因 `UNKNOWN` 提交为 `TASK_STATE_FAILED` 的 Task 永远不能在事后改成 `COMPLETED` 或 `CANCELED`。对账只追加：

- reconciliation result 扩展元数据；
- 脱敏审计/状态消息；
- 必要的结果或补偿 Artifact；
- 新的 Task version/eventSequence。

客户端若要继续业务，创建具有新 messageId 的新 Task，并显式引用原 case/Task；系统不能把旧失败 Task “修好”为成功，也不能自动重放原副作用。

---

## 7. 运维 API 与 CLI

### 7.1 API

| 方法 | 路径 | capability |
|---|---|---|
| `GET` | `/ops/v1/reconciliation-cases` | `ops.reconciliation.read` |
| `GET` | `/ops/v1/reconciliation-cases/{caseId}` | `ops.reconciliation.read` |
| `POST` | `/ops/v1/reconciliation-cases/{caseId}/claims` | `ops.reconciliation.claim` |
| `POST` | `/ops/v1/reconciliation-cases/{caseId}/claim-renewals` | `ops.reconciliation.claim` |
| `POST` | `/ops/v1/reconciliation-cases/{caseId}/claim-releases` | `ops.reconciliation.claim` |
| `POST` | `/ops/v1/reconciliation-cases/{caseId}/evidence` | `ops.reconciliation.evidence.write` |
| `POST` | `/ops/v1/reconciliation-cases/{caseId}/resolutions` | `ops.reconciliation.resolve` |
| `POST` | `/ops/v1/reconciliation-cases/{caseId}/closings` | `ops.reconciliation.close` |
| `POST` | `/ops/v1/reconciliation-cases/{caseId}/reopenings` | `ops.reconciliation.reopen` |
| `POST` | `/ops/v1/tasks/{taskId}/outbox-events/{eventId}/recoveries` | `ops.outbox.recover` |

列表支持稳定cursor、`workflowState/escalated/priority/provider/age`过滤和确定排序，不允许按自由文本provider body搜索。reconciliation mutating请求必须携带`Idempotency-Key`、`expectedRevision`；claim之后的写操作还需`claimFencingToken`。claim body恰为`expectedRevision,ownerInstanceId,leaseDurationMs,reasonCode`；renew body恰为`expectedRevision,ownerInstanceId,expectedClaimFencingToken,leaseDurationMs,reasonCode`；release body恰为`expectedRevision,ownerInstanceId,expectedClaimFencingToken,reasonCode`。owner Principal只取认证上下文且进入AuthProof/requestDigest，caller不得自报owner hash；API分别映射ACQUIRE/RENEW/RELEASE，EXPIRE/ESCALATE无公共HTTP入口。path caseId不得在body覆盖，同HTTP Idempotency-Key同body逐字节重放、异body 409。

outbox recovery body恰含`expectedHeadSeq,expectedEventDigest,repairEvidenceUri,repairEvidenceSha256,reasonCode`并携带非空`Idempotency-Key` header；`reasonCode`只允许`RETRY_AFTER_BROKER_REPAIR|RETRY_AFTER_CONFIG_REPAIR|RETRY_AFTER_VERIFIED_REVIEW`，`taskId/eventId`只取path且`eventId`格式必须为`<taskId>:<expectedHeadSeq>`。API生成`RecoverDeadOutboxRequestV1`时把path字段、body、Idempotency-Key和自身AuthProof原样绑定，caller不能提供其他State subject、自选Principal或覆盖path字段。

`repairEvidenceUri`只接受带内容地址的私有Artifact URI或append-only WORM URI，不接受可原地改写HTTP URL。其exact bytes必须是non-detached JWS General JSON `OutboxRepairEvidenceV1`：payload恰含`schemaVersion,evidenceId,meshId,configGeneration,taskId,eventId,eventSeq,payloadDigest,diagnosisCode,repairAction,verificationResult,issuedAt,expiresAt,producerPrincipal`；`repairAction=RETRY_SAME_EVENT`、`verificationResult=SAFE_TO_RETRY`，`diagnosisCode`只允许`BROKER_REPAIRED|ACL_OR_CONFIG_REPAIRED|TRANSIENT_RETRY_EXHAUSTED|OPERATOR_VERIFIED_SAFE_RETRY`。时间只接受UTC恰3位毫秒`Z`且证据在请求时未过期。protected header恰含`alg=EdDSA,kid,typ=a2amesh-outbox-repair-evidence+jws,schemaVersion=1`，由active trusted config中具有`ops.outbox.recover` capability的稳定机器Principal恰签1次；payload/protected/signing input/顶层envelope使用Config §7.1相同base64url+RFC8785构造，禁止unprotected header，`repairEvidenceSha256=SHA-256(exact envelope bytes)`。证据必须绑定当前mesh/config及请求taskId/eventId/expectedHeadSeq/expectedEventDigest；字段缺失/额外、URI bytes漂移、坏签名/过期/错误Principal均拒绝。

Ops API先做相同预验证，再以独立`ops-recovery` NKey/AuthProof只调用`a2a.v1.state.outbox.recover`；State ingress必须独立重取exact evidence并复验，不能信任API布尔结论。State四项比较互相独立：`blockedByDeadSeq == expectedHeadSeq`；`(taskId,expectedHeadSeq)`定位的记录存在且为`DEAD`；该记录的`eventId == path.eventId`；该记录的`payloadDigest == expectedEventDigest`。还必须验证`expectedHeadSeq=publishedSeq+1`、dead index和evidence绑定，全部成立才按Redis §6.15单CAS恢复同一eventId→PENDING。同key同body返回原结果；同key异body、旧head/digest、错误task/event、可变/坏hash证据或无capability均零写入拒绝。不得借该接口删除/skip原事件；确需语义替换时先持久化受审replacement event，原DEAD事实仍由event recovery字段和WORM audit保留。

### 7.2 CLI

```text
a2amesh ops reconcile list --state open
a2amesh ops reconcile show <case-id>
a2amesh ops reconcile claim <case-id> --expect-revision <n> --lease-ms <n> --idempotency-key <key>
a2amesh ops reconcile claim renew <case-id> --fencing-token <n> --expect-revision <n> --lease-ms <n> --idempotency-key <key>
a2amesh ops reconcile claim release <case-id> --fencing-token <n> --expect-revision <n> --idempotency-key <key>
a2amesh ops reconcile evidence add <case-id> --file <signed-record>
a2amesh ops reconcile resolve <case-id> --result applied --evidence <id>
a2amesh ops reconcile close <case-id> --reason <code>
a2amesh ops reconcile reopen <case-id> --evidence <id> --reason <code>
a2amesh ops outbox recover <task-id> <event-id> --expect-head <seq> --expect-digest <sha256> --evidence <immutable-uri> --evidence-sha256 <sha256> --reason <code>
```

CLI 只调用 API，不直接写 Redis、effect ledger 或 Task。命令输出默认脱敏，不打印 provider 凭据和完整响应。

---

## 8. 权限与职责分离

- Gateway/A2A/MCP 业务 Credential 不具备 `/ops` capability。
- `read`、`claim`、`evidence.write`、`resolve`、`close`、`reopen`、`ops.outbox.recover`分开授予；不存在通用admin通配。Reconciliation Service NKey不能继承outbox recovery，Ops Recovery NKey不能访问case resolution或其他State mutation。
- 默认 resolve 需要独立机器 Credential，且不能由产生该 UNKNOWN 的 Runtime instance 使用同一 Credential 自批。
- 高风险 operation 的 `COMPENSATED` resolution 可配置第二机器凭据复核；V1 可通过双签 API 请求实现，不要求 Web 审批系统。
- provider adapter 只能访问配置允许的 provider/reference，不能接受操作员输入任意 URL、shell 或数据库语句。
- 所有权限来自 active trusted config generation，generation 漂移时 fail closed。

---

## 9. SLA、升级与保留

默认运维目标：

| 条件 | 动作 |
|---|---|
| effect 进入 UNKNOWN | 原子创建 `OPEN` case 并立即告警 |
| UNKNOWN 持续 5 分钟 | P1，进入人工处理队列 |
| 15 分钟未 claim | `escalated=true`、提高 priority，通知升级联系人 |
| claim 10 分钟未续约 | lease 过期，可被重新认领 |
| 30 分钟无新证据 | P1 持续告警并执行 provider 专项 Runbook |
| resolution 完成 | 立即发 outbox；24 小时内完成 close/复核 |

Case、Evidence、Resolution 和 Audit 在线保留至少 365 天，之后进入不可篡改加密归档；不得因 Task TTL 到期提前删除。包含业务敏感内容的 evidence Artifact 采用最小可用保留和访问审计，但其 digest/元数据长期保留。

---

## 10. 失败矩阵

| 故障 | 行为 | 禁止行为 |
|---|---|---|
| provider 查询超时 | 追加 INCONCLUSIVE evidence，保持 UNKNOWN | 判定 FAILED 并重试原操作 |
| 自动 collector 不可用 | case 仍 OPEN/P1，人工按 Runbook 收证 | 关闭 case |
| claim lease 过期 | 旧 token 失效，重新认领 | 接受旧操作员晚到 resolution |
| ledger 已被其他 resolution 更新 | 409，读取当前事实并审计冲突 | 覆盖 ledger |
| State/Redis 不可写 | 不宣布 resolution 成功 | 仅在 CLI 本地记录“已处理” |
| outbox/Relay 故障 | ledger/case 已权威，outbox 重投 | 直接向 JetStream 发布替代事实 |
| evidence Artifact 不可读/hash 失败 | evidence 无效，不能 resolve | 只凭文件名或截图裁决 |
| config generation 变化 | 重新授权并校验 provider policy | 继续使用已撤销 Credential |
| 新证据推翻旧裁决 | authorized reopen + 新 effect/补偿流程 | 改写旧 audit 或 Task 终态 |

---

## 11. 可观测与审计

对账域必须提供 case 数量/年龄、claim/expiry、evidence 类型、resolution/耗时、升级和冲突信号。精确指标名、标签和告警阈值以《统计、审计与运行监控规则》为准；UNKNOWN 5 分钟 P1 使用 `OBS-ALERT-022`，15 分钟未 claim、claim 反复过期或 backlog 增长使用 `OBS-ALERT-029`。

WORM 受限审计必须使用《统计、审计与运行监控规则》的 canonical `AuditEnvelopeV1`；公共顶层携带 action/result/canonical `actorPrincipal`/requestId/traceId/taskId/configGeneration，对账 `payload` 只写 case/effect ID、claim fencing token、expected/actual revision、evidence ID/digest、resolution 和 reasonCode，不得另造平行顶层 casing。Redis 热索引/导出使用 deployment-keyed `actorPrincipalPseudonym+pseudonymKeyVersion`，不是裸 hash；metric label 不包含 caseId、taskId、Principal、provider reference 或自由文本。

---

## 12. 验收标准

- **TEST-RECON-CASE-001**：每个 UNKNOWN effect 原子创建唯一 case，重复回调不重复建单。
- **TEST-RECON-CLAIM-001**：用唯一closed wire覆盖ACQUIRE/RENEW/RELEASE/EXPIRE/ESCALATE；并发双claimant只有一个成功，每次renew发更高token，release/逻辑expiry/resolve/reopen推进tombstone fence。分别在case/due/result/audit/outbox CAS前后及commit-before-reply杀进程，同operation同digest逐字节重放且revision/token/audit只变化一次，异digest或旧owner/instance/token/revision零写入；旧token即使scanner延迟也不能evidence/resolve，失联后新operator可由lazy-expire ACQUIRE或scanner EXPIRE安全接管。
- **TEST-RECON-EVIDENCE-001**：只有允许类型、完整digest/来源/时间的证据可用于resolution，自由文本不能单独裁决；Evidence必须以`sourceType=EVIDENCE/sourceId=evidenceId`的source-centric path提交canonical正文及完整五字段refs，并由同一`commit_typed_source_and_refs` CAS与retention lock一起变为VISIBLE。正例一次引用两个Artifact并用新sourceVersion移除其一；逐项拒绝path/ref tuple漂移、四字段ref、目标content digest错误、old∪new expected versions少报/多报及按单Artifact授权跨目标修改。分别在canonical/ref校验、CAS前后、State commit-before-reply、与Artifact DELETE/Reaper竞争点杀进程；任一点不得出现VISIBLE Evidence+missing reverse/forward ref，旧sourceVersion/digest/Artifact version拒绝，重复commit逐字节重放且不重复case index/Artifact version/event/audit。
- **TEST-RECON-RESOLVE-001**：APPLIED/FAILED/COMPENSATED 与 ledger、case、Task 聚合、audit、outbox 原子一致。
- **TEST-RECON-IMMUTABLE-001**：已失败 Task 在任何 resolution/reopen 后仍保持原标准终态，只追加结果。
- **TEST-RECON-IDEMP-001**：Idempotency-Key、revision 和 fencing 冲突不会产生重复 resolution/event。
- **TEST-RECON-AUTHZ-001**：业务 Credential、旧 generation 和缺失细分 capability 均不能调用运维写接口。
- **TEST-RECON-SLA-001**：create/reopen同CAS插入ESCALATE due，首次claim永久移除本cycle升级；claim/renew重排10分钟expire due。双scanner中只有current lease/fence取得持久candidate/token；扫描响应丢失逐字节重放，接管后新token仍映射相同dueOperationId。15分钟未claim仅升级一次，EXPIRE/ESCALATE与ACQUIRE/resolve/reopen竞争只有一个CAS成功，失败路径revision/fence/due/audit/outbox全零变化。
- **TEST-RECON-DR-001**：Case/Evidence/Audit 恢复后可与 effect ledger、Task 和 provider 证据重新对账。
- **TEST-RECON-STATE-001**：workflowState、escalation、claim lease三个正交维度不存在非法互相覆盖；逐项验证五个具名writer、server-time逻辑expiry、OPEN cycle、due tuple和closed reasonCode，EXPIRE不改escalation/workflow，ESCALATE不改claim/history，RELEASE不重启已claim cycle升级。
- **TEST-RECON-REOPEN-HISTORY-001**：reopen 清 currentResolution 但 resolutionHistory/ledger/audit 不可变。
- **TEST-EFFECT-STALE-001**：陈旧 APPLYING先写入持久stale-due；只有持有效scanner lease/fence的effect-reconciler可经`a2a.v1.state.effect.scan-stale`以scanOperationId认领。覆盖owner lease失效前后、scanner claim/UNKNOWN CAS前后、State/reply丢失、双scanner与重启；同ID同digest逐字节重放，异digest/错误Principal/错误staleAfter零写入，最终只产生一次UNKNOWN、一个case、一个告警outbox且不重复provider调用。

---

## 13. G0 对账冻结合同

1. case 业务状态只有 OPEN/RESOLVED/CLOSED；claim lease 和 escalation/priority 是独立维度。
2. currentResolution 与 append-only resolutionHistory 分离，reopen 不产生“OPEN 但仍有 current resolution”的矛盾。
3. effectIntentId 跨安全重试稳定，effectAttemptId 唯一标识真实 provider 调用和 reconciliation case。
4. owner lease 失效且 APPLYING 超时必须自动、幂等转 UNKNOWN 并建单。
5. 原 Task 标准终态、原 ledger、旧 resolution history 永远不因 reopen/纠错而覆盖。
6. ResolutionRecord immutable 且按 revision 有序；resolve 清 claim、close 要求独立复核、reopen 清 current/claim 并使旧 fencing 失效。

---

## 14. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [Redis状态平面与数据设计 V1.6](A2AMesh_Redis状态平面与数据设计_V1.6.md)
- [任务生命周期与长任务运行时设计 V1.6](A2AMesh_任务生命周期与长任务运行时设计_V1.6.md)
- [编排器 Runtime与工具适配设计 V1.6](A2AMesh_编排器_Runtime与工具适配设计_V1.6.md)
- [接口请求与响应标准 V1.6](A2AMesh_接口请求与响应标准_V1.6.md)
- [受信配置与变更治理设计 V1.2](A2AMesh_受信配置与变更治理设计_V1.2.md)
- [统计审计与运行监控规则 V1.6](A2AMesh_统计审计与运行监控规则_V1.6.md)
