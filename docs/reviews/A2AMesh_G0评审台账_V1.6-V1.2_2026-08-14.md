# A2AMesh G0 评审台账（V1.6/V1.2）

> 文档 ID：`A2AM-G0-REVIEW-20260814`
> 状态：第一至第五轮FAIL；第六轮调度无效；第七轮FAIL；R9-F/第八轮在commit `8ed1ebb...`、tree `315c49f...`上A/B/C均FAIL（P0=0、P1=12、P2=3）；R10修订已形成分模块本地checkpoint但尚未完成全量门禁和同tree三路复审，尚未批准
> 评审日期：2026-08-14
> 初始代码基线：`d0ba51d2752feb754f6425e8f775361f3a764547`；各轮当前候选以第4节所列commit/tree为准
> 设计范围：`docs/specs` 的 8 份 V1.6、3 份 V1.2 专项、实施计划、综合分析、索引及 V1.6 架构图
> 内容清单：关闭复审后生成；本台账不得自我批准

## 1. 判定规则

G0 只判定设计合同是否唯一、可实现、可测试，不判定代码已经实现或产品已兼容/可生产。正式批准必须同时满足：

1. G0-01～G0-15 均有且仅有一个主权威、支持文档和完整 TEST ID；
2. 三路隔离关闭复审均为 PASS，且不存在未关闭 P0/P1；
3. 当前设计 payload 由 SHA-256 content manifest 绑定；
4. 本地/外部链接、Markdown 结构、表格、版本、术语、SVG/HTML 同源和视觉检查通过；
5. `compileall`、pytest、Ruff 和 `git diff --check` 通过；
6. 台账批准状态、文档元数据和索引状态在同一最终提交中一致。

## 2. 首轮问题关闭映射

| 关闭项 | 首轮问题 | 候选关闭合同 | 主证据 | 状态 |
|---|---|---|---|---|
| CL-A01 | Task 返回后 dispatch 可能丢失 | immutable `DispatchTask`、`DATA-DISPATCH-001`、durable intent/deadline | Redis §5.16/§6.14；NATS §6.1.1/§16.2 | 待复审 |
| CL-A02 | admission、accept/start 与 ownerless cancel 无唯一线性化点 | 持久 DRR；`accept_dispatch_and_start`；ownerless cancel CAS | Redis §5.17、§6.5、§6.16 | 待复审 |
| CL-A03 | Event Outbox 可越序或 claim 后丢失 | per-Task head/watermark、claim lease、PubAck、DEAD fail-closed | Redis §5.9/§6.15；NATS §16.3 | 待复审 |
| CL-A04 | effect intent/attempt 混合导致重复副作用 | 独立 intent/attempt、PREPARED/APPLYING、证据门禁与 reconciliation | Redis §5.10/§6.7；Task §13；Runtime §8.4 | 待复审 |
| CL-A05 | Plan、Auth replay、SSE race 和版本协商不闭合 | `DATA-PLAN-001`、`DATA-AUTH-REPLAY-001`、consumer-first SSE、`BindingCapabilities` | Redis §5.17/§5.18；API §14；NATS §16 | 待复审 |
| CL-B01 | Runtime containment 只有等级名、不可判定 | OS-specific `ContainmentProfile` 与 launch attestation | Runtime §8.5/§11 | 待复审 |
| CL-B02 | Redis lease 无法 fence 文件系统写入 | private attempt worktree + fenced Merge Broker | Runtime §8.5；Redis §5.18 | 待复审 |
| CL-B03 | 非 State 审计来源与 WORM 链不闭合 | `DATA-AUDIT-001`、fsync WAL/Audit Ingress、receipt、sealed segment、跨日链 | Redis §5.19/§6.21；监控 §11.1 | 待复审 |
| CL-B04 | Redis 全损恢复依赖自证或仅有“相近时间”备份 | signed `DATA-RECOVERY-001`、实际 URI/digest/watermark、delete journal、双人 release | Redis §5.20/§6.22 | 待复审 |
| CL-B05 | Config hash/JWS/genesis/READY 身份不完整 | exact JWS wire、signed deployment/genesis、一次性 marker、正式 `components[]` | Config §3/§4/§7.1；Redis §5.14/§6.12 | 待复审 |
| CL-C01 | Artifact 墓碑可能变成授权，hold/引用竞态不完整 | tombstone 不授权、`ArtifactHold`、反向引用和 finalize/delete CAS | Artifact §4～§8；Redis §5.13/§6.11 | 待复审 |
| CL-C02 | reconciliation resolution 可变、缺 close/reopen 完整矩阵 | immutable `ResolutionRecord`、ordered history、close endpoint 和正交状态矩阵 | 对账 §3/§6/§7；Redis §5.15/§6.13 | 待复审 |
| CL-C03 | G0、profile、阶段和 current-vs-target 声明冲突 | 单主权威矩阵、完整 TEST ID、累积 profile、绑定代码 SHA 的状态表 | `docs/specs/README.md`；实施计划 §2/§14～§17 | 待复审 |
| CL-C04 | 活动外链可变、归档断链、README 不可执行 | v1.0.1 immutable links、精确 archive manifest/兼容指针、uv/NKey quick start | 根 README；`docs/archive/*/README.md` | 待复审 |
| CL-C05 | 架构图误示标准 NATS transport、暴露/信任域和流程不清 | custom Binding、公开入口/私有服务/敏感存储域、①～⑦和 fit-to-width | `docs/assets/A2AMesh_V1.6_Architecture.svg/.html` | 待复审 |

