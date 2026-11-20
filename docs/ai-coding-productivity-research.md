# AI coding 提效：证据与可执行方法

调研日期：2026-11-08。仅使用研究机构、工程组织的原始资料；未在本项目进行提效实验。以下方法是对资料的综合建议，不是某个组织认证的统一标准。

## 结论与来源选择

本次没有找到适用于所有项目、全球统一的 AI coding 提效标准。较可靠的路线是：沿用小批次交付、快速反馈和结果度量等工程方法，再改善代理的上下文、工具、验证与任务隔离。

| 来源 | 值得参考的内容 | 证据边界 |
| --- | --- | --- |
| Google Cloud DORA | AI Capabilities Model、AI 辅助交付研究、小批次与反馈循环 | 包含调查和相关性研究，不能把相关性直接解释为因果 |
| ACM Queue / SPACE | 从满意度、绩效、活动、协作、效率与流动等维度衡量开发生产力 | 通用软件工程测量框架，不是专为 AI 编码设计 |
| Thoughtworks Technology Radar | harness engineering、多会话与 subagent 的工程取舍 | 全球团队的经验判断，不是随机对照实验 |
| METR | AI 对真实开发任务的实证测量及测量偏差 | 特定人群、工具和任务；不同年份的结果不能互相替代 |

DORA 将 AI 视为既有组织能力的放大器；其 2025 年度报告和七能力模型强调底层工程体系的重要性。[2025 报告](https://dora.dev/research/2025/dora-report/)、[能力模型](https://dora.dev/ai/capabilities-model/report/)

## 三类可以落地的方法

### 1. 小批次交付与快速验证

DORA 明确建议将工作切成独立、可验证的小批次，避免 AI 一次生成巨大 PR 后把成本转嫁给审查和集成。可用 INVEST 检查任务：独立、可协商、有价值、可估算、小、可测试。[Working in small batches](https://dora.dev/capabilities/working-in-small-batches/)

建议每个 Issue 至少写清目标、验收条件、非目标、依赖和验证命令；允许实现后反馈改变方案。规格有用，但本次证据不支持所有任务都要引入重型规格框架或追求一次性完整设计。

### 2. Harness engineering：改善代理周围的执行条件

Thoughtworks Radar Vol 35 将 harness engineering 列为主题：关注代理的上下文、记忆、工具、反馈与约束环境，而不只是提示词。它同时讨论多个会话、worktree、窄范围 subagent 以及审查和交付容量的约束。[Technology Radar](https://www.thoughtworks.com/radar)

对本仓库的建议是维护好已有的 `AGENTS.md`、`GLOSSARY.md`、ADR 和可运行验证入口。主代理负责接口与交付；subagent 负责边界清晰的分析、实现或审查；不同写任务明确文件归属或使用独立工作树。

### 3. 度量交付结果，持续调整工作流

DORA 2026-03-10 的文章基于 1,110 条 Google 工程师开放调查回答，指出初稿生成节省的时间经常转移到审计和验证；更快生成代码不保证端到端交付更快。其建议包括反馈循环、作者侧验证和小批次。[Balancing AI tensions](https://dora.dev/insights/balancing-ai-tensions/)

建议选择相近类型的任务，先记录一段时间基线，再逐项尝试改变分工或工具。至少记录：

- 从开始到验收通过的日历时间，以及人工实际投入时间；并行时二者分开计算。
- 审查等待与修改时间、返工轮次、合并后的缺陷。
- 模型与环境费用，以及为维护自动化投入的时间。

避免用生成代码行数、PR 数、代理数量或单纯主观“感觉更快”作为唯一指标。小样本试验用于本团队改进，不应包装成普遍因果结论。

ACM Queue 发表的 SPACE 框架同样强调开发生产力不能由单一活动指标衡量，需结合人的体验、成果、协作和工作流；可用它检查上述测量是否遗漏重要维度。[SPACE 原始论文介绍](https://www.microsoft.com/en-us/research/publication/the-space-of-developer-productivity-theres-more-to-it-than-you-think/)

## 为什么不能只看“提效百分比”

METR 2025-07-10 的随机对照实验研究了 16 位熟悉成熟开源仓库的开发者、246 个任务及早期 2025 工具。在该条件下，允许 AI 的任务耗时增加 19%，而参与者仍主观认为得到提速。它不证明所有开发者或今天的工具都变慢。[原始研究](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)

METR 2026-02-24 后续公布：新数据倾向于提速，但参与者和任务选择偏差、报酬变化以及多代理并行计时问题，使效应大小难以可靠解释。正确启示是用实际结果校准主观感受，并标明任务、工具版本和测量口径。[后续研究设计说明](https://metr.org/blog/2026-02-24-uplift-update/)

更新到 2026-11-05，METR 对 Anthropic AI 研发的评估已讨论明显加速，但存在很大不确定性，且部分关键证据被删节。它研究的是特定机构的 AI 研发进展，不可用来声称普通软件项目存在相同倍数收益。[2026-11-05 评估](https://metr.org/blog/2026-11-05-anthropic-ai-rd-acceleration-assessment/)

## 本项目最小实施建议

以下是基于上述资料与仓库现状的建议，尚未实施：

1. **准备任务**：在 GitHub Issue 写清验收条件和依赖，复用现有 [Issue 流程](agents/issue-tracker.md)，不新增第二套任务真相来源。
2. **提供上下文**：让代理读取 [领域词汇](../GLOSSARY.md)、相关 ADR 和验证入口，再开始修改。
3. **限制在制任务**：从少量独立任务开始；出现审查堆积或集成等待时先减少并发。这里没有可直接套用的“最佳代理数量”。
4. **隔离写任务**：明确文件归属；跨 Issue 并行修改使用独立工作树。工作树不自动隔离 Docker 资源。
5. **保留真实反馈**：本项目的 SQL/SPARQL 对照、本体公理移除、事件失效与恢复实验及核心/CLI 测试继续作为验收证据，按改动范围执行。[README 验证入口](../README.md)、[CI](../.github/workflows/integration-test.yml)
6. **集成先串行**：当前 Compose 固定项目名、端口和镜像标签，阶段二测试还会修改 E01；在资源隔离前，由主代理串行运行完整集成验证。[Compose](../docker-compose.yml)、[阶段二脚本](../scripts/test-phase2.sh)
7. **定期复盘**：比较交付时间、人工审查投入、缺陷与成本，确定下一个真正的瓶颈，再决定是否增加并发或引入 Sandcastle 等编排工具。

这套流程以“小批次、可验证、上下文明确、有限并发、结果度量”为起点；它不要求先搭建复杂多代理平台。
