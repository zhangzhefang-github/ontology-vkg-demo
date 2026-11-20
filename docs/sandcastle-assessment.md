# Sandcastle 适配性评估

评估日期：2026-11-08。Sandcastle 基准为提交 [`ed8e716`](https://github.com/mattpocock/sandcastle/commit/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8)，其 `package.json` 版本为 `0.14.0`；该数字不代表已核实 npm 最新发布版本。本项目基准为 `3e28ed2`。本次只审查文档、配置和代码，未安装 Sandcastle，未执行代理任务，也未验证性能或费用。

## 结论

**暂不作为项目默认开发依赖；有持续的编码任务编排需求时，再做小范围试用。**

| 使用位置 | 判断 | 理由 |
| --- | --- | --- |
| 业务运行时：本体推理、规则执行 | 不适合当前目标 | 项目验证的是确定性语义计算；Sandcastle 编排的是会读写代码的代理，两者职责不同。 |
| 单个代理日常改代码 | 能用，但当前收益有限 | 项目已有分支、Docker 环境和 CI；需额外维护编排运行时、凭据和沙箱配置。 |
| 多个独立任务、批量修复、自动审查 | 有条件适合 | 独立沙箱和命名分支有用，但现有集成环境需要先处理资源隔离。 |

上述为适配性判断，依据是本项目 [README](../README.md)、[贡献指南](../CONTRIBUTING.md)、[现有 CI](../.github/workflows/integration-test.yml) 和 Sandcastle 的[能力定位][getting-started]。本项目 [ADR-0002](adr/0002-reasoner-core-boundary.md) 明确本轮不增加 Agent 入口；开发辅助工具可独立试用，不应改变核心计算边界。

## 已核实的能力和接入要求

- Sandcastle 是 TypeScript 编码代理编排库，负责沙箱、工作树、分支以及提交回收；官方支持 Claude Code、Codex、Pi、Copilot、Cursor、OpenCode。目标代码库无需改成 TypeScript。[快速开始][getting-started]、[代理提供方][agents]
- 官方接入方式需要 npm 安装包、生成 `.sandcastle/`，再通过 `tsx` 运行编排文件。因此本仓库虽以 Python、Shell、Docker 为主，仍可使用它，但会新增一套 Node/npm 开发工具链。已审阅的包配置未声明统一 `engines.node` 下限，不能把某个云 SDK 的 Node 要求推广成所有模式的最低版本；试用时应固定并验证 Node 和依赖版本。[快速开始][getting-started]、[package.json][package]
- Linux/WSL2 可使用 Bubblewrap，macOS 可使用 Seatbelt，也可选择 Docker、Podman 或云沙箱。WSL2 的 Bubblewrap 方案要求代码位于 Linux 文件系统；其内部隐藏 `/mnt`、`/run/WSL` 和 Windows 互操作入口。项目的 Docker Desktop + WSL2 环境方向上匹配，但不能据此认定现有 Docker 工作流在沙箱内直接可用。[沙箱提供方][sandboxes]、[项目环境说明](../README.md)
- 原生沙箱使用独立 HOME，不自动继承宿主机代理和 `gh` 登录。代理 CLI、认证注入及所需工具必须单独配置。[沙箱提供方][sandboxes]
- 分支策略必须显式选择：bind-mount 提供方默认 `head`，会直接修改当前工作目录；isolated 提供方默认 `merge-to-head`，任务结束后自动合并。试用建议明确使用 `branch`，保留独立命名分支供 CI 和人工审查。[分支策略][branches]
- 文档中的 hooks 用于工作树或沙箱就绪时的初始化，非零退出会中止初始化；它们不等同于代理完成后的质量门禁。最终验证应由外层流程或现有 CI 执行。[Hooks][hooks]、[现有 CI](../.github/workflows/integration-test.yml)

## 当前最重要的适配障碍：运行环境未按任务隔离

Sandcastle 能隔离代理的代码工作区，不能自动把项目所访问的同一个 Docker daemon 上的业务资源隔开。以下风险基于本仓库配置推导，尚未做并发复现。

1. **Compose 项目名固定。** [`docker-compose.yml`](../docker-compose.yml) 使用 `name: ontology-vkg-demo`。多个工作树在同一个 daemon 上按默认命令启动时，会操作同一 Compose 项目和 `mysql-data` 卷；仅有不同 Git 分支不够。
2. **端口与镜像也需要隔离。** 默认发布 `13306`、`8080`，虽支持环境变量覆盖，但独立项目仍需分配不同端口。Ontop、reasoner 的构建镜像标签固定；不同工作树若并发构建到同一标签，后续容器可能使用另一任务的镜像。只设置 `COMPOSE_PROJECT_NAME` 不能解决全部问题。依据：[`docker-compose.yml`](../docker-compose.yml)。
3. **测试会修改数据库状态。** [`scripts/test-phase2.sh`](../scripts/test-phase2.sh) 将 E01 的 `is_current` 设为 FALSE，再恢复为 TRUE。`trap` 负责结束后的恢复，不保证两个测试同时运行时相互隔离。CI 的 `down --volumes --remove-orphans` 若被照搬到共享项目，也可能清除另一任务依赖的资源。依据：[阶段二脚本](../scripts/test-phase2.sh)、[CI 清理步骤](../.github/workflows/integration-test.yml)。
4. **Docker 沙箱不等于已经具备 Compose 测试环境。** 官方 Docker provider 创建代理容器，支持额外挂载、用户组和网络；本项目测试还需要 Docker CLI、Compose、daemon 访问及正确的宿主机挂载路径。共享 Docker socket 后，业务资源仍处在同一 daemon 上，必须自行设计隔离；不应作为首次试用的默认配置。[Docker provider 源码][docker-source]、[项目 Compose 配置](../docker-compose.yml)

如果需要并行集成测试，建议每任务使用独立 daemon/VM，或同时隔离 Compose 项目名、卷、网络、宿主机端口、构建镜像标签与清理范围，并验证 bind mount 指向对应工作树。当前更便宜的方案是：代理并行修改独立分支，集成验证串行执行。

## 建议的最小试用范围

1. 选一个边界明确的文档或小型代码维护任务；编排代码独立放在开发工具目录，固定包版本、锁文件和 Node 版本，不加入业务运行链路。
2. 只启用一个代理，显式选择命名 `branch`；优先不授予宿主机 Docker daemon 访问。先验证代码改动、提交回收、日志和认证能否稳定工作，再讨论并行。
3. 沙箱内先做静态检查或明确筛选的本地 fixture 用例。现有 `unittest discover` **不是完整离线测试集**：[`reasoner/test_core.py`](../reasoner/test_core.py) 和 [`reasoner/test_cli.py`](../reasoner/test_cli.py) 同时包含真实 Ontop 依赖用例与本地 HTTP fixture 用例，不应整套直接搬入无服务沙箱。
4. 最终仍运行 [CI](../.github/workflows/integration-test.yml) 已有的 Compose、SQL/SPARQL、阶段二对照实验与核心/CLI 验证，审查通过后合并。对文档任务按影响范围选择检查，不必启动整套服务。
5. 记录任务成功率、人工修正与审查耗时、代理及沙箱费用、环境故障次数；只有持续节省开发时间，才扩展到多代理和批量任务。

这是后续试用建议，本次未新增编排配置或依赖。是否值得正式引入，主要取决于将来是否持续出现足够多、验收明确且互不冲突的编码任务，而非项目是否使用 Python 或 Docker。

[getting-started]: https://github.com/mattpocock/sandcastle/blob/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8/docs/guide/getting-started.md
[package]: https://github.com/mattpocock/sandcastle/blob/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8/package.json
[agents]: https://github.com/mattpocock/sandcastle/blob/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8/docs/guide/agent-providers.md
[sandboxes]: https://github.com/mattpocock/sandcastle/blob/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8/docs/guide/sandbox-providers.md
[branches]: https://github.com/mattpocock/sandcastle/blob/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8/docs/guide/branch-strategies.md
[hooks]: https://github.com/mattpocock/sandcastle/blob/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8/docs/guide/hooks.md
[docker-source]: https://github.com/mattpocock/sandcastle/blob/ed8e7164e0a5d65a0a98c6917508b45cd2de89a8/src/sandboxes/docker.ts