## 3. 已执行门禁

| 门禁 | 实际结果 | 状态 |
|---|---|---|
| 活动 Markdown 结构 | 14 文件；每份一个 H1；围栏闭合 | PASS（候选快照） |
| 本地链接 | R10-C4治理工作树共87份tracked Markdown；markdown-it-py 4.2.0/CommonMark递归`inline.children`口径计本地`link_open href` 670、image src 4、总计674；外链/纯fragment不计；破链0；7个archive manifest | PASS（R10-C4重算） |
| 外部链接 | R10-G3.1从14份活动规范提取15个唯一Markdown href；逐链真实GET均为2xx/3xx，RFC 9728经IPv4/退避后200并跳转`/info/rfc9728/` | PASS（R10-G3.1） |
| G0/ID | G0-01～15；27个G0 TEST ID、10个DATA ID均有专项定义；R10目标88个唯一State request literal及精确caller coverage | PASS（R10-C4重算） |
| Markdown 表格 | 固定范围`docs/specs/*.md`14份+根README+本台账；markdown-it-py table parser重算123+2+9=134组表格 | PASS（R10-C4重算） |
| 版本/术语 | 规范性 A2A 声明/链接均为 v1.0.1；版本历史表保留真实 v1.0 行；活动Markdown URL无`/latest/`或旧 custom Binding文案 | PASS（候选快照） |
| SVG/HTML | R10-G3.2用XML/HTML解析器通过；独立SVG与HTML inline SVG逐字节同源，SHA-256=`b7792dabec665447e4c0089e9392217230615cab33636d66c34336438551cd15`，尺寸1800×1120，16个SVG ID唯一 | PASS（R10-G3.2） |
| 架构图视觉 | R10-G3.3隔离渲染PNG为1800×1120、SHA-256=`6e410fe9902c8b7e85f94c61fb079ffb30180fbcd36c7054dfc9b16aa1d3c466`；三组Runtime→private worktree箭头清晰，State/Object Store→WORM捷径均已移除，Workers/Relays在独立源点经底部专用走廊进入WORM，标注`AUDIT RELAY (WORKERS ONLY) → WORM`；`NO WORM PUBLISH`与`AUDIT ONLY`可读，无裁切/严重遮挡。小字号为非阻断可读性提示 | PASS（R10-G3.3） |
| Python 编译 | 隔离候选快照执行`uv run python -m compileall -q src` | PASS |
| 测试 | R10-G1默认隔离套件：46 passed、6 conditional skipped；R10-G4.2在Docker `nats:2.10.26`（image digest `sha256:736d575e60135ce1d50fc206675d48d0e57dcaa0704f696f0cb4b5f6dadd49d7`）上执行`tests/test_security.py`：6 passed、0 skipped；R10-G4.3另执行State/他人inbox/JS DELETE三项broker permission negative：3/3 PASS | PASS（R10最终tree仍需重跑全套） |
| 静态检查 | 隔离候选快照执行`uv run --with ruff ruff check .` | PASS |
| CLI smoke | 隔离候选快照执行`uv run a2amesh --help`、`uv run mesh --help` | PASS |
| Diff 格式 | `git diff --cached --check` | PASS |

