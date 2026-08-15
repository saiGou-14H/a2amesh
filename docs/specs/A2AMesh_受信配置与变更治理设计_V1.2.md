# A2AMesh 受信配置与变更治理设计 V1.2
> 文档ID：`A2AM-CONFIG-001`
> 文档状态：设计基线（待代码实现与验收）
> 权威范围：签名配置 bundle、generation、校验、暂存、激活、回滚、撤销、组件就绪与 Card publisher ownership
> 目标读者：架构、后端、Gateway、Peer、安全、测试、运维
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

本文档定义 A2AMesh 所有“受信配置”的统一生命周期。Credential/Alias、capability grant、Card publisher 候选者、delivery profile、Artifact policy、Runtime/Tool policy 必须来自同一可验证 generation，不能由各组件分别读取未签名 YAML 后自行解释。

任一 Mesh 同时只能存在一个单一 active generation。`STAGED` 仅用于无流量校验，任何请求、授权、Card、Artifact 或 Runtime 执行都不得把 staged 内容与 active 内容混合使用。

本文不建设用户、角色、组织、通用 RBAC 或强制 Web 管理后台。配置运维通过独立机器 Credential、最小 `ops.config.*` capability 和 API/CLI 完成。当前代码尚未实现本合同，所有 READY、ACTIVE、回滚和故障结论均待实现验证。

### 1.1 版本说明

| 版本 | 日期 | 变更说明 |
|---|---|---|
| V1.0 | 2026-08-14 | 建立签名 bundle、单 active generation、两阶段激活、回滚撤销、启动 fail closed 和 publisher lease 合同 |
| V1.1 | 2026-08-14 | 同步 V1.5/V1.1 权威引用，受信配置领域合同不变 |
| V1.2 | 2026-08-14 | 闭合 G0：hash/JWS、genesis、READY认证、稳定publisher身份、滚动代际及无自引用GateEvidenceRecord激活证据 |

---

## 2. 权威边界

| 内容 | 权威来源 | 运行时物化 |
|---|---|---|
| bundle 内容与签名 | 不可变版本化配置制品 | 本地只读缓存 + Redis generation 快照 |
| 当前 active generation | Config Controller 的 Redis CAS pointer | 所有组件只消费该 generation |
| Credential/Alias/Grant | active bundle | State Service 原子物化索引 |
| Card publisher 候选者 | active bundle | Redis lease + fencing 选出唯一 active publisher |
| delivery profile 期望 | active bundle | 组件 READY 和兼容门禁决定实际可广告子集 |
| activation gate证据 | 独立签名、不可变`GateEvidenceRecordV1` | active pointer绑定bundle content hash与evidence hash；不得回写bundle |
| Artifact/Runtime/Tool policy | active bundle | 对应服务按 generation fail closed 执行 |
| secret 值 | OS Secret Store/受保护文件/外部 Secret Manager | bundle 只保存 `secretRef` 和版本约束 |
| 审计 | append-only config audit | 不可由新 bundle 覆盖历史 |

根信任公钥、允许的签名算法和紧急撤销公钥必须通过部署镜像/主机受保护配置固定，不能由待验证 bundle 自己替换自己。密钥轮换采用旧根签新根或离线批准的双签过渡，具体操作必须留审计证据。

---

## 3. Bundle 合同

### 3.1 Envelope

bundle 是 RFC 8785 canonical JSON，使用允许算法的 JWS 签名。至少包含：

