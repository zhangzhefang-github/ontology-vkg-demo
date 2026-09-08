# 验证矩阵与实际结果

[返回项目首页](../README.md)

## 已验证环境

| 项目 | 实际环境 |
| --- | --- |
| 宿主系统 | Windows + Docker Desktop |
| Linux 环境 | WSL 2 Ubuntu |
| 容器启动 | Docker Compose |
| MySQL | `mysql:8.4.8` |
| Ontop | `ontop/ontop:5.5.0` |
| 推理运行时 | `python:3.12.11-slim-bookworm` |
| 推理库 | RDFLib 7.1.4、owlrl 7.1.4 |

## 发布前命令

```bash
docker compose config --quiet
bash scripts/demo-phase2.sh
bash scripts/test-sql.sh
bash scripts/test-sparql.sh
bash scripts/test-phase2.sh
```

## 验证矩阵

| 场景 | 基础事实中的直接类型 | 推理出的父类型 | 规则结果 | SQL 对照 |
| --- | --- | --- | --- | --- |
| 第一阶段 | 不适用 | 不适用 | 不适用 | SQL = SPARQL，订单 101 |
| 正常第二阶段 | DeliverySuspensionEvent | SupplyDisruptionEvent | 订单 101 | 订单 101 / E01 |
| 移除继承公理 | DeliverySuspensionEvent | 无 | 无 | 固定 SQL 不随本体改变 |
| E01 失效 | DeliverySuspensionEvent | SupplyDisruptionEvent | 无 | 无 |
| 恢复 E01 | DeliverySuspensionEvent | SupplyDisruptionEvent | 订单 101 | 订单 101 / E01 |

2026-09-08 的发布前实测全部通过，MySQL 和 Ontop 均为 `healthy`，测试结束后 E01 为有效状态。

## 正常场景证据

```text
<https://example.org/event/E01> rdf:type proc:DeliverySuspensionEvent
<https://example.org/event/E01> rdf:type proc:SupplyDisruptionEvent
R-01 命中: order=101, event=E01,
           supplier=<https://example.org/supplier/1>
订单101需要供应风险复核。
```

机器可读的 `RESULT_JSON` 包含：

- 基础事实三元组数量；
- 继承公理是否加载；
- 推理新增父类型的事件；
- `NeedsReviewOrder` 订单；
- R-01 的订单、事件、供应商和状态绑定。

## 失败语义

测试失败返回非零退出码。推理程序进一步区分：

| 退出码 | 含义 |
| ---: | --- |
| 2 | Ontop 服务请求或响应异常 |
| 3 | 请求成功，但基础事实为空或缺少事件 |
| 4 | 实验断言失败，例如预期命中但没有命中 |
| 5 | 其他推理程序错误 |

反事实场景明确预期“不命中”时返回 0；这与服务异常或数据意外为空不同。

## 持续集成

GitHub Actions 工作流 [`.github/workflows/integration-test.yml`](../.github/workflows/integration-test.yml) 在 Ubuntu runner 上启动完整 Compose 环境并执行第一、二阶段测试。工作流失败时输出 MySQL 和 Ontop 日志，并始终清理测试容器和卷。