本节是累积门禁证据而非当前批准：设计payload截至R10-C3 clean checkpoint（commit `092fb05ae3f438d64ffb83a3417f6ca1f7891d43`、tree `8752b69a77cd9569676e207b4927e177dc0d36ea`）；本地链接、表格和State subject行在R10-C4治理工作树重算；外链、SVG/HTML与视觉证据在R10-G3工作树重算。compileall/pytest/Ruff/CLI等其余行仍需在R10最终冻结tree上全部重跑，不能由本表沿用为最终证明。

## 4. 独立关闭复审

第一轮复审绑定代码 HEAD `d0ba51d2752feb754f6425e8f775361f3a764547` 和当时工作树候选字节；三路均为 FAIL，因此不得批准。评审后修订不反向改变第一轮结论。

| 复审流 | 范围 | 结论 | 未关闭项 |
|---|---|---|---|
| A | 执行、State、NATS、API | **FAIL（第一轮）** | dispatch/outbox expired claim、DRR reservation、pre-accept cancel、Plan takeover、Auth wire、NATS ACL |
| B | Runtime、安全、Config、Audit、Recovery、架构图 | **FAIL（第一轮）** | Recovery/Audit exact JWS/receipts、完整 TEST、AuditEnvelope、Ops/AS/Runtime trust boundary |
| C | Artifact、Config、Reconciliation、治理 | **FAIL（第一轮）** | Artifact hold/ref、genesis crash saga、G0 引用/TEST、staged bytes、C5 门禁、历史行 |

每一修订轮都必须重新绑定 staged tree；在最终一轮三路均返回 PASS 前，本表不得改写为批准。

### 第二轮结果

第二轮绑定 staged tree `020fba5fb830184e6dec3a62b46fa4420f110b31`；输入为隔离的 Git index snapshot，三路均首尾复算 tree OID，未读取变化中的工作树。

| 复审流 | 结论 | 残余阻断 |
|---|---|---|
| A | **FAIL（第二轮）** | P1-A-01 Plan expired owner 可绕过 recover；P1-A-02 State RPC subject set 漏项/`card.query`；P1-A-03 streaming 与 `allow_responses max=1` 冲突；P1-A-04 JetStream stream 名不一致 |
| B | **FAIL（第二轮）** | P1-B-01 Audit recordsDigest framing 未唯一；P1-B-02 `tenant_rejected`/`tenantRejected` casing 冲突 |
| C | **PASS（第二轮）** | 无 G0 P0/P1；Artifact/Genesis/G0 traceability/staged links/C5/历史/候选治理全部关闭 |

上述六项已在下一候选中定点修订：Plan 初次 acquire/recover 分流；State literal subjects；DATA-STREAM-SESSION-001 + Controller/JS Provisioner；固定 stream `A2AMESH_TASK_EVENTS`；AuditRecordV1 big-endian framing fixtures；tenant payload camelCase。第三轮必须绑定新的 staged tree 并防回归复审；本记录不反向改写第二轮结论。

### 第三轮结果

第三轮绑定 staged tree `00c280a0d6d9838735b24e089369962d9a00f4c9`；三路只读隔离 snapshot并复算tree。A、C仍有G0 P1，B通过；本记录不反向改写该轮结论。

| 复审流 | 结论 | 残余阻断 |
|---|---|---|
| A | **FAIL（第三轮）** | P1-A-R3-01 Plan recovery gate无持久schema；R3-02六个State RPC无发布身份；R3-03 snapshot覆盖事件在max_ack_pending=1下死锁；R3-04 Controller failover与instance-bound delivery冲突；R3-05 final/expired ACK/清理顺序及旧inbox实施指令冲突 |
| B | **PASS（第三轮）** | AuditRecordV1 fixtures/casing/JWS/Recovery防回归均通过，无G0 P0/P1 |
| C | **FAIL（第三轮）** | P1-C-R3-01 Stream Controller/JS Provisioner未闭合到C3文件与门禁、C7物理部署、上线READY及最终CORE发布门禁 |

第三轮六项已在第四轮候选中定点修订：持久`recoveryState/recoveryEpoch/recoveryRevision/cursor`；独立Peer Binding/Task Supervisor/Orchestrator NKey和71项State caller coverage；snapshot-covered broker ACK watermark；稳定mesh-scoped非queue controller delivery；DRAINING_FINAL/EXPIRING及signed consumer INFO清理合同；C3/C7/C8/上线/Config/总体/图完整部署与READY门禁。第四轮必须绑定新staged tree重新复审。

