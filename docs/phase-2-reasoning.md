# 第二阶段：本体推理与业务规则

[返回项目首页](../README.md)

## 目标与边界

第二阶段展示本体如何参与计算，以及独立规则如何使用推理结果。它保留第一阶段 MySQL 与 Ontop，仅增加供应中断事件和一个一次性推理容器。

实现方式是 **OWL-RL 本体推理 + 显式 SPARQL CONSTRUCT 规则执行**。这不是 Datalog 引擎，也不是通用规则平台。

## 供应中断事件

表 `supply_disruption_event` 保存：

| event_id | affected_supplier_id | event_type | is_current |
| --- | ---: | --- | ---: |
| E01 | 1 | DeliverySuspensionEvent | 1 |

`supplier_status=DISABLED` 只表示系统停用，不能解释为停供。第二阶段判断完全依据明确的当前有效事件。

新数据卷由 [`sql/init.sql`](../sql/init.sql) 初始化。已有数据卷使用幂等迁移：

```bash
docker compose up -d mysql
bash scripts/migrate-phase2.sh
```

迁移文件 [`sql/migrate-phase2.sql`](../sql/migrate-phase2.sql) 不删除供应商和订单，也不要求删除数据卷。迁移后重新创建 Ontop 容器，使它读取新的数据库元数据。

## Ontop 只导出基础事实

[`ontop/mapping.obda`](../ontop/mapping.obda) 增加事件映射，导出：

- 订单状态及实际采购方；
- E01 的直接类型 `DeliverySuspensionEvent`；
- E01 影响的实际供应商；
- E01 当前是否有效。

基础事实查询位于 [`queries/phase2-base-facts.rq`](../queries/phase2-base-facts.rq)，按事件表记录导出具体事件类型，不设置具体事件类型白名单。核心计算在检查父类之前，只去掉 `rdf:`、`rdfs:`、`owl:` 命名空间中的类型，其余类型保留。基础图一旦包含任何 `rdf:type SupplyDisruptionEvent`，核心计算即失败，CLI 返回退出码 4；查询不会过滤该父类。

## 本体类型推理

继承公理单独保存在 [`ontop/phase2-extension.ttl`](../ontop/phase2-extension.ttl)：

```turtle
:DeliverySuspensionEvent rdfs:subClassOf :SupplyDisruptionEvent .
```

Ontop 不加载该文件。一次性推理容器把基础事实放入新的 RDFLib 内存图，再加载扩展并执行 owlrl 前向推理：

```text
E01 rdf:type DeliverySuspensionEvent      # 基础事实
E01 rdf:type SupplyDisruptionEvent        # OWL-RL 新增
```

移除继承模式不把扩展中的任何 `rdfs:subClassOf` 公理装入推理图。基础事实中的父类仍会导致计算失败，不能通过删除这些事实模拟移除继承。数据库、Ontop Mapping、基础事实查询和业务规则保持不变。

## R-01 业务规则

规则位于 [`rules/R-01-needs-review.rq`](../rules/R-01-needs-review.rq)：

```text
如果订单状态为 PENDING，
订单实际采购自供应商 S，
并且存在当前有效的 SupplyDisruptionEvent 影响 S，
那么订单属于 NeedsReviewOrder。
```

风险条件不在 Python `if` 分支中。SPARQL CONSTRUCT 同时产生一个内存中的 `RuleApplication`，记录规则编号、订单、事件和供应商绑定，用于生成本 Demo 的事实依据。

结论只表示“需要供应风险复核”，不能表述为“必然无法交付”。未产生结论也不能表述为“订单安全”。

## 运行链路

```text
MySQL 当前数据
→ Ontop 导出基础 RDF
→ 新建空内存图
→ 加载阶段二本体扩展
→ owlrl 执行类型推理
→ 执行 R-01 SPARQL CONSTRUCT
→ 输出结论和实际规则绑定
```

运行：

```bash
bash scripts/demo-phase2.sh
```

每次运行都会创建新容器和空图，重新获取当前基础事实。它是全量重算，不是增量推理或增量撤回算法。数据库不保存 `NeedsReviewOrder`，因此事件失效后不会残留上次的派生结论。

## 直接调用核心计算

`reasoner.core.compute` 不解析命令行，不接受实验预期，也不打印。它负责从 Ontop 拉取事实、加载本体、执行类型推理与规则，并返回 `ReasoningResult`：

```python
from pathlib import Path
from reasoner.core import compute

result = compute(
    endpoint="http://ontop:8080/sparql",
    base_query=Path("queries/phase2-base-facts.rq"),
    ontology=Path("ontop/phase2-extension.ttl"),
    rule=Path("rules/R-01-needs-review.rq"),
    without_inheritance=False,
)
orders = result.needs_review_orders  # ["101"]
facts = result.base_facts           # 完整的基础 RDF 图，含命名空间绑定
summary = result.to_json()          # 现有 RESULT_JSON 的五个字段
```

以上端点用于 Compose 网络内的推理容器。宿主机调用时使用 `http://localhost:8080/sparql`，并需具备 `requirements-reasoner.txt` 中的依赖；既有演示无需在宿主机安装这些库。

返回结构还保存继承公理是否加载、新增父类型事实、规则绑定和规则文件路径，供 CLI 重放四段输出。基础事实不会加入终端的结果 JSON。CLI 在核心外执行 `--expect` 比对；`--expect one` 缺少 `--expected-order` 时，在拉取事实前以退出码 4 失败。

核心与 CLI 边界测试复用现有推理容器和 Ontop，在 E01 当前有效时运行：

```bash
docker compose --profile phase2 run --rm -T --entrypoint python reasoner \
  -B -m unittest discover -s reasoner -p 'test_*.py'
```

## 对照实验

```bash
bash scripts/test-phase2.sh
```

脚本验证：

1. 正常推理只命中订单 101；订单 102 已完成，订单 103 采购自其他供应商；
2. 移除继承公理后，E01 仍有直接类型，但父类型和规则结论消失；
3. E01 失效时，供应商仍为 DISABLED，但 SQL 和规则均无结果；
4. 恢复 E01 后，从空图重新计算并再次命中订单 101。

事件状态由 shell `trap` 在成功、失败或中断时恢复。输出证据位于 `.artifacts/phase2/`。

等价 SQL 位于 [`queries/needs-review-orders.sql`](../queries/needs-review-orders.sql)。SQL 同样能实现当前判断，并直接枚举本 Demo 已知的具体事件类型。本体方案让 R-01 依赖抽象的 `SupplyDisruptionEvent`，具体类型的分类关系可以在本体层维护。

## 证据能力的边界

RDFLib/owlrl 没有为本项目提供可直接使用的通用证明记录器。本 Demo 保存并核验所用继承公理，以及 R-01 构造的订单、事件和供应商绑定。这足以解释固定案例，但不代表系统具有任意 OWL 公理和任意规则的自动证明追踪能力。

## 增加的成本

语义层增加了事件类型质量、稳定 URI、Mapping、本体和规则版本、推理依赖、全量计算、证据输出及回归测试等维护工作。只有在跨系统语义复用、多个具体类型共享抽象规则或跨团队语义治理时，这些投入才更可能值得。
