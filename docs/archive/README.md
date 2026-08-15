# A2AMesh 历史设计归档

本目录保存已发布但已被替代的设计文档和版本化架构资产，仅用于审计、差异比较与历史追溯。

- 当前权威设计：[`docs/specs`](../specs/README.md)
- [`v1.0`](v1.0/README.md)：首版主专项与三个控制面专项首版；含跨版本兼容指针
- [`v1.1`](v1.1/README.md)：对称路由、双 Binding、MCP 桥与追踪合同
- [`v1.2`](v1.2/README.md)：Canonical Principal、MCP 幂等、OAuth AS/JWKS 与 tenant 空值合同
- [`v1.3`](v1.3/README.md)：交付剖面、outbox/effect、capability/admission、RTO/RPO 与 Card publisher
- [`v1.4`](v1.4/README.md)：Artifact/Object Store、配置与对账闭环引入时的主专项版本桶
- [`v1.5`](v1.5/README.md)：V1.6/V1.2 发布前最后一套完整活动集
- `assets`：V1.1/V1.2 的 PNG、SVG 与自包含 HTML 架构渲染

## 归档模型

`v1.0`～`v1.4` 是**版本桶**，不保证单个目录独立构成完整发布包；每个目录 README 给出精确正文清单及必要的跨版本兼容指针。`v1.5` 是上一套完整活动基线，由 8 份 V1.5 主专项与 3 份 V1.1 控制面专项组成。

已发布历史正文保持字节不变。兼容指针和 README 属于归档导航元数据，可用于修复历史链接，但不会把旧内容重新定义为当前合同。

归档可以由当前文档用于 supersession、审计或差异追踪链接；**不得作为规范性实现依据、代码合同或新测试 fixture 的权威来源**。需要修订当前设计时，从最新活动文档递增版本，不修改历史正文。
