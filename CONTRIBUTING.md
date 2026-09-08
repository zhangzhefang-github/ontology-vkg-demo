# Contributing

[中文](#中文) | [English](#english)

## 中文

感谢你改进这个架构验证项目。请保持项目范围聚焦于关系数据库、Ontop 虚拟知识图谱、OWL-RL 类型推理和显式 SPARQL 规则。

提交 Issue 前，请先查看现有 Issue 和 [`docs/troubleshooting.md`](docs/troubleshooting.md)。Bug 报告应包含：

- 操作系统、Docker Desktop 和 Docker Compose 版本；
- 完整执行命令；
- `docker compose ps` 输出；
- 相关容器日志；
- 预期结果和实际结果。

提交代码时：

1. 从 `main` 创建短生命周期分支；
2. 不提交 `.artifacts/`、JAR、数据库卷或凭据；
3. 保持 shell 脚本使用 LF，失败时返回非零退出码；
4. 将业务规则保存在 `rules/`，不要把风险条件硬编码进 Python；
5. 更新受影响的中英文入口或详细文档；
6. 在提交 Pull Request 前运行：

```bash
docker compose config --quiet
bash scripts/test-sql.sh
bash scripts/test-sparql.sh
bash scripts/test-phase2.sh
```

Pull Request 应说明问题、行为变化、验证结果和已知限制。安全问题不要提交公开 Issue，请遵循 [`SECURITY.md`](SECURITY.md)。

## English

Contributions are welcome. Keep the scope focused on relational data, the Ontop virtual knowledge graph, OWL-RL type reasoning, and explicit SPARQL rules.

Before opening an issue, review existing issues and [`docs/troubleshooting.md`](docs/troubleshooting.md). A bug report should include the operating system, Docker versions, exact command, `docker compose ps`, relevant logs, expected result, and actual result.

For code changes:

1. Create a short-lived branch from `main`.
2. Do not commit `.artifacts/`, JAR files, database volumes, or credentials.
3. Keep shell scripts on LF line endings and return non-zero on failure.
4. Keep business conditions in `rules/`; do not hard-code risk logic in Python.
5. Update the affected English/Chinese entry point or detailed documentation.
6. Run the commands shown above before opening a pull request.

Describe the problem, behavior change, verification, and known limitations in the pull request. Follow [`SECURITY.md`](SECURITY.md) for security reports.
