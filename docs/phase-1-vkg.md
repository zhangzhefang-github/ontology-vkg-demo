# 第一阶段：Ontop 虚拟知识图谱

[返回项目首页](../README.md)

## 目标

第一阶段验证以下链路：

```text
业务概念 → Ontology → OBDA Mapping → SPARQL
        → Ontop 确定性改写 SQL → MySQL 结果
```

业务问题是“已停用供应商名下尚未完成的采购订单”。本项目将“尚未完成”明确限定为 `PENDING`，预期结果只有 `101 / 华北钢材 / 50000.00`。

SPARQL 是查询语言，不需要单独安装。Ontop 根据固定 Mapping 改写查询；大模型没有参与查询生成。

## 数据模型

供应商：

| supplier_id | supplier_name | supplier_status |
| ---: | --- | --- |
| 1 | 华北钢材 | DISABLED |
| 2 | 东方电子 | ACTIVE |

采购订单：

| order_id | supplier_id | amount | order_status |
| ---: | ---: | ---: | --- |
| 101 | 1 | 50000.00 | PENDING |
| 102 | 1 | 30000.00 | COMPLETED |
| 103 | 2 | 20000.00 | PENDING |

初始化脚本位于 [`sql/init.sql`](../sql/init.sql)。金额使用 `DECIMAL(12,2)`，所以 `50000.00` 与 `50000` 数值相同。

## Ontology 与 Mapping

基础本体 [`ontop/ontology.ttl`](../ontop/ontology.ttl) 定义：

- `Supplier` 和 `PurchaseOrder`；
- `supplierName`、`supplierStatus`、`orderStatus`、`amount`；
- `PurchaseOrder orderedFrom Supplier`。

OBDA Mapping [`ontop/mapping.obda`](../ontop/mapping.obda) 把表和列映射为 RDF 类、属性、关系及稳定 URI：

| 关系数据 | RDF 表达 |
| --- | --- |
| `supplier` 行 | `https://example.org/supplier/{supplier_id}` |
| `purchase_order` 行 | `https://example.org/purchase-order/{order_id}` |
| `purchase_order.supplier_id` | `orderedFrom` 对象关系 |
| 名称、状态 | `xsd:string` |
| 金额 | `xsd:decimal` |

MySQL 使用 utf8mb4 和二进制排序规则，保留中文并避免数据库大小写比较与 RDF 字符串语义出现意外差异。

## 启动

```bash
docker compose config
docker compose up -d --build
docker compose up -d --wait --wait-timeout 240
docker compose ps
```

服务地址：

- Ontop 页面：<http://localhost:8080/>
- SPARQL Endpoint：<http://localhost:8080/sparql>
- MySQL 宿主机端口：`127.0.0.1:13306`

容器内部的 JDBC 地址始终是 `mysql:3306`。宿主机的 `13306` 不用于 Ontop 容器间通信。

## SQL 与 SPARQL 验证

传统 SQL：

```bash
bash scripts/test-sql.sh
```

SPARQL 及 SQL 结果比较：

```bash
bash scripts/test-sparql.sh
```

测试会校验唯一订单、供应商名称、金额、订单 URI 和 RDF 数据类型。原始结果保存在 `.artifacts/sql.tsv` 和 `.artifacts/sparql.json`。

查询文件：

- [`queries/risky-orders.sql`](../queries/risky-orders.sql)
- [`queries/risky-orders.rq`](../queries/risky-orders.rq)

订单 102 因为状态为 `COMPLETED` 被排除；订单 103 因为供应商状态为 `ACTIVE` 被排除。

## 查看 Ontop 改写结果

项目启用了 Ontop 开发模式，可以查看查询计划和其中的原生 SQL：

```bash
mkdir -p .artifacts
curl --fail-with-body --silent --show-error \
  --data-urlencode 'query@queries/risky-orders.rq' \
  http://localhost:8080/ontop/reformulate \
  | tee .artifacts/reformulated.txt
```

Ontop 5.5.0 返回包含 `CONSTRUCT` 和 `NATIVE` 节点的查询计划。`NATIVE` 下是数据库 SQL；整个响应不是可直接交给 MySQL 的纯 SQL 文件。

也可以查看 debug 日志：

```bash
docker compose logs --no-color ontop > .artifacts/ontop.log
grep -n -A 20 -B 5 -E 'SQL|Native|SELECT' .artifacts/ontop.log
```

Ontop 生成的别名、投影和 URI 构造表达式可能与手写 SQL 不同。对照重点是关联、过滤条件和最终结果。

## 第一阶段的价值边界

在这个两表案例中，直接 SQL 更简单。虚拟知识图谱的主要价值出现在多个数据源需要统一业务词汇、调用方需要与物理表结构解耦、或语义模型需要跨团队治理的场景。Ontology 不会自动发现数据库外键，Mapping 仍然需要维护。