```json
{
  "bundleId": "cfg_opaque",
  "generation": 42,
  "previousGeneration": 41,
  "deploymentId": "deploy-prod-001",
  "genesis": false,
  "genesisNonce": null,
  "meshId": "default",
  "issuedAt": "2026-08-14T00:00:00Z",
  "notBefore": "2026-08-14T00:05:00Z",
  "expiresAt": "2026-09-13T00:00:00Z",
  "schemaVersion": "1.0",
  "contentSha256": "...",
  "credentials": [],
  "aliases": [],
  "grants": [],
  "components": [],
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
8. `deploymentId` 必须匹配主机 bootstrap；g1 的 `genesis=true/genesisNonce` 必须进入签名 payload，正常 generation 固定 `genesis=false/genesisNonce=null`。

`contentSha256`精确定义为：对**排除contentSha256和所有JWS signature字段**后的payload执行RFC 8785，再取SHA-256小写十六进制。bundle只包含静态`requiredGateTestIds`，严禁出现报告URI/hash或gateEvidenceRefs；测试结果永远位于独立GateEvidenceRecord，不能反向改写bundle。JWS使用General JSON Serialization；protected header至少含`alg/kid/typ="a2amesh-config+jws"`，签名覆盖包含已计算contentSha256的完整canonical payload。所有Config/GateEvidence General JWS的顶层`signatures[]`恰只含`protected,signature`，解码protected header后的`kid`必须唯一并按UTF-8字节严格升序；重复kid、反序、缺kid、额外entry字段、unprotected header或threshold不足均在digest/验签前拒绝。根轮换允许旧根+新根双签，验签策略和阈值来自主机信任根配置，不能由bundle自己降低。

最终 wire 固定为 JWS General JSON：`payload=BASE64URL(UTF8(RFC8785(fullPayloadWithContentSha256)))`，`signatures[]` 每项包含 `protected=BASE64URL(RFC8785({alg,kid,typ}))` 与 `signature=BASE64URL(Sign(protected || "." || payload))`；禁止 detached/unencoded payload、额外 unprotected header 和 padding。仓库 fixture 必须同时保存 payload canonical bytes、content hash、protected bytes、完整 JWS 与坏 hash/坏签名反例，所有语言实现按字节互验。

### 3.2 内容最小约束

| 区域 | 必须校验 |
|---|---|
| `credentials` | 稳定 credentialId、类型、Principal、状态、有效期、rotationGroup、secretRef |
| `aliases` | source/target 稳定、单跳、无环、不可改指历史 owner |
| `grants` | Principal、targetAgent、operation/skill/toolRisk/workspaceAlias 全维明确，无隐式通配 |
| `components` | componentType、稳定 componentPrincipal、nodeId、允许 instance NKey/selector、requiredForProfiles、schema/feature/version 范围；READY 候选唯一映射 |
| `cardPublishers` | agentId、稳定 publisherPrincipal/nodeId 候选、lease TTL、Card 输入引用；不预声明每次启动 UUID |
| `deliveryProfile` | `CORE/INTEROP/EXTENDED`目标子集、依赖组件、静态`requiredGateTestIds`、signed `explicitlyRequiredOperationalSlots[]`、由`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`确定性生成的`requiredSlots[]`；NATS §9.5 exact `TaskEventStreamConfigV1`及其desiredConfigDigest、session ackWait/expiry/slow-consumer/brokerOperationLeaseMs/brokerApiApplyMaxMs上限和ACL template version；不得含报告引用/hash |
| `artifactPolicy` | inline/对象上限、MIME、URL TTL、保留、bucket alias、扫描策略 |
| `runtimePolicy` | Runtime 版本/二进制 digest、ContainmentProfile/hash、并发、超时、workspace alias、SideEffectAdapter |
| `toolPolicy` | registry、risk、参数 schema、网络/文件边界、side-effect 分类 |
| `recoveryPolicy` | recovery Manifest signer set/version；closed requiredComponents[]的componentType/componentPrincipal/nodeId/verificationMethod/expectedDigest；恢复/释放阈值 |

配置只能请求发布能力，不能替代实现/兼容验收。即使 bundle 请求 `INTEROP`，Card 也只能在对应组件 READY 且实施计划门禁证据有效后广告 gRPC/Push；MCP 永远不作为 A2A Binding。

启用 NATS 两个流式操作时，`components[]` 必须分别列出稳定 `stream-session-controller` 与 `js-provisioner` Principal/node/NKey selector，二者不得共用运行时 NKey；前者只能按 DATA-STREAM-SESSION-001 管理 frame/代理 ACK，后者是固定 stream 上唯一 `$JS.API` 身份。`deliveryProfile.taskEventStreamConfig`必须逐字段符合NATS §9.5，且`taskEventStreamConfigDigest=SHA-256(RFC8785(TaskEventStreamConfigV1))`；缺任一 READY、stream config/digest、ACL template/session 参数与 NATS §9.4/§9.5/§16.6 不一致时，generation NACK，不得降级为 Peer 多 response 或授予 Peer/Gateway JS 权限。

每个启用执行/编排的Agent node还必须在`components[]`分别列出`peer-binding`、`application-core`、`task-supervisor`、`orchestrator`的稳定Principal与不同NKey selector。Peer Binding与Application Core条目还必须分别固定NATS §16.9所需`ipcProfileVersion=1`、host OS principal和binary digest；实际UID/GID或Windows service SID、端点ACL、server identity、binary任一不匹配均READY NACK。Peer节点Application Core只获11操作所需task claim/get/cancel/append与最小查询/stream-open subjects，并且§16.9端点只接受Peer Binding已验证envelope；Supervisor直接订阅dispatch并只获command.get/recover/heartbeat/lease/transition/dispatch/input/effect subjects；Orchestrator只获Plan subjects；Peer Binding不继承三者权限。`application-core`条目还明确内嵌Merge Broker：它是Core唯一shared-root writer，不是独立`components[]` slot，不拥有额外NKey、NATS subject或READY；workspace lease/fencing只由Core-owned模块消费，Task Supervisor/Runtime/Tool只能通过§16.9受保护typed request申请Merge，不能直接写shared root。Public Gateway部署必须另列`gateway-adapter`与`application-core`，两者绑定同一`deploymentUnitId`和binary digest：adapter仅有NATS §16.6 gateway transport ACL，Core NKey只由同进程typed Core模块持有并执行State RPC；Gateway不得连接§16.9端点或获得任意State subject。任一复用Runtime/Tool/其他组件NKey、角色缺失或ACL literal set与NATS §16.6不一致均NACK。所有`components[]`候选的同一NKey由ACL生成器仅额外叠加自身`a2a.v1.state.config.ready` publish和既有私有inbox，不得获得其他组件READY、Task/Plan/effect或JS权限。

启用ordered Event Outbox的CORE generation还必须分别列出`event-relay`与`ops-recovery`稳定Principal/NKey。Relay只获claim/reclaim/published/reschedule；Ops Recovery只获`outbox.recover`且须经独立机器Credential/capability和不可变repair evidence。两者不得共用NKey或继承彼此权限，缺任一READY或DEAD→PENDING正反例未通过均NACK。

所有启用DATA-AUDIT-001及任一受保护操作的delivery profile都必须在`components[]`列出独立`audit-relay`稳定Principal、nodeId、NKey selector、binary digest和`requiredForProfiles`。该NKey只获NATS §16.6的`a2a.v1.state.audit.claim`、`a2a.v1.state.audit.ack`、自身私有inbox及READY overlay；它是Audit Segment签名/WORM投递的唯一运行组件，但外部`AUDIT_SINK`仍是独立required recovery slot，二者不得共用Principal、NKey或被视为同一READY。`audit-relay`不得与`event-relay`、State、Recovery Orchestrator/Verifier/Compactor或Runtime复用身份；组件/READY缺失、ACL多授其他State subject、WORM receipt read-back或claim/ack crash fixture未通过均NACK。

启用Artifact/Object Store的generation必须在`components[]`列出`artifact-adapter`稳定Principal/nodeId/NKey selector和`requiredForProfiles`。该NKey只获NATS §16.6的artifact create/finalize/delete、hold create/renew/release、唯一`artifact.source.commit`、自身inbox及READY overlay；旧target-centric ref增删subject不得出现在rendered ACL。State对source.commit仍重验source owner AuthProof、source path tuple及跨全部touched Artifact的版本/内容，adapter transport身份不能自授跨目标能力。组件/READY缺失、ACL漏source.commit或多授旧ref/Task/Reconciliation/Recovery subject、跨两个Artifact正反例未通过均NACK。

启用DATA-RECON-001的generation必须在`components[]`列出`reconciliation-service`稳定Principal/nodeId/NKey selector和`requiredForProfiles`。该NKey只获NATS §16.6的`recon.open/claim/scan-due/evidence/resolve/close/reopen`、`effect.scan-stale`、自身inbox及READY overlay；claim operator capability仍来自受控Ops机器Credential，不能由service NKey自授。`recon.scan-due`复用同一组件身份但State另验persistent scanner lease/fence/candidate；不得与Runtime、Artifact、Ops Recovery或Recovery Compactor复用NKey。组件/READY缺失、ACL漏scan-due或多授outbox/recovery/Task/effect mutation、五操作/双scanner负例未通过均NACK。

启用DATA-RECOVERY-001 summary/archive compaction的generation必须在`components[]`列出三个彼此独立的稳定slot：`recovery-orchestrator`、`recovery-verifier`、`recovery-compactor`，每项恰含Principal、nodeId、NKey selector、binary digest、signing kid slot和`requiredForProfiles`。`recovery-orchestrator`是Manifest payload/seal与ReleaseReceipt唯一writer，只可publish `a2a.v1.state.recovery.seal`、`a2a.v1.state.recovery.release`；`recovery-verifier`是VerificationReceipt/RestoreReceipt唯一writer，只可publish `a2a.v1.state.recovery.verify`、`a2a.v1.state.recovery.restore`；`recovery-compactor`是ArchiveTransition/summary compaction唯一writer，只可publish唯一`a2a.v1.state.recovery.compact` closed union。三者不得与Plan `orchestrator`、State、Audit、彼此复用NKey或signing key；每个slot分别报告CANDIDATE/PRODUCTION_GATED READY，组件缺失、signing kid错位、ACL多授其他Recovery subject或旧fence/replay负例未通过均NACK。State按Redis §5.20.1 persistent due/scan/source lease/fence/transition ledger重验。

`RequiredSlotSetV1(profileName,bundle,deploymentDescriptor)`是所有profile/READY/Recovery合同唯一的稳定slot算法：先从signed deployment descriptor取得固定基础slot集合`CONFIG_CONTROLLER,STATE_SERVICE,GATEWAY,NATS_JETSTREAM,OBJECT_STORE,ARTIFACT_BROKER,AUDIT_SINK`，每个类型至少一个稳定slot；再取`components[]`中`requiredForProfiles`数组按UTF-8精确包含`profileName`的组件slot；最后并入signed `deliveryProfile.explicitlyRequiredOperationalSlots[]`，其中每项恰含`componentType,componentPrincipal,nodeId`并必须逐字节解析到`components[]`或deployment descriptor中的现有稳定slot，不能凭空创建身份。固定基础slot、profile组件slot和显式operational slot均只以`(componentType,componentPrincipal,nodeId)`作为身份；Core内嵌Merge Broker归属于`application-core`，不得作为额外operational slot。动态`instanceId`、process PID、NKey seed和READY requestId不得进入required slot集合。结果先去重，再按三字段UTF-8字节严格升序生成`deliveryProfile.requiredSlots[]`；该数组是由Config Controller确定性生成并签入bundle的closed projection，caller不能自行提供、删减、追加或以通配符替代，validator必须从`components[]`与deployment descriptor重算并逐字节比较。`recoveryPolicy.requiredComponents[]`必须恰等于同一`RequiredSlotSetV1`的带证明投影：每项恰含`componentType,componentPrincipal,nodeId,verificationMethod,expectedDigest`，按前三字段同序排列，expectedDigest恰为64位小写SHA-256；因此requiredSlots、requiredComponents、RestoreReceipt的componentVerificationDigests三者稳定slot集合必须完全相等。任意profile少报/多报、重复/空slot、profile名称大小写漂移、动态instanceId冒充稳定slot、deployment descriptor与bundle不一致或expectedDigest格式错误均REJECTED。

每个`RequiredSlotSetV1`成员都必须有可达的READY证明路径：`components[]`运行组件以自身允许NKey/AuthProof调用`config.ready`；不原生运行A2AMesh组件的固定基础slot（例如外部NATS/Object Store/Audit Sink）必须在signed deployment descriptor中额外绑定`readyReporterPrincipal,verificationMethod,expectedDigest`及其独立probe credential，State重验probe结果后仍以该stable slot的`componentType,componentPrincipal,nodeId`签发`ComponentReadyAttestationReceiptV1`。不得由Config Controller以自身Principal代报、由其他slot复用receipt、因组件无本地NKey而静默省略，或把一个probe receipt复制给多个slot。

启用NATS Binding的CORE generation，其`deliveryProfile.requiredGateTestIds`必须恰包含`TEST-NATS-ACL-001`与`TEST-NATS-STREAM-SESSION-001`；这里只声明静态门禁ID，不包含尚未生成的报告。报告URI/SHA-256及bundle/ACL绑定只进入§3.3独立GateEvidenceRecord；payload出现`gateEvidenceRefs`或任意报告hash必须NACK。

### 3.3 DATA-GATE-EVIDENCE-001：独立 activation 证据

`GateEvidenceRecordV1`是bundle stage之后生成的独立RFC8785/JWS General JSON记录，payload字段恰为`schemaVersion,evidenceId,evidencePurpose,meshId,generation,bundleContentSha256,deliveryProfile,aclDigest,readyAttestations,readySetDigest,reports,issuedAt,expiresAt,evidenceSha256`；`evidencePurpose`是closed enum `CANDIDATE_TEST|PRODUCTION_ACTIVATION`。`reports[]`按testId UTF-8字典序排列，每项恰为`testId,status,skipCount,reportUri,reportSha256,testedBundleContentSha256,testedAclDigest,environmentDigest,startedAt,completedAt`；相同testId只能出现一次。`readyAttestations[]`对bundle dependency matrix每个required component slot恰选一份未过期不可变READY receipt，canonical item字段固定为`componentType,componentPrincipal,nodeId,instanceId,generation,readinessPlane,rolloutLeaseId,rolloutFencingToken,contentSha256,deployedAclDigest,deployedStreamConfigDigest,environmentDigest,requestId,authProofDigest,attestationJwsJson,attestationJwsDigest,observedAt,expiresAt`，数组按`(componentType,componentPrincipal,nodeId,instanceId,requestId)`各字段UTF-8字节升序排序，对包含exact attestationJwsJson字符串的完整RFC8785数组取SHA-256得到readySetDigest。`readinessPlane`只允许`CANDIDATE|PRODUCTION_GATED`；CANDIDATE时rolloutLeaseId/rolloutFencingToken可显式null，PRODUCTION_GATED时二者必须非null且绑定当前rollout，三种deployed digest/environment字段不得为null。`observedAt/expiresAt`必须是UTC、恰3位毫秒和`Z`的RFC3339字符串。组件后续renew产生新receipt而不改旧bytes；只要所选receipt尚未过期且current pointer无NACK，它仍有效，不因正常续租造成digest活锁。

`evidenceSha256=SHA-256(RFC8785(payload排除evidenceSha256和所有signature字段))`；protected header固定`alg/kid/typ="a2amesh-gate-evidence+jws"`，顶层`signatures[]`按解码kid UTF-8严格升序且无重复/额外字段，签名覆盖包含evidenceSha256的完整canonical payload，验签根/阈值来自主机固定release policy。每份报告必须status=PASS、skipCount=0；reportUri必须是带内容地址的私有Artifact URI或WORM URI，不接受可原地改写的普通URL，State Service读取的exact bytes必须与reportSha256匹配。报告同时绑定本record的bundleContentSha256和aclDigest；environmentDigest必须等于本次候选部署环境。reports只绑定先前已stage的bundle/ACL，不得包含evidenceId/evidenceSha256；record在报告之后签发，所以hash依赖图严格单向，不存在bundle↔report或report↔evidence自引用。

同generation可因测试重跑stage多个不可变evidence record，但不能修改旧record；activate request必须显式选择一个`evidencePurpose=PRODUCTION_ACTIVATION`的evidenceSha256。record中required test集合必须与bundle deliveryProfile完全相等，readyAttestations必须精确覆盖required slots且每份State签名receipt exact bytes/digest/TTL仍有效；candidate测试Evidence可以证明隔离broker门禁，但不能单独授权production active CAS；用于activate的所选record必须每个required slot均为`PRODUCTION_GATED`并绑定同一rolloutLeaseId/fence、deployed ACL/stream digest和production environmentDigest。任何缺失、重复、额外required替代、当前NACK、报告漂移、ACL重渲染变化、plane/lease/fence/environment漂移、receipt签名/expiry失败均拒绝。若所选READY过期，只能选择新receipts并签发新evidence，不能改旧record。

GateEvidence的`readyAttestations[]`稳定slot集合必须逐字节等于当前bundle/profile的`deliveryProfile.requiredSlots[]`，每个`(componentType,componentPrincipal,nodeId)`恰一份有效receipt，不得以多个instance、缺失slot或额外slot替代；`instanceId`只选择该stable slot的运行实例。`CANDIDATE_TEST`只能选择`readinessPlane=CANDIDATE`并绑定candidate bundle/ACL/environment，`PRODUCTION_ACTIVATION`只能选择`PRODUCTION_GATED`并绑定同一rolloutLeaseId/fence、deployed ACL/stream/environment；两种平面的receipt不能混组、互相替代或通过只改`readySetDigest`绕过重算。

`attestationJwsJson`是只含ASCII的canonical non-detached JWS General JSON envelope文本，作为JSON string嵌入GateEvidence payload；取该string的UTF-8 bytes必须逐字节等于State最初签发的envelope并匹配attestationJwsDigest，解析后重新RFC8785序列化不等即拒绝。GateEvidence签名同时覆盖exact string与digest，所以热receipt key过期/删除、Redis全损或component离线后仍可从GateEvidence本身重新验证历史READY。`expiresAt`只判定receipt能否参与本次stage/activate；过期不得用于新的激活或恢复后宣称当前健康，但不得删除/否认已签入历史GateEvidence的exact证明。

---

## 4. 生命周期

### 4.1 状态机

```text
submitted ── verify/schema/semantic ──▶ VALIDATED ── persist immutable ──▶ STAGED
    └──────────────── failure ────────▶ REJECTED

