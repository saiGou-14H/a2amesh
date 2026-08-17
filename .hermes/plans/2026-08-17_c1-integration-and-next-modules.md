# A2AMesh C1集成与后续State/控制面实施计划

> 这是执行计划，不是完成证明。每个模块必须有独立RED→GREEN、普通Git checkpoint、提交后全量门禁和精确树独立复审。

**目标：** 在不把候选checkpoint误报为整体完成的前提下，完成C1候选集成，然后按《A2AMesh_开发实施计划》把C2 Redis State与C2.5最小控制面拆成可验证、可回滚的小模块。

**当前基线（2026-08-17 Asia/Shanghai）：**

- C1-1最新候选：`1fcdbe02cc5b96f4cf6d01bd12472fa0d534aca3` / tree `ebbf3825c63866b66a6b66316a844da9b10106f7`，clean，8/8，`442 passed, 8 skipped`，精确QA pending。
- C1-3最新候选：`2fff0069d91a4fd085c66894741112b89bef28fd` / tree `83d10b06427047fd7934556aadf9b0f6a4ae69e0`，clean，8/8，`446 passed, 8 skipped`，精确QA `PASS`（仅限该checkpoint）。
- 两棵候选均未合入main、未push、未使用`[verified]`。

---

## 模块执行规则

每个模块固定执行：

1. 冻结父HEAD/tree/status；
2. 写最小RED测试并真实看到失败；
3. 实现最小GREEN；
4. 运行模块专项、相关回归、Ruff/compileall；
5. 运行仓库`run_ci.py`八门禁；
6. 检查旧实现/旧导入/旧文档是否仍有活动引用；
7. 创建普通checkpoint（不提前写`[verified]`）；
8. 在clean提交后重跑八门禁；
9. 派发只读精确树独立复审；
10. 复审通过后才进入下一模块。

外部证据单独记录：真实Redis、真实NATS/JetStream、secure ACL、Windows/NAT、远端Actions和生产验收不得由纯Python测试替代。

---

## M0：当前进度留档与候选冻结

**文件：**

- `docs/progress/A2AMesh_项目完成进度_2026-08-17.md`
- 本计划文件

**验收：**

- 当前候选hash/tree、测试计数、复审状态、未合入main和未push边界均有记录；
- 进度表Markdown结构无`||`/断行/旧状态覆盖；
- main工作树保持clean，候选worktree独立。

---

## M1：C1候选集成（只做语义组合）

**父候选：** C1-1 `1fcdbe0` + C1-3 `2fff006`。

**新worktree：** `/root/a2amesh-c1-integration-1`，不得在两棵被审树上merge或修复。

**冲突组合原则：**

- 保留C1-1：stream DTO exact gate、protobuf snapshot/digest完整性、legacy/NATS/stream verifier封存、V1 NATS server runtime-state边界；
- 保留C1-3：Core `dispatch_streaming` owner/iterator cleanup、bounded awaitable cleanup、validator modality、shared generation gate、response schema/fallback；
- `auth.py`：合并C1-1 policy/principal binding与C1-3 envelope generation exact gate；
- `envelope.py`：合并C1-1结构化exact字段检查与C1-3 shared verifier所需safe generation边界；
- `response.py`/schema：合并C1-1 exact DTO检查与C1-3控制字符schema、total remote fallback；
- `transport.py`：保留C1-1 server slots/class/delete封存，同时保留C1-3固定错误映射；
- `core/__init__.py`：联合导出`dispatch_unary`、`dispatch_streaming`、`validate_application_contract`；
- 两份文档：保留历史BLOCKED/PASS事实，追加集成候选状态，不重写历史报告。

**门禁：** 冲突解决后先运行import/collection，再运行完整测试和八门禁；任何冲突标记、旧函数引用或文档矛盾均阻断。

---

## M2：C1集成独立复审与旧代码边界清理

**目标：** 证明集成树而非两棵父树可工作，并识别可安全清理的旧代码。

**审查范围：**

- `src/a2amesh/core/`、`src/a2amesh/bindings/nats_v1/`、`src/a2amesh/identity/`；
- `src/a2amesh/a2anats/compatibility.py`及相关legacy adapter；
- `src/a2amesh/contracts/`与旧标准对象导入；
- 全部tests、docs、入口导出。

**清理规则：**

- 先`search_files`全仓库确认符号/动态导入/fixture/文档引用；
- 用`git blame`确认历史用途；
- 只删除“无活动引用且不属于设计要求保留的compatibility层”的代码；
- `a2anats/`设计文档明确要求迁移完成前保留，不得当前阶段擅删；
- 每次删除独立commit，先RED/coverage证明无调用，再GREEN/全量门禁。

