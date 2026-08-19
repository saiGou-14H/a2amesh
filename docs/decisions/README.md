# A2AMesh 实现决策记录

本目录保存当前实施计划在版本化专项之间需要冻结的实现级决策。它们不得覆盖 `docs/specs/` 中声明的权威专项；已通过评审的决策应在下一次专项版本发布时被吸收或显式废弃。

| ID | 决策 | 状态 | 适用阶段 |
|---|---|---|---|
| [ADR-038](ADR-038_Redis_Key_Builder_V1.md) | Redis V1 Key builder 命名空间、组件 codec 与 bootstrap 模板 | Verified（代码 `eb254c0` / tree `f0df9a`；独立复审 PASS） | C2 §9.3 步骤2 |

状态词：

- `Proposed`：尚未批准进入实现；
- `Accepted for implementation candidate`：可以按合同实现，但不等于代码或阶段 PASS；
- `Verified`：精确 commit/tree 的实现、门禁和独立复审均闭合；
- `Superseded`：已由新 ADR 或新版本权威专项替代。