STAGED ── required components READY + valid GateEvidenceRecord + CAS ──▶ ACTIVE ── newer activation ──▶ SUPERSEDED
   └──────────────── validation/timeout ────▶ REJECTED

ACTIVE(g42) ── activate g43 with rollbackOfGeneration=42 ──▶ ROLLED_BACK(g42) + ACTIVE(g43)
```

`ROLLED_BACK` 标记被回滚离场的 generation；承载恢复内容的新 generation 仍是 `ACTIVE`。active pointer 不允许降回较小 generation。任何恢复旧内容都必须创建、签名、校验和激活更高 generation，并记录 `rollbackOfGeneration`、来源 content hash 和原因。

### 4.2 Validate

Config Controller 验证签名链、canonical hash、schema、时间窗、meshId、generation/CAS、引用完整性、alias/grant/publisher 约束、secretRef 可解析性和目标组件兼容范围。validate 是只读操作，不写运行时索引。

### 4.3 Stage

stage 把已验证 bundle 作为不可变记录写入 State，并发布 `ConfigStaged`。各组件读取候选 generation，在不改变流量的情况下检查：

- 本组件认识 schema 和所有必选字段；
- secretRef 存在且权限正确，但不把 secret 返回 Controller；
- 外部依赖可达、目标 bucket/Runtime/tool registry 可验证；
- 从 active 到 staged 的迁移可执行且不会破坏历史 Task；
- 本地缓存落盘并验证签名/hash。

组件写带TTL的READY/NACK request：字段恰为`componentType,componentPrincipal,nodeId,instanceId,generation,readinessPlane,rolloutLeaseId,rolloutFencingToken,contentSha256,deployedAclDigest,deployedStreamConfigDigest,environmentDigest,status,reasonCode,observedAt,expiresAt,requestId,authProofDigest,authProof`，时间必须是UTC恰3位毫秒`Z`；CANDIDATE的rolloutLeaseId/rolloutFencingToken可显式null但deployed digest/environment仍必须绑定candidate fixture，PRODUCTION_GATED的lease/fence必须匹配当前维护rollout且trafficGate=CLOSED、组件只开放health/config.ready listener而不承接业务。State先执行通用`claim_auth_request`并验证组件AuthProof覆盖exact canonical request/subject/reply/target，再验证稳定component Principal/nodeId位于bundle `components[]`且允许当前NKey/instance、content hash/generation/plane/rollout/deployed ACL/stream/environment精确匹配。验证成功后State签发`ComponentReadyAttestationReceiptV1`：payload恰含`schemaVersion,componentType,componentPrincipal,nodeId,instanceId,generation,readinessPlane,rolloutLeaseId,rolloutFencingToken,contentSha256,deployedAclDigest,deployedStreamConfigDigest,environmentDigest,status,reasonCode,requestId,authProofDigest,readyRequestDigest,observedAt,expiresAt`；`readyRequestDigest=SHA-256(RFC8785(request排除authProof字段))`。protected header恰含`alg=EdDSA,kid,typ=a2amesh-component-ready-attestation+jws,schemaVersion=1`，由deployment trust root授权的State attestation signer恰签1次（genesis阶段使用bootstrap固定State signer，后续使用当前accepted trust policy），使用non-detached JWS General JSON、禁止unprotected header和§7.1相同base64url/RFC8785 envelope构造；`attestationJwsDigest=SHA-256(exact envelope bytes)`。State按requestId保存exact receipt bytes/digest，另以CAS更新component-current pointer，不覆盖旧receipt。后续GateEvidence/activate只重新验证State receipt的exact bytes/digest/signature、绑定的authProofDigest、plane/rollout/deployed digest/environment和TTL，不要求从digest反推或重新取得原component AuthProof；receipt签名即State已完成原AuthProof验证的权威证明。自由文本只用于诊断，Controller只根据稳定reason code裁决。

bundle达到STAGED后，Config Controller按bundle中的ACL template version确定性渲染完整NATS配置并计算`aclDigest=SHA-256(exact rendered ACL bytes)`；先以同broker版本/JetStream参数将**同一exact bytes**部署到隔离candidate broker，生产broker继续运行active ACL且candidate broker不接生产流量。candidate broker上的专用State ingress使用候选State NKey：只有签名`config.ready` attestation写入权威Redis的staged config keys；ACL/stream门禁所需Task/session/effect调用全部路由到独立测试Redis namespace，并在报告后销毁。任何candidate业务请求指向权威Task/effect/upload key都因generation非active和namespace guard双重fail closed，不建立第二生产State authority。所有门禁报告必须绑定该aclDigest与包含broker/State ingress/test namespace/component binary digest的candidate `environmentDigest`。aclDigest是候选派生物，不回写bundle；任何模板、组件NKey、broker参数或渲染字节变化都会产生新digest并使旧报告失效。

生产promote固定为fail-closed维护事务：取得唯一rollout lease，Gateway拒绝新请求、停/排空Dispatch/Event Relay/Controller新操作，阻断应用侧NATS listener并确认无新业务publish；保留Config→State受控管理通道。随后对生产broker先执行`nats-server --config <candidate> -t`，再reload隔离测试过的同一exact bytes，并由Config Controller从部署文件重读计算`deployedAclDigest`；然后按NATS §9.5/Redis §6.24以同rollout lease驱动固定Task Event Stream的INFO→必要CREATE/UPDATE→fresh INFO，取得State `CONFIRMED`的`deployedStreamConfigDigest`。在active CAS前，必须以该exact deployed ACL/stream、同一generation、rollout lease/fence和production environment启动新组件的**GATED_PASSIVE**实例：只开放health/config.ready受控入口，不订阅业务流量、不接新Task/effect/upload。每个required slot在该生产维护域提交`readinessPlane=PRODUCTION_GATED` READY，State验证lease/fence/deployed ACL/stream/environment并签发receipt；所有required production READY有效后，release actor必须以这些receipt和生产environment签发新的GateEvidence（candidate broker evidence不可替代），再允许ACTIVATE。若reload、stream reconcile/digest复验、production READY/evidence或activate CAS失败，必须在流量仍关闭时恢复已保存的exact active ACL bytes并验证active digest；stream禁止自动破坏性回退，若候选无损UPDATE已发生但activate失败，继续保持流量关闭并按active bundle发起新的受审计无损reconcile，不能DELETE/recreate。active CAS成功后，组件已经在生产gated域运行；只允许在State trafficGate仍CLOSED时重验health并调用FINISH_ROLLOUT切换业务listener/开State业务门，最后开放外部Gateway。任何时刻都不允许staged与active组件同时承接生产请求。

`RolloutControlRequestV1`是`a2a.v1.state.config.activate`唯一closed union。所有variant共同字段恰为`schemaVersion,operation,generation,rolloutOperationId,idempotencyKey,requestDigest,reasonCode,authProof`；requestDigest、scope与result ledger按Redis §5.14，未列variant字段必须absent：

- `PREPARE_ROLLOUT`额外恰含`candidateEvidenceSha256,expectedActiveGeneration,candidateAclDigest,candidateStreamConfigDigest,ownerInstanceId,leaseDurationMs`；State要求该SHA已stage、purpose=CANDIDATE_TEST且bundle/ACL/报告/环境仍有效，只授权进入维护事务，不授权active CAS；随后分配rolloutLeaseId与首个fence/revision。
- `RENEW_ROLLOUT`额外恰含`rolloutLeaseId,rolloutFencingToken,expectedRevision,ownerInstanceId,leaseDurationMs`；只允许未过期current owner/fence，过期后必须TAKEOVER。
- `ENTER_MAINTENANCE`额外恰含`rolloutLeaseId,rolloutFencingToken,expectedRevision,ownerInstanceId,drainEvidenceDigest`；同CAS置trafficGate=CLOSED。
- `TAKEOVER_ROLLOUT`额外恰含`rolloutLeaseId,observedFencingToken,expectedRevision,expectedState,newOwnerInstanceId,recoveryEvidenceUri,recoveryEvidenceDigest,leaseDurationMs`；只接受signed Config Controller NKey叠加独立机器Credential的`ops.config.recover`，且旧lease已过期或存在WORM signed handoff；CAS发更高fence，绝不打开trafficGate。
- `ACTIVATE`额外恰含`rolloutLeaseId,rolloutFencingToken,expectedRevision,ownerInstanceId,evidenceSha256,deployedAclDigest,deployedStreamConfigDigest`。
- `RESTORE_ACTIVE_ACL`额外恰含`rolloutLeaseId,rolloutFencingToken,expectedRevision,ownerInstanceId,restoredActiveAclDigest,restoredActiveStreamConfigDigest,recoveryEvidenceUri,recoveryEvidenceDigest`；只在active pointer仍为expected old generation且两个active digest重验相等时RESTORED/OPEN。
- `FINISH_ROLLOUT`额外恰含`rolloutLeaseId,rolloutFencingToken,expectedRevision,ownerInstanceId,healthEvidenceUri,healthEvidenceDigest`；只在active pointer已为candidate、ACL/stream digest与新generation相等、required health/READY有效时COMPLETED/OPEN。
- `MARK_FAILED_CLOSED`额外恰含`rolloutLeaseId,rolloutFencingToken,expectedRevision,ownerInstanceId,failureEvidenceUri,failureEvidenceDigest`；只固化FAILED_CLOSED/CLOSED，之后仍须受信TAKEOVER选择RESTORE或FINISH。

`RolloutControlResultV1`字段恰为`schemaVersion,rolloutOperationId,generation,rolloutLeaseId,state,trafficGate,revision,ownerInstanceId,rolloutFencingToken,leaseExpiresAt,activeGeneration,resultCode,resultDigest`；leaseExpiresAt使用UTC三位毫秒Z或terminal时显式null，resultDigest为排除自身后RFC8785 SHA-256。active CAS后崩溃/lease过期的TAKEOVER必须从active pointer发现candidate已生效，只能续做FINISH；CAS前崩溃可继续安全步骤或RESTORE；任何不确定证据保持FAILED_CLOSED。每次响应丢失重入逐字节返回operation ledger result。

### 4.4 Stage Gate Evidence

candidate门禁通过后，release actor可先为隔离candidate选择READY并生成candidate evidence；但production promote必须在同rollout维护域取得每个required slot的`PRODUCTION_GATED` receipt后，再选择这些不可变receipt组装新的GateEvidenceRecord。每份receipt必须有效期覆盖预计activate窗口，并验证exact bytes/digest/signer/TTL、rolloutLeaseId/fence、deployed ACL/stream digest、production environment及不可变报告URI/hash/状态/skipCount/bundleContentSha256/aclDigest/environmentDigest，计算readySetDigest并按主机release policy签名。`stage_gate_evidence`只接受bundle已STAGED、generation/content hash/mesh匹配、ACL从该bundle确定性重算相等、required test/READY slot集合精确相等的record；`CANDIDATE_TEST`必须全slot为CANDIDATE且rollout字段显式null，`PRODUCTION_ACTIVATION`必须全slot为PRODUCTION_GATED并与当前维护rollout一致。以`generation:evidenceSha256`不可变写入State并审计。同digest幂等，异内容必须生成新evidenceId/hash，禁止覆盖。管理面必须使用§5两个不同入口：candidate evidence可在rollout前stage且其SHA必须由PREPARE显式选择；production evidence只能走带`generation/rolloutLeaseId`的rollout入口，且State rollout state已经`MAINTENANCE`、trafficGate=CLOSED、lease/fence仍current。PREPARE只携带已存在的candidateEvidenceSha256；prepare/enter-maintenance均不得携带或预留未来productionEvidenceSha256。

### 4.5 Activate

激活必须满足：

1. bundle 仍在时间窗内，签名和 hash 未变化。
2. `previousGeneration` 仍等于 active pointer。
3. 交付剖面所需组件类型至少一个与当前rollout绑定的`PRODUCTION_GATED`健康实例 READY；State、Gateway/Core和Config Controller自身为必选。
4. 影响Agent的Peer/Runtime、Card publisher、Artifact Store等按bundle dependency matrix在生产维护域取得`PRODUCTION_GATED` READY；隔离candidate READY不能替代。
5. 无 NACK、无缺失 secretRef、无未处理迁移冲突。
6. activate request显式携带已stage且`evidencePurpose=PRODUCTION_ACTIVATION`、包含production gated READY的evidenceSha256与deployedAclDigest；record签名/expiry有效，bundleContentSha256/aclDigest与当前候选及生产维护窗口内已reload bytes逐字节重算一致，readySetDigest与record内readyAttestations重算一致，每份所选State签名receipt的exact bytes/digest/signer/TTL仍有效、`readinessPlane=PRODUCTION_GATED`、rolloutLeaseId/fence/deployed ACL/stream/environment全部与当前维护域相等且对应current component无NACK，required reports逐份hash复验且全部PASS/0 skip。
7. 启用NATS Binding时，activate request还必须携带deployedStreamConfigDigest；State在同一CAS读取DATA-STREAM-CONFIG-001，要求confirmedGeneration等于候选generation、state=CONFIRMED且desired/observed/deployed三者digest均等于bundle的taskEventStreamConfigDigest。CREATE/UPDATE success但没有fresh INFO、旧rollout fence或FAILED_CLOSED均拒绝；production gated READY缺失、过期、错误environment/deployed digest或只存在candidate READY时同样零写入拒绝。

Controller先把generation前缀的不可变运行时索引完整物化并校验，再以单个Redis CAS写active pointer（含generation/contentSha256/evidenceSha256/aclDigest/streamConfigDigest/readySetDigest）、activation audit和outbox；不在一个脚本中临时重写全部大索引。CAS前任一READY过期、报告字节变化、ACL重渲染变化或confirmed stream config漂移都必须拒绝并重建evidence/stream operation。各服务观察active event后只对**新请求/新副作用**使用该generation；不能组合`generation 41`的Credential与`generation 42`的grant。

滚动窗口内旧实例只可完成已在旧 generation 下开始且 Task 已固化策略快照的安全操作，并仍须按当前 generation 检查紧急 Credential/grant revocation；旧 generation 不接受新 Task、新 effect、新 Artifact upload 或 Card publish。达到 activation deadline 仍未切换的实例从路由/presence READY 集合摘除。

### 4.6 Supersede、rollback 与 revocation

- 新 generation 激活后，旧 generation 标记 `SUPERSEDED`，保留至少 24 小时只读回滚窗口；历史 Task 继续记录创建时 generation。
- rollback生成更高generation，内容可引用旧bundle的hash，但需重新校验当前secretRef、时间、组件READY并生成绑定新generation/contentSha256/aclDigest的全新GateEvidenceRecord；旧evidence不得复用。
- Credential/grant 紧急撤销必须使用签名的更高 generation 或签名 emergency revocation bundle；不允许手工改 Redis hash。
- 撤销立即阻止新请求和新副作用，但不改写历史 Task owner；正在执行任务按风险策略继续、取消或进入对账。
- 已泄漏根签名密钥属于 P0，停止新激活并切换预置紧急信任根；不能通过被泄漏密钥自签“修复”。

---

## 5. API 与 CLI

运维入口只绑定内网/受控管理网络，使用独立机器 Credential 和 capability；V1 不要求 Web UI。

| 方法 | 路径 | capability |
|---|---|---|
| `POST` | `/ops/v1/config-bundles/validations` | `ops.config.validate` |
| `POST` | `/ops/v1/config-bundles/{bundleId}/stages` | `ops.config.stage` |
| `POST` | `/ops/v1/config-bundles/{bundleId}/candidate-gate-evidence-stages` | `ops.config.evidence.stage` |
| `POST` | `/ops/v1/config-bundles/{bundleId}/rollouts` | `ops.config.activate` |
| `POST` | `/ops/v1/config-rollouts/{generation}/{rolloutLeaseId}/renewals` | `ops.config.activate` |
| `POST` | `/ops/v1/config-rollouts/{generation}/{rolloutLeaseId}/maintenance-entries` | `ops.config.activate` |
| `POST` | `/ops/v1/config-rollouts/{generation}/{rolloutLeaseId}/production-gate-evidence-stages` | `ops.config.evidence.stage` |
| `POST` | `/ops/v1/config-rollouts/{generation}/{rolloutLeaseId}/activations` | `ops.config.activate` |
| `POST` | `/ops/v1/config-rollouts/{generation}/{rolloutLeaseId}/finishes` | `ops.config.activate` |
| `POST` | `/ops/v1/config-rollouts/{generation}/{rolloutLeaseId}/recoveries` | `ops.config.recover` |
| `POST` | `/ops/v1/config-bundles/{generation}/rollbacks` | `ops.config.rollback` |
| `POST` | `/ops/v1/config-bundles/{generation}/revocations` | `ops.config.revoke` |
| `GET` | `/ops/v1/config-bundles` | `ops.config.read` |
| `GET` | `/ops/v1/config-status` | `ops.config.read` |

每个mutating请求必须携带非空`Idempotency-Key`和reasonCode；path中的bundleId/generation/rolloutLeaseId不得在body重复或覆盖。Controller计算`httpRequestDigest=SHA-256(RFC8785({method,pathTemplate,pathParams,body,idempotencyKeyHash}))`，幂等scope固定为`machinePrincipalHash+method+normalizedPath+Idempotency-Key`；同scope同digest逐字节返回首次HTTP result，同scope异digest 409。各入口**只执行一个阶段**，不得在同一调用中等待未来证据或自动串联后续阶段：

1. candidate evidence-stage body恰为`evidenceRecordJws,expectedGeneration,expectedBundleContentSha256,expectedAclDigest,reasonCode`，只接受`CANDIDATE_TEST`。
2. rollout create body恰为`candidateEvidenceSha256,expectedActiveGeneration,candidateAclDigest,candidateStreamConfigDigest,ownerInstanceId,leaseDurationMs,reasonCode`，只映射`PREPARE_ROLLOUT`并返回持久`rolloutLeaseId/fence/revision/state`；candidate SHA必须已stage且有效，body禁止productionEvidenceSha256。
3. renewal body恰为`expectedRevision,ownerInstanceId,leaseDurationMs,reasonCode`，只映射`RENEW_ROLLOUT`。
4. maintenance-entry body恰为`expectedRevision,ownerInstanceId,drainEvidenceDigest,reasonCode`，只映射`ENTER_MAINTENANCE`；成功后Controller才可reload生产ACL、reconcile stream并收集PRODUCTION_GATED READY。
5. production evidence-stage body恰为`evidenceRecordJws,expectedRevision,expectedBundleContentSha256,expectedAclDigest,expectedDeployedStreamConfigDigest,reasonCode`；Controller从State读取path rollout的current lease/fence与实际deployed digests，验签后只把canonical record交给`stage_gate_evidence`。只接受`PRODUCTION_ACTIVATION`且record内全部READY绑定该path rollout。
6. activation body恰为`evidenceSha256,expectedRevision,expectedActiveGeneration,reasonCode`；此时evidence必须已由第5步持久存在。Controller不得信任caller自报deployed digest/fence，而从current rollout ledger和部署read-back取得它们后只映射一次`ACTIVATE`。禁止选择latest、candidate evidence或服务端猜测未来SHA。
7. finish body恰为`expectedRevision,ownerInstanceId,healthEvidenceUri,healthEvidenceDigest,reasonCode`，只映射`FINISH_ROLLOUT`。

任一步CAS前失败由调用者显式发recovery；recovery body恰为`rolloutOperationId,expectedState,expectedRevision,desiredOutcome,recoveryEvidenceUri,recoveryEvidenceDigest`，desiredOutcome只允许`RESTORE_OLD|FINISH_CANDIDATE|KEEP_FAILED_CLOSED`。Controller先TAKEOVER，再按State重读active pointer唯一选择RESTORE/FINISH/MARK；commit-before-reply由各State operation ledger和HTTP幂等ledger逐字节恢复。进程重启只凭generation/rolloutLeaseId读取持久state继续，不能依赖内存中的长事务。

```text
a2amesh ops config validate <bundle>
a2amesh ops config stage <bundle-id>
a2amesh ops config evidence stage-candidate <bundle-id> --record <gate-evidence.jws.json> --expect-generation <n>
a2amesh ops config rollout prepare <bundle-id> --candidate-evidence-sha256 <sha256> --expect-active-generation <n> --candidate-acl-digest <sha256> --candidate-stream-digest <sha256>
a2amesh ops config rollout renew <generation> <rollout-lease-id> --expect-revision <n>
a2amesh ops config rollout enter-maintenance <generation> <rollout-lease-id> --expect-revision <n> --drain-evidence-digest <sha256>
a2amesh ops config evidence stage-production <generation> <rollout-lease-id> --record <gate-evidence.jws.json> --expect-revision <n>
a2amesh ops config rollout activate <generation> <rollout-lease-id> --evidence-sha256 <sha256> --expect-revision <n> --expect-active-generation <n>
a2amesh ops config rollout finish <generation> <rollout-lease-id> --expect-revision <n> --health-evidence <immutable-uri>
a2amesh ops config rollout recover <generation> <rollout-lease-id> --operation-id <id> --expect-state <state> --expect-revision <n> --desired-outcome <RESTORE_OLD|FINISH_CANDIDATE|KEEP_FAILED_CLOSED> --evidence <immutable-uri>
a2amesh ops config rollback <generation> --reason <code>
a2amesh ops config revoke <generation> --reason <code>
a2amesh ops config status
```

CLI 不直接写 Redis、配置目录或组件本地缓存。

---

## 6. Card publisher ownership

bundle 为每个 `agentId` 声明一个或多个稳定 `publisherPrincipal/nodeId` 候选；进程临时 instanceId 由已认证 NKey 会话绑定。active publisher 由 Redis lease/fencing 选择：

```text
stable publisher Principal READY for active generation
→ acquire card-publisher:<agentId> lease
→ obtain monotonically increasing fencing token
→ build/validate Card from the same config generation
→ upsert_card(agentId, publisherPrincipal, nodeId, instanceId, cardGeneration, configGeneration, fencingToken)
```

规则：

1. State 只接受 active generation、允许候选、有效 lease 和最新 fencing token。
2. lease 默认 15 秒，5 秒续约；失联后必须等 lease 过期，新 publisher 才能取得更高 fencing token。
3. 旧 publisher 恢复后只能更新 presence，不能覆盖 Card。
4. Card generation 与 config generation 分开单调递增，但每次 Card 记录必须保存来源 config generation/content hash。
5. publisher failover 不自动扩大 delivery profile；新实例只能发布 active generation 已允许且门禁已通过的能力。
6. 所有候选不可用时保留最后一个已验证 Card 快照并标记发布能力 degraded；不能由任意 Peer 接管。

---

## 7. 启动、缓存和失效行为

### 7.1 Genesis bootstrap

首次部署没有 active generation 时采用离线批准的 genesis 流程：主机镜像固定 deploymentId、meshId、nodeId、根公钥、Config Controller/State 的稳定 NKey、bootstrap 地址、`genesisWormOrigin`和随机一次性 genesis nonce。运维提交 `generation=1,previousGeneration=null,genesis=true,deploymentId,genesisNonce` 的双人批准签名 bundle。跨 Redis/主机/WORM 不宣称事务；唯一不可逆提交点是外部 WORM 的 `GenesisCommitReceiptV1`。

Saga 状态固定 `ABSENT→PREPARED→COMMITTED`，以 `deploymentId+nonceDigest` 为唯一键：

`genesisWormOrigin`必须是仅含scheme/authority的canonical HTTPS origin：scheme恰为小写`https`，无userinfo/path/query/fragment，DNS host为IDNA2008 A-label小写，默认443不得显式写端口，非默认端口用无前导零十进制；解析后重新序列化不逐字节相等即拒绝。`genesisNonce`恰为32随机字节的base64url无paddingcanonical编码，`nonceDigest=lowerhex(SHA-256(decoded genesisNonce bytes))`且恰64个小写hex字符。`segment(x)=base64url_no_pad(UTF8(NFC(x)))`，输入必须非空且已经是NFC；URI禁止percent-encoding、`.`/`..`、重复斜线、尾斜线、query和fragment。两个唯一对象URI恰为：

```text
intentUri = genesisWormOrigin + "/a2amesh-genesis/v1/deployments/" + segment(deploymentId) + "/nonces/" + nonceDigest + "/intent.jws"
commitUri = genesisWormOrigin + "/a2amesh-genesis/v1/deployments/" + segment(deploymentId) + "/nonces/" + nonceDigest + "/commit.jws"
```

两者只依赖主机bootstrap事实；不把intent/commit digest放入locator。WORM必须提供对exact URI的authenticated create-if-absent、强read-after-write和永久禁止overwrite/delete：首次201/等价created成功；已存在且exact bytes相同按幂等成功；已存在异bytes为P0冲突；网络未知结果必须GET exact URI并比对bytes/digest，禁止换备用路径重写。`createdAt/committedAt`只接受UTC RFC3339恰3位毫秒和大写`Z`（正则`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$`），拒绝offset、更多/更少小数、leap second和等价替代拼写。所有JWS base64url段都必须无padding且decode→re-encode逐字节相等；所有SHA-256文本均为64位小写hex。

1. **prepare**：在空State/无accepted generation下验证g1 bundle后，构造`GenesisIntentPayloadV1`，payload恰含`schemaVersion,deploymentId,meshId,nodeId,nonceDigest,bundleId,contentSha256,createdAt,producerPrincipal`且不含digest/signature。Intent恰由主机bootstrap固定Config Controller signer签1次，`kid`必须映射producerPrincipal；protected header恰含`alg=EdDSA,kid,typ=a2amesh-genesis-intent+jws,schemaVersion=1`。wire只允许non-detached JWS General JSON：`protected=base64url(RFC8785(header))`、`payload=base64url(RFC8785(payloadObject))`、签名输入为ASCII(`protected.payload`)，signature entry恰含`protected,signature`，顶层恰含`payload,signatures[]`，禁止unprotected header；最终envelope按RFC8785序列化，`intentJwsDigest=SHA-256(exact envelope bytes)`。先对上述exact `intentUri`执行WORM create-if-absent写immutable `GenesisIntentV1` envelope；再用temp+fsync+atomic rename+parent-dir fsync写主机PREPARED marker（Windows用FlushFileBuffers+ReplaceFile/ACL等价），最后CAS Redis PREPARED。三者都绑定同一intentUri/intentJwsDigest；同digest重试幂等，任一已有异digest立即冲突/P0；
2. **commit/线性化**：仅当WORM intent、主机PREPARED、Redis PREPARED三者URI/digest一致时，由bootstrap固定State signer构造`GenesisCommitReceiptPayloadV1`，payload恰含`schemaVersion,deploymentId,meshId,nodeId,nonceDigest,bundleId,contentSha256,intentUri,intentJwsDigest,committedAt,result`且`result=ACCEPTED`。protected header恰含`alg=EdDSA,kid,typ=a2amesh-genesis-commit+jws,schemaVersion=1`；恰1个State signer签名，并使用上一项相同的General JSON/non-detached/base64url/RFC8785构造和禁止unprotected header规则。`GenesisCommitReceiptV1` envelope首次create-if-absent写入上述exact `commitUri`成功即为genesis accepted唯一线性化点，`commitReceiptDigest=SHA-256(exact envelope bytes)`；receipt payload的intentUri必须逐字节等于同bootstrap公式计算值；
3. **materialize**：commit 后将主机 marker 原子升级 COMMITTED并 fsync，再 CAS Redis COMMITTED，均保存 commitReceipt URI/digest；这两步只是投影，不改变 accepted 事实。

任意crash后，`recover_genesis`必须仅从bootstrap的genesisWormOrigin/deploymentId/genesisNonce重算nonceDigest、intentUri和commitUri，不依赖Redis、本机marker、对象listing或bundle文件；先GET exact commitUri：存在则验证receipt exact bytes/signature/time/result及其intentUri，再GET/验证exact intentUri并补齐主机/Redis COMMITTED；commit为404才GET exact intentUri，其他HTTP/认证/超时结果一律UNKNOWN/fail closed，不得当作不存在。只有PREPARED碎片时，仅同node attestation+同intentJwsDigest可补齐并继续，异digest/新主机需离线灾备；CommitReceipt已存在时只能从其补齐主机/Redis COMMITTED，不能创建第二receipt；主机/Redis声称COMMITTED但WORM receipt缺失或验签失败时P0/fail closed。nonce一旦出现intent即永久烧毁，不允许用不同bundle复用。数据库清空/主机重装后必须从WORM/Recovery Manifest重建或由离线灾备重新授权，不能因“空库”重放旧g1。只有WORM commit、两份COMMITTED marker和g1 ACTIVE都成立后Gateway、Runtime、Artifact、dispatch/effect才开放；此前仅开放validate/stage/genesis-recover/READY/activate/health。

### 7.2 正常启动与失效

- 组件启动必须从受保护本地缓存读取最后一个 active bundle，重新验证签名、meshId、hash、expiry 和 active pointer。
- 能访问 Controller/State 时，必须确认本地 generation 未被撤销；缓存与 active pointer 不一致时不接受新业务。
- Controller 暂时不可用但最后 active bundle 未过期、未撤销且 State 可确认 pointer 时，可继续既有 generation；记录 degraded 告警。
- bundle 过期、签名无效、meshId 不匹配、secretRef 缺失或 generation 未知时 fail closed：Gateway/Core 停止新任务和新副作用，只保留最小健康、只读查询和运维修复入口。
- 不得回退读取未签名 `.env`/YAML 来恢复业务；bootstrap 连接地址和根公钥不属于业务 bundle，但也必须受主机权限保护。
- READY 有 TTL；实例失联后 Controller 不把旧 READY 当作下一次激活证据。

---

## 8. 审计与保留

append-only WORM 必须使用《统计、审计与运行监控规则》的 canonical `AuditEnvelopeV1`，不得另造 `auditId/timestamp/actorPrincipal` 顶层 casing；Config 领域只在 `payload` 写 `bundleId,generation,contentSha256,previousGeneration,idempotencyKey,reasonCode`，action/result/requestId/traceId/actorPrincipal 使用公共顶层字段。Redis 热索引/导出只存 deployment-keyed `actorPrincipalPseudonym+pseudonymKeyVersion`；签名原文、secret 值和 Credential 不进入普通日志。bundle制品、签名、validate报告、内嵌所选READY exact JWS的GateEvidence、未选READY/NACK摘要、激活/回滚/撤销记录至少保留365天并进入加密异机备份；任何归档/compaction不得从GateEvidence删除或只保留attestation digest。

任何 active pointer 修复都必须通过受控恢复命令生成审计，禁止 Redis CLI 手改。灾难恢复后先验证 Recovery Manifest 的 trust-root version、全部 bundle hash 和 active generation，再恢复 pointer，最后允许组件 READY。审计进入独立 append-only sink；Redis Stream 仅作热缓冲，不是 365 天唯一权威。

---

## 9. 失败矩阵

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

## 10. 可观测与告警

配置域必须提供 bundle 状态/激活耗时、active generation、签名/expiry、组件 READY/NACK、generation mismatch、撤销传播和 publisher lease/fencing 信号。精确指标名、标签和阈值以《统计、审计与运行监控规则》为准：签名/expiry/generation/READY 故障映射 `OBS-ALERT-026`，publisher split brain/旧 fencing/无候选映射 `OBS-ALERT-027`。Principal、bundleId 和 content hash 不作为 metric label。

---

## 11. 验收标准

- **TEST-CONFIG-SIGN-001**：canonical hash、允许算法、签名链、meshId、时间窗和 schema 失败均拒绝。
- **TEST-CONFIG-CAS-001**：并发 stage/activate、同 generation 异内容、previousGeneration 漂移均返回确定冲突。
- **TEST-CONFIG-ATOMIC-001**：Credential/Alias/Grant/Profile/Policy 不出现跨 generation 混用。
- **TEST-CONFIG-READY-001**：必选组件 READY/NACK/TTL 和 delivery profile 广告门禁正确。
- **TEST-CONFIG-ROLLBACK-001**：rollback 使用更高 generation，旧内容重新校验且全程可审计。
- **TEST-CONFIG-REVOKE-001**：Credential/grant 撤销阻止新请求，不改写历史 Task owner，传播时限可测。
- **TEST-CARD-OWNER-001**：多候选、lease 过期、网络分区和旧实例恢复时只有最新 fencing publisher 可更新 Card。
- **TEST-CONFIG-STARTUP-001**：Controller outage、过期、撤销、坏缓存和 secretRef 缺失均按规则 fail closed。
- **TEST-CONFIG-DR-001**：信任根、bundle、active pointer 和审计可从异机备份一致恢复。
- **TEST-CONFIG-HASH-001**：bundle与GateEvidenceRecord各自的自字段排除、RFC8785、JWS General JSON、typ、双签轮换fixture字节级一致；`signatures[]`按解码protected `kid` UTF-8严格升序、kid唯一且entry恰含`protected,signature`，逐项拒绝重复kid、反序kid、缺kid、额外字段、unprotected header、threshold不足和同语义不同exact envelope；bundle出现gateEvidenceRefs/report hash必须拒绝。
- **TEST-CONFIG-GENESIS-001**：URI fixture固定`deploymentId=deploy-fixture-001`、`genesisWormOrigin=https://worm.example.test`、`genesisNonce=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8`、`nonceDigest=630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd`、`intentUri=https://worm.example.test/a2amesh-genesis/v1/deployments/ZGVwbG95LWZpeHR1cmUtMDAx/nonces/630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd/intent.jws`、`commitUri=https://worm.example.test/a2amesh-genesis/v1/deployments/ZGVwbG95LWZpeHR1cmUtMDAx/nonces/630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd/commit.jws`；固定GenesisIntent/CommitReceipt payload、protected header、signer、base64url、General JSON和exact envelope bytes；逐项拒绝Intent/Receipt字段缺失或额外、错误typ/schemaVersion/kid、unprotected header、错误producer signer、`intentDigest`旧字段及intentUri/intentJwsDigest漂移。分别在WORM intent、主机PREPARED fsync/rename、Redis PREPARED、WORM commit、主机COMMITTED、Redis COMMITTED每一步前后杀进程并组合丢Redis/主机。相同digest必须幂等恢复到唯一commit，不同digest/nonce重放/第二genesis/伪造COMMITTED/同receipt异内容全部拒绝；WORM commit前不得宣布accepted，commit后marker未补齐或g1未ACTIVE时业务不得早启。另逐项拒绝HTTP/大写scheme或host、显式`:443`、userinfo/path origin、percent-encoded或带padding segment、digest大写、备用路径、query/fragment，以及非UTC三位毫秒Z时间；清空Redis与主机marker/bundle后只凭bootstrap+两个exact WORM URI必须重建COMMITTED投影。
- **TEST-CONFIG-READY-AUTH-001**：伪造instance、过期/重放READY、非允许Principal、错误`readinessPlane`/rollout lease/fence/deployed digest/environment和content hash漂移均拒绝；有效组件AuthProof必须由State签发唯一`ComponentReadyAttestationReceiptV1` exact JWS，同request重试逐字节返回同一receipt。逐项篡改receipt payload/signature、GateEvidence内嵌JWS string、只保留authProofDigest/digest而删除receipt bytes、attestationJwsDigest不符、observedAt/expiresAt非UTC三位毫秒Z或毫秒转换不一致均拒绝stage/activate。stage后分别删除热receipt key、推进其TTL过期、清空并从备份重建Redis：历史GateEvidence都必须仅凭内嵌exact JWS重新验签；已过期receipt仍不得用于新的activate/current READY，恢复后必须取得新receipt。NATS generation必须按同一signed bundle重算`RequiredSlotSetV1`并为每个stable slot取得CANDIDATE READY，再在production gated维护域为逐字节相同的stable slot set取得`PRODUCTION_GATED` READY；缺失角色、任意两角色共用NKey、组件无法调用自身config.ready、Peer获Task/Plan/input/effect、Core获Plan/effect、Supervisor获Plan、Orchestrator获Task/effect、Relay获outbox.recover、Ops Recovery获普通Relay/其他State权限、错误stream/ACL/session参数或production组件在READY前承接业务流量均必须NACK。 另从同一fixture按`fixedRecoveryBase ∪ requiredForProfiles ∪ explicitlyRequiredOperationalSlots`重算`RequiredSlotSetV1`，逐项注入missing/extra/duplicate slot、共享NKey、未知explicit slot、基础slot缺readyReporterPrincipal/expectedDigest、Core内嵌Merge Broker被错误展开、candidate-only receipt、production plane/rollout/deployed/environment漂移；任一情况必须在stage/activate前REJECTED且零active-pointer写入。
- **TEST-CONFIG-GATE-EVIDENCE-001**：先stage不可变bundle并确定aclDigest，再生成绑定二者的`TEST-NATS-ACL-001`/`TEST-NATS-STREAM-SESSION-001`报告。`CANDIDATE_TEST`只能通过bundle-scoped candidate endpoint/CLI stage；PREPARE必须显式选择已stage candidateEvidenceSha256，随后maintenance成功并取得production READY，`PRODUCTION_ACTIVATION`才可通过generation+rolloutLeaseId-scoped production endpoint stage，最终activation只接收已持久存在的显式productionEvidenceSha256。断言PREPARE缺/错/candidate过期SHA拒绝，prepare/renew/maintenance body均禁止production evidence字段；production stage前调用activation、candidate SHA调用ACTIVATE、latest/猜测SHA、错误rollout path、旧revision/fence全部拒绝。API/CLI不直写State；每个normalized path相同Idempotency-Key+body逐字节返回原HTTP/State result，同key异body 409，无对应capability拒绝。bundle不含报告引用；报告不含evidence digest；依赖图无环。报告missing/duplicate/extra-required替代、skip/fail、URI字节hash漂移、bundle/ACL/environment digest不符、所选READY过期/current NACK/evidence签名或expiry坏、同generation异record覆盖、rollback复用旧evidence均拒绝；所选attestation未过期时正常READY renew不得改变readySetDigest，过期后必须选择新attestations并stage新evidence hash。
- **TEST-CONFIG-ROLLOUT-001**：从STAGED exact bytes启动隔离candidate broker并产出绑定acl/environment digest的报告，确认生产broker仍为active ACL。管理API逐次调用并断言状态顺序固定为`rollout create(PREPARE)→renew*→maintenance-entry→production-evidence-stage→activation→finish`，每个response持久返回rolloutLeaseId/revision且进程清空内存后可只凭path handle恢复；禁止单一activation endpoint内部串联、在prepare前要求未来evidence或跳阶段。随后依次在`-t`前后、生产reload/deployed digest重读、production evidence stage、ACTIVATE CAS、FINISH/listener/Gateway重开前后杀Controller；断言流量门未在错误路径打开。reload/摘要/CAS失败必须在同一维护窗显式recover并恢复exact active ACL，恢复失败保持FAILED_CLOSED；candidate/active字节混用、错误deployedAclDigest/deployedStreamConfigDigest、过期/他人lease均拒绝。对8个RolloutControl variant分别在operation ledger/CAS前后及State commit-before-reply杀Controller：相同scope/key/digest必须逐字节重放且revision/audit/outbox不增加，同key异digest零写入。分别令PREPARING、MAINTENANCE、ACTIVATED、FAILED_CLOSED的逻辑lease过期；旧owner的RENEW/阶段写和旧fence全部永久拒绝，普通Config NKey无`ops.config.recover`也拒绝TAKEOVER。具双凭据的新实例TAKEOVER必须递增fence/revision且门保持CLOSED：active pointer仍为old时只可重验old ACL/stream后RESTORE；已为candidate时只可重验candidate ACL/stream/health后FINISH；pointer/digest未知时只能KEEP_FAILED_CLOSED。模拟rollout key缺失必须fail closed。365天后只对COMPLETED/RESTORED做terminal compaction，tombstone的operationReplayJson必须让任一历史阶段同scope/key/digest逐字节返回各自原result、异请求冲突且不得重建lease、修改active pointer或开流量；FAILED_CLOSED不得compaction。成功路径只在candidate ACL/stream已部署且digest匹配后，以同一rollout lease/fence在生产维护域启动exact generation的GATED_PASSIVE组件；每个required slot必须提交`PRODUCTION_GATED` READY并绑定production environment/deployed ACL/stream，随后生成并独立stage新的production-bound evidence。任一生产组件未READY、candidate-only receipt、错误plane/lease/fence/environment/deployed digest时ACTIVATE零写入。只有已stage evidence有效才CAS active；CAS后组件已运行，独立FINISH只切换业务listener/State gate，最后开外部Gateway。新请求只用新generation，旧实例仅完成受控in-flight，任一时刻不得由staged与active组件同时承接生产请求，紧急撤销仍即时生效。