### 第四轮结果

第四轮绑定staged tree `42a8e58dcc58d5c68335aa277d4e75ac58ca9cd6`；三路只读隔离snapshot并独立复算tree。A/B/C均有G0 P1；本记录不反向改写该轮结论。

| 复审流 | 结论 | 残余阻断 |
|---|---|---|
| A | **FAIL（第四轮）** | P1-A-R4-01 Session未持久绑定config generation/consumer config/initial response；P1-A-R4-02 inactive threshold可在session expiry前自动删除consumer |
| B | **FAIL（第四轮）** | P1-B-R4-01 Consumer INFO响应未绑定本次查询，旧签名`exists=false`可重放推进早终态 |
| C | **FAIL（第四轮）** | P1-C-R4-01 bundle内gate evidence报告引用与测试后生成顺序形成摘要自引用环 |

第四轮四项已在第五轮候选中定点修订：Session持久绑定configGeneration/canonical consumer config/initialFrame/openedResponse；禁用inactive自动删除并对ACTIVE consumer lost fail closed；State签名BrokerOperationTicket与Provisioner claim/complete/Controller单次consume账本；bundle只声明requiredGateTestIds，测试报告后生成独立签名DATA-GATE-EVIDENCE-001并由active pointer绑定evidenceSha256。第五轮必须绑定新staged tree复审，旧tree/manifest不得批准。

### 第五轮结果

第五轮绑定staged tree `891a85b74e381800f292e0de74b5b49dba010edc`；三路在只读隔离snapshot首尾独立复算同一tree，未读取变化工作树。A/B/C均为FAIL，合计P0=0、P1=11；本记录不反向改写该轮结论。

| 复审流 | 结论 | 残余阻断 |
|---|---|---|
| A | **FAIL（第五轮，P0=0/P1=6）** | R5-01 Peer/Core State调用链不可达；R5-02 Task recovery无wire/身份；R5-03 DEAD outbox恢复无API/ACL；R5-04终态Plan可被recover；R5-05 BrokerOperation request digest字段冲突；R5-06单槽quiesce遗漏旧epoch迟到CREATE |
| B | **FAIL（第五轮，P0=0/P1=3）** | R5-01 required component自身NKey无READY权限；R5-02 Peer直连无可信Core身份；R5-03架构图把Controller/Provisioner画入broker框；另有非阻断P2：Supervisor/Orchestrator合框歧义 |
| C | **FAIL（第五轮，P0=0/P1=2）** | R5-01 GateEvidenceRecord无唯一管理API/CLI且activate未显式携带evidenceSha256；R5-02上线顺序在bundle STAGED前部署ACL；另有非阻断P2：TEST-CARD-PUBLIC-001仅摘要未完整定义 |

第五轮阻断已在第六轮工作树候选中定点修订，仍须重新冻结staged tree后验证：独立`application-core` NKey和受保护本地IPC；Supervisor直订dispatch并使用`task.command.get/recover/heartbeat`；受审`outbox.recover` Ops身份；80项State literal及components READY overlay；终态Plan永久拒绝takeover/派生；统一`brokerOpRequestDigest`；per-epoch broker operation ledger与单调全历史apply上界；GateEvidence stage API/CLI和显式evidence activation；STAGED→隔离candidate broker→维护门内production promote；Controller/Provisioner/Broker及Peer四个受信角色独立绘图；完整TEST-CARD-PUBLIC-001。第六轮不得复用第五轮tree或临时manifest。

### 第六轮调度无效记录

第六轮候选tree为`d27631c6a60b3e2a946de6edd70f7b1419af53cd`。该次调度只返回流A，且评审器把应为`DESIGN_CONTRACT`的门禁错误扩大为`IMPLEMENTATION_CAPABILITY`，以文档明确标注的prototype未实现项给出P0=5/P1=4；流B/C未形成结果。因此本次调度**不构成完整的三路设计复审轮、不能批准，也不把实现缺口转记为G0设计缺陷**。其原始FAIL输出保留在复审缓存中，后续以明确锁定设计域的第七轮替代；本记录不删除或伪装该次无效调度。

### 第七轮结果

