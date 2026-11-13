# 供应风险复核决策闭环

本演示复用现有 R-01 推理计算。查询命中只表示订单需要供应风险复核，不表示无法交付；没有命中也不表示安全。模块操作是业务验收入口，MCP 和命令行只是适配器。

## 独立环境

新闭环使用独立镜像和 Compose 配置，不改变阶段二推理镜像。以下命令从仓库根目录执行；宿主机只需 Docker Compose。项目名同时用于隔离镜像标签、网络、容器和卷，无宿主机端口发布。

```bash
export DECISION_PROJECT=ontology-vkg-decision-demo
docker compose -f compose.decision.yml up -d --build --wait mysql ontop
docker compose -f compose.decision.yml build decision
```

初始化脚本将 Ontop 的权限收窄到三张源表的 SELECT。已有卷不会重复初始化；本演示必须使用新的独立项目名和卷。不要对原演示项目执行清理命令。

## 查询与 MCP

在容器内直接调用模块操作：

```bash
docker compose -f compose.decision.yml run --rm -T --entrypoint python decision \
  -c 'import json; from decision.service import DecisionService; print(json.dumps(DecisionService().query(), ensure_ascii=False))'
```

正常结果只有订单 101，证据包括 E01、交付暂停事件、推出的供应中断事件及 R-01 规则应用。查询不写决策或业务数据，推理服务错误以失败返回。

运行一次实际 stdio 握手、工具枚举与查询检查（输出应只含 `query_supply_review`）：

```bash
docker compose -f compose.decision.yml run --rm -T --entrypoint python decision \
  scripts/smoke-decision-mcp.py
```

外部 Agent 的 MCP 配置使用标准输入输出命令：

```json
{
  "mcpServers": {
    "procurement-supply-review": {
      "command": "docker",
      "args": ["compose", "-f", "/absolute/path/to/compose.decision.yml", "run", "--rm", "-T", "decision"],
      "env": {"DECISION_PROJECT": "ontology-vkg-decision-demo"}
    }
  }
}
```

替换绝对路径，并保持项目名与启动命令一致。第一张任务只提供 `query_supply_review`；提交建议由后续任务加入。审批、执行和追溯不注册为 Agent 工具。日志使用 stderr，不往协议 stdout 打印诊断。

## 验收与清理

测试通过模块操作观察真实 MySQL 与 Ontop 的结果。管理账号仅供夹具模拟业务变化，不能注入 MCP 的运行配置；用例会恢复 E01。对同一项目串行执行这些用例与阶段二实验。

```bash
docker compose -f compose.decision.yml run --rm -T \
  -e TEST_DB_PASSWORD=demo_root_password --entrypoint python decision \
  -B -m unittest decision.test_query
# 仅删除此处单独创建的演示环境及其数据：
docker compose -f compose.decision.yml down --volumes --remove-orphans
```

依赖选择依据见 [技术选型](decision-loop-technology-selection.md)。`requirements-decision.in` 是直接依赖输入，`requirements-decision.txt` 固定传递依赖与 SHA-256；镜像使用 `--require-hashes` 安装。依赖锁由 Python 3.12 容器中的 `pip-compile --generate-hashes` 生成。
