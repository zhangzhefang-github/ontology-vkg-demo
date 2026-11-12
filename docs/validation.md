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
docker compose --profile phase2 run --rm -T --entrypoint python reasoner \
  -B -m unittest discover -s reasoner -p 'test_*.py'
```

完整测试需要 MySQL 和 Ontop 健康，且 E01 当前有效；`test-phase2.sh` 结束时会恢复 E01。核心与 CLI 测试也由持续集成运行。

## 验证矩阵

| 场景 | 基础事实中的直接类型 | 推理出的父类型 | 规则结果 | SQL 对照 |
| --- | --- | --- | --- | --- |
| 第一阶段 | 不适用 | 不适用 | 不适用 | SQL = SPARQL，订单 101 |
| 正常第二阶段 | DeliverySuspensionEvent | SupplyDisruptionEvent | 订单 101 | 订单 101 / E01 |
| 移除继承公理 | DeliverySuspensionEvent | 无 | 无 | 固定 SQL 不随本体改变 |
| E01 失效 | DeliverySuspensionEvent | SupplyDisruptionEvent | 无 | 无 |
| 恢复 E01 | DeliverySuspensionEvent | SupplyDisruptionEvent | 订单 101 | 订单 101 / E01 |

2026-09-08 的发布前实测全部通过，MySQL 和 Ontop 均为 `healthy`，测试结束后 E01 为有效状态。

## 核心与 CLI 边界测试

[`reasoner/test_core.py`](../reasoner/test_core.py) 和 [`reasoner/test_cli.py`](../reasoner/test_cli.py) 在当前代码中共包含 18 个测试方法，覆盖核心返回结构、基础事实与继承公理隔离、类型导出、CLI 输出和失败处理。测试数量不代表本页所列场景均已重新实测；上面的日期记录仅对应当时的发布前验证。

2026-11-12 的三个补测试提交没有改变业务实现，新增以下 4 个测试：

| 提交 | 测试方法 | 验证行为 |
| --- | --- | --- |
| `66e649a` | `CoreUnitTests.test_event_direct_types_ignores_non_uri_events_and_types` | 提取事件直接类型时忽略非 URI 事件，以及字面量或空白节点类型，仅返回 URI 事件及其 URI 类型 |
| `66e649a` | `CLITests.test_invalid_expect_choice_fails_before_computing` | `--expect many` 在参数解析阶段以退出码 2 失败，不调用核心计算，stdout 为空，stderr 含参数错误 |
| `ec2cc1b` | `CoreUnitTests.test_compute_rejects_select_rule_instead_of_construct` | 规则使用 `SELECT` 而非返回图的 `CONSTRUCT` 时，核心计算抛出 `ValueError`，提示“业务规则必须返回 CONSTRUCT 图” |
| `78aec5b` | `CLITests.test_unexpected_compute_error_is_reported_without_stdout` | 核心抛出未预期的 `RuntimeError` 时，CLI 返回 5，stdout 为空，stderr 为 `REASONER_ERROR: RuntimeError: reasoning failed` 加换行 |

这 4 项使用内存 RDF 或替代的计算、HTTP 拉取边界，不依赖运行中的 MySQL 和 Ontop。可复用已构建的推理镜像单独验证：

```bash
docker compose --profile phase2 run --rm -T --no-deps --entrypoint python reasoner \
  -B -m unittest -v \
  reasoner.test_core.CoreUnitTests \
  reasoner.test_cli.CLITests.test_invalid_expect_choice_fails_before_computing \
  reasoner.test_cli.CLITests.test_unexpected_compute_error_is_reported_without_stdout
```

2026-11-12 在现有推理镜像中执行上述独立命令，4 个测试全部通过（`Ran 4 tests`、`OK`）。本次未重跑完整的 18 项测试和 MySQL、Ontop 对照实验。

这些测试验证错误处理和类型提取边界，不替代完整的 Ontop 集成与 SQL/SPARQL 对照实验。

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
| 2 | CLI 参数解析失败（如非法 `--expect`），或 Ontop 服务请求、响应异常；两者应结合 stderr 区分 |
| 3 | 请求成功，但基础事实为空，或缺少具有具体类型的 URI 事件 |
| 4 | 实验条件或断言失败，如 `--expect one` 缺少 `--expected-order`、基础事实提前包含供应中断事件父类，或命中结果不符 |
| 5 | 其他推理程序错误 |

反事实场景明确预期“不命中”时返回 0；这与服务异常或数据意外为空不同。

参数解析错误由 `argparse` 输出用法和错误说明；核心失败经 CLI 映射为 `SERVICE_ERROR:`、`EMPTY_BASE_FACTS:`、`EXPERIMENT_FAILURE:` 或 `REASONER_ERROR:`。核心计算本身抛出异常，不返回 CLI 退出码。失败不输出成功的 `RESULT_JSON`；实验结果比对失败时，之前的四段演示输出可能已写入 stdout。

## 持续集成

GitHub Actions 工作流 [`.github/workflows/integration-test.yml`](../.github/workflows/integration-test.yml) 在 Ubuntu runner 上启动完整 Compose 环境，执行第一、二阶段测试，以及上述完整的核心与 CLI 测试发现命令。工作流失败时输出 MySQL 和 Ontop 日志，并始终清理测试容器和卷。