第七轮绑定staged tree `0837b902c64a122e9e279ce68ccb3859d7b676a9`；三路在只读隔离snapshot独立复算同一tree，明确`OUT_OF_SCOPE_IMPLEMENTATION_GAPS`，只按活动设计合同判定。A/B/C均为FAIL，原始合计P0=0、P1=8、P2=8；其中A/C重复报告同一DEAD outbox谓词，去重后为7个唯一P1。本记录不反向改写该轮结论。

| 复审流 | 结论 | 残余阻断 |
|---|---|---|
| A | **FAIL（第七轮，P0=0/P1=4/P2=2）** | Supervisor伪代码绕过command.get→provisional lease→accept；task.recover无持久发现/稳定operation ID；DEAD outbox tuple/wire/幂等不闭合；Plan缺RUNNING/业务终态具名writer |
| B | **FAIL（第七轮，P0=0/P1=3/P2=4）** | GenesisIntent exact JWS未冻结；READY只存digest却要求后续验签；Audit/Recovery多签数组与集合schema/排序不唯一 |
| C | **FAIL（第七轮，P0=0/P1=1/P2=2）** | DEAD outbox把字符串eventId与整数sequence错误等同，G0-03不可实现 |

第七轮7个唯一P1及全部8个P2已在R8工作树候选中按小模块修订：Supervisor启动顺序与负例；recovery due/scanner/operation ID；Plan全业务writer；Genesis/READY/Audit/Recovery exact JWS；DEAD outbox四元比较/evidence/幂等CAS；Protected Local IPC、JS info汇总、rollout tombstone、Containment exact wire、架构图箭头/审计链及可复算治理计数。R8仍须重新冻结tree、跑完整门禁并取得A/B/C同tree PASS，修订声明本身不构成批准。

### R9-F / 第八轮结果

R9-F绑定commit `8ed1ebb1cd105c0b50156267c2dd3d62c9862ccb`、tree `315c49f254f4ac18bbad6ab13134a652f5dfea53`；三路均从只读Git snapshot首尾复算同一tree，明确判定域为`DESIGN_CONTRACT`且不把prototype实现缺口计为设计阻断。A/B/C均为FAIL，原始合计P0=0、P1=12、P2=3；旧tree永久保持FAIL，后续修订不反向改写。

| 复审流 | 结论 | 残余阻断 |
|---|---|---|
| A | **FAIL（第八轮，P0=0/P1=7/P2=1）** | Task recovery/admission与stable operation wire；Plan root终态writer；Stream session持久发现/flush；Gateway/Core authority；IPC journal exact codec/replay |
| B | **FAIL（第八轮，P0=0/P1=2/P2=0）** | Recovery compaction缺可达且fenced的writer/触发/幂等合同；架构图仍有Object Store→WORM旁路 |
| C | **FAIL（第八轮，P0=0/P1=3/P2=2）** | production GateEvidence因果顺序；Reconciliation claim-control/due scanner；typed-source target path与ref wire冲突；台账状态及链接计数陈旧 |

上述问题已进入R10分模块修订，但“局部探针通过/形成commit”不等于该轮问题已获独立关闭复审。

### 当前修订候选状态（非复审结论、非批准）

R10以第八轮FAIL为基线按可回滚小模块修订，当前已形成：R10-A `ded7bd0`（执行/recovery/Plan/Stream/Gateway-Core/IPC）、R10-B `7a86ff4`（Recovery compaction与WORM旁路）、R10-C1 `03dcd14`（production rollout evidence顺序）、R10-C2 `7d4eb71`（Recon claim-control/due scanner）、R10-C3 `092fb05`（source-centric typed refs）、R10-G3 `c81e0c0`（外链/架构资产/视觉）、G4正在收口真实NATS与e2e路径修复。G1代码门禁、G2文档合同门禁、G3外链/资产/视觉门禁和G4.1～G4.3局部门禁已分别通过；G4修复尚未提交最终checkpoint，仍需在最终tree重跑全套测试、最终冻结和A/B/C独立PASS。三路未全部PASS且manifest未复验前，本节与这些checkpoint均不构成批准。

## 5. 批准记录

当前结论：**未批准**。

只有第 4 节三路均为 PASS、残余问题清零，并生成和复验最终 content manifest 后，维护者才能把本节改为“G0 设计冻结通过”。代码实现状态必须继续保持为 prototype/未通过 CORE 门禁。