---

## M3：C2-0纯State合同基线与模块索引

**设计依据：** `docs/specs/A2AMesh_开发实施计划.md` §9；现有纯合同：

- `src/a2amesh/state_contracts/`
- `tests/test_state_contracts_*.py`或对应专项

**工作：**

- 盘点现有`artifact_hold.py`、`reconciliation.py`纯合同与设计差异；
- 建立C2 active module index和“纯合同/Redis实现/真实集成”三栏状态；
- 清理重复常量、死导出、旧测试入口，但保留设计要求的历史compat层；
- 不宣称Redis持久化、Lua原子性或重启恢复。

**产物：** 普通checkpoint + 独立只读合同复审。

---

## M4：C2-1 Identity/Key/immutable snapshot垂直切片

**候选文件：**

- `src/a2amesh/state/`（若不存在则最小创建）
- `src/a2amesh/identity/`
- `tests/`对应纯状态测试

**RED：**

- mesh/key编码冲突、空/非法Principal、credential rotation不改变历史owner；
- snapshot digest对字段顺序/额外字段/别名变更fail closed；
- 输入map深拷贝后调用方变更不影响快照。

**GREEN边界：** 纯Python key builder、immutable command/Task owner snapshot、确定性canonical bytes；不接Redis。

**门禁：** 独立fixture verifier不能调用实现自身digest helper；通过后checkpoint。

---

## M5：C2-2 Idempotent command/message claim

**设计任务对应：** `claim_auth_request`、`claim_message`、immutable command、Task初始快照。

**RED：**

- 同messageId同digest只产生一个逻辑Task；
- 同ID异digest返回conflict且零写；
- replay在后续状态推进后仍逐字节返回原结果；
- forged self-rehashed result不能跨Task/Context/Principal绑定。

**GREEN边界：** 先做传输无关纯状态CAS模型与write-set，不假称Redis Lua。

---

## M6：C2-3 Task transition/event/outbox纯状态切片

**RED：**

- `eventSeq=current+1`唯一分配；
- 非法状态迁移、终态复活、旧version/token写入拒绝；
- Task快照、索引、outbox、dispatch intent要么同提交要么零写；
- Relay head-of-line：n+1不能越过未完成n。

**GREEN边界：** 纯状态事务模型、确定性outbox/result；真实Redis Function另立模块。

---

## M7：C2-4 lease/fencing/recovery纯状态切片

**RED：**

- 双owner只能一个lease；
- 旧token/fence late write拒绝；
- lease过期reclaim不会重复副作用；
- stale APPLYING只形成一个UNKNOWN/case；
- cancel/accept race有明确CAS结果。

**GREEN边界：** 纯状态模型与并发测试，记录需要真实Redis/多进程验证的缺口。

---

## M8：C2.5最小控制面垂直模块（再拆四个checkpoint）

1. **Config/GateEvidence：** genesis、READY、evidenceSha256、activate/rollback、签名阈值；
2. **Artifact fixture：** checksum、finalize/ticket/delete、hold/reaper纯合同；
3. **Reconciliation：** claim/renew/release/expire/escalate与UNKNOWN；
4. **Audit/Recovery：** append-only fixture、Manifest/summary DAG、restore/approval。

每个模块只做一个权威writer和一组crash/replay RED，不跨模块偷接未实现服务。

---

## M9：真实Redis适配与集成门禁

**前置：** M4–M8纯合同均有独立PASS。

- Redis Function/Lua原子实现；
- disposable Redis fixture、重启/并发/故障注入；
- 纯合同fixture与真实Redis结果逐项对照；
- 任何skip（Redis不可用）只能记为能力缺口，不记PASS。

---

## M10：C3真实NATS/JetStream与Windows/NAT

**前置：** C1集成、C2、C2.5、真实Redis均有明确证据。

- secure NKey/ACL actor-operation graph；
- outbox/Event Relay/Dispatch Worker；
- stream session generation/takeover/ACK/recovery；
- Linux↔Windows NAT出网注册与对称A2A调用；
- 远端Actions、真实broker和生产验收单独留档。

---

## 每模块进度报告模板

```text
模块：<ID/name>
目标：<一句话>
当前checkpoint：<HEAD/tree/worktree>
状态：RED/GREEN/candidate/PASS/BLOCKED
真实门禁：<命令与结果>
工作树：clean/dirty
清理：<删除/保留及搜索证据>
未完成/阻断：<明确列出>
下一步：<一个模块>
```