---

## 12. G0 配置冻结合同

1. content hash 排除自身/signature 后 RFC8785；JWS General JSON Serialization 与 protected header 固定。
2. genesis 是空 State 下的一次性、离线批准、crash-safe saga；WORM GenesisCommitReceipt 是唯一提交点，Redis/主机 marker 为可恢复投影，不与正常 READY 形成循环依赖。
3. READY/NACK 必须由稳定 component Principal 签名，绑定 nodeId/临时 instanceId，并进入 replay 防护。
4. Card publisher 以稳定 publisherPrincipal/nodeId 配置；临时 instance UUID 不能成为预签候选。
5. generation 前缀索引先完整 stage，再 CAS pointer；不依赖 Redis Lua 失败 rollback。
6. 滚动窗口不等于双 active：新操作只用当前 active，旧代仅完成固化的 in-flight 并服从当前 revocation。
7. 根密钥轮换使用主机固定策略下的双签/阈值，bundle 不能自举替换信任根。
8. `components[]`是CANDIDATE与PRODUCTION_GATED READY的唯一来源；State先验证组件AuthProof、plane、rollout/deployed/environment约束，再持久化包含Principal/node/hash/request/AuthProof digest的exact签名`ComponentReadyAttestationReceiptV1`；GateEvidence/activate重验receipt，不用digest替代签名材料；production gated READY必须先于active CAS，activate只切active root pointer/audit/outbox，FINISH才切业务listener。
9. bundle只声明requiredGateTestIds；测试报告绑定已stage的bundleContentSha256+aclDigest；GateEvidenceRecord在报告后独立签名。activate显式绑定evidenceSha256，禁止任何摘要自引用或旧evidence复用。

---

## 13. 参考依据

- [A2AMesh V1 设计文档索引](README.md)
- [业务与总体架构设计 V1.6](A2AMesh_业务与总体架构设计_V1.6.md)
- [AgentCard与协议对象规范 V1.6](A2AMesh_AgentCard与协议对象规范_V1.6.md)
- [Redis状态平面与数据设计 V1.6](A2AMesh_Redis状态平面与数据设计_V1.6.md)
- [编排器 Runtime与工具适配设计 V1.6](A2AMesh_编排器_Runtime与工具适配设计_V1.6.md)
- [Artifact与对象存储设计 V1.2](A2AMesh_Artifact与对象存储设计_V1.2.md)
- [统计审计与运行监控规则 V1.6](A2AMesh_统计审计与运行监控规则_V1.6.md)
- [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)
- [RFC 7515 JSON Web Signature](https://www.rfc-editor.org/rfc/rfc7515)
