# 供应风险复核决策闭环：技术选型

调研日期：2026-11-13。范围是 [#6](https://github.com/zhangzhefang-github/ontology-vkg-demo/issues/6) 及其子任务 #7–#13 的实施准备。以下是基于官方资料、固定版本源码和本仓库约束的推荐，不把资料支持等同于闭环验收通过。版本在调研当天通过 PyPI JSON 元数据核实；正式实现时锁定全部传递依赖及制品哈希。

## 推荐

| 用途 | 选择 | 理由与边界 |
| --- | --- | --- |
| 语言与运行 | 沿用 Python 3.12，新增独立容器 | 与现有推理核心兼容；本轮不把新依赖装入现有推理镜像 |
| MCP | 官方 `mcp==2.3.0`，使用 `MCPServer`，stdio | 已发布稳定版本，支持 Python 3.12；两个工具足够，不需要另加 MCP 框架 |
| MySQL 客户端 | `PyMySQL[rsa]==1.2.4` | DB-API 同步客户端，显式 SQL 和事务容易对应本规格的锁顺序；包含 SHA-2 认证所需可选依赖 |
| 数据持久化 | 沿用 MySQL 8.4.8、InnoDB；同实例的两个 schema | 业务库和决策库隔离权限，同时保持一连接跨 schema 的本地事务与外键 |
| 并发模型 | 同步模块操作、MCP 同步工具函数；每次操作独占连接 | 官方 SDK v2 会在线程中调用同步工具函数；CLI 直接调用同一模块 |
| CLI / 测试 | 沿用标准库 `argparse`、`unittest`；真实 MySQL + Ontop 验收 | 不为参数解析和 MCP 帧另建业务测试缝 |
| 业务推理 | 保留 RDFLib 7.1.4、OWL-RL 7.1.4、pyparsing 3.3.2 与已有 R-01 | 新闭环复用现有计算，不将规则移到 Python 或 SQL |

本仓库已有依赖和镜像见 [现有推理依赖](../requirements-reasoner.txt)、[推理镜像](../Dockerfile.reasoner)、[Compose](../docker-compose.yml)。保留它们是本项目范围选择，不表示本次审核了其所有漏洞或性能。

## MCP：使用官方 SDK v2，避免沿用旧版示例

PyPI `mcp` 2.3.0 于 2026-10-02 上传，包要求 Python >=3.10，标注 MIT；固定 tag 的元数据明确列出 Python 3.12。此次按该版本选型，不依赖不断变化的主分支最低版本声明。[PyPI 2.3.0](https://pypi.org/project/mcp/2.3.0/)、[固定版本元数据](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/pyproject.toml)

采用 `from mcp.server import MCPServer`、`@server.tool()` 与 `server.run(transport="stdio")`。v2 将 SDK 中的 `FastMCP` 改名为 `MCPServer`；旧 `mcp.server.fastmcp` 导入不能直接复用。SDK 的 `cli` extra 是开发和安装工具的附加依赖，容器直接运行自己的 Python 入口时不需要安装。[固定版本快速示例](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/README.md)、[v2 迁移说明](https://py.sdk.modelcontextprotocol.io/migration/)、[stdio 运行实现](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py)

v2 的同步工具函数通过 `anyio.to_thread.run_sync` 执行。因此推荐工具函数为普通 `def`，在其内部调用同步决策操作；不要写一个 `async def` 工具后直接调用同步 MySQL、HTTP 拉取和推理计算。前者适配当前代码，无需另加一层线程切换；线程隔离避免阻塞事件循环，但不承诺加速 CPU 密集的 OWL-RL 计算。[固定版本函数执行实现](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/utilities/func_metadata.py)

SDK v2 的版本变化还涉及类型字段、客户端握手和错误返回。实施 #7 时须对目标 Agent 客户端做一次实际 stdio 连接检查；可以用 SDK 的官方客户端确认枚举工具与调用。该检查是传输兼容性 smoke，不把 MCP 帧升级为新的业务测试缝。仅查询与提交建议注册成工具，不注册审批、执行或追溯操作。[客户端文档](https://py.sdk.modelcontextprotocol.io/client/)、[迁移说明](https://py.sdk.modelcontextprotocol.io/migration/)

独立包 `fastmcp` 4.1.8 于 2026-11-10 上传，Python >=3.10、Apache-2.0，由 PrefectHQ 维护；它与旧版官方 SDK 中名为 FastMCP 的接口不是同一个版本序列。它是有效候选，但本项目只需两个 stdio 工具，本次没有证明其额外能力能抵消新增框架和迁移认知成本，因此推荐直接使用官方 SDK。这是对本项目规模的工程判断，不是对两个框架做出的性能结论。[FastMCP 发布资料](https://pypi.org/project/fastmcp/4.1.8/)、[维护方源码](https://github.com/PrefectHQ/fastmcp)

## MySQL：PyMySQL 与显式 SQL

| 候选 | 官方资料确认 | 本项目取舍 |
| --- | --- | --- |
| PyMySQL 1.2.4 | 2026-11-09 上传；Python >=3.9；MIT；纯 Python DB-API 客户端 | 推荐。适合少量明确 SQL、列级权限和手动事务控制；没有本项目性能瓶颈证据要求 C 驱动 |
| MySQL Connector/Python 26.10.0 | 2026-10-22 上传；Python >=3.10；GPLv2 + FOSS License Exception；Oracle 维护，支持 SHA-2 认证 | 可行备选；官方维护身份不自动意味着更适合本演示。选它时需单独核对许可证义务；本研究不作法律判断 |
| mysqlclient 2.3.1 | 2026-11-10 上传；Python >=3.10；GPL-2.0-or-later；涉及 C 客户端与构建依赖 | 当前没有吞吐量需求支撑增加 slim Linux 镜像的本机构建/客户端库依赖，暂不采用 |

资料：[PyMySQL 1.2.4](https://pypi.org/project/PyMySQL/1.2.4/)、[Connector/Python 26.10.0](https://pypi.org/project/mysql-connector-python/26.10.0/)、[Connector 连接参数](https://dev.mysql.com/doc/connector-python/en/connector-python-connectargs.html)、[mysqlclient 2.3.1](https://pypi.org/project/mysqlclient/2.3.1/)、[mysqlclient Linux 安装说明](https://github.com/PyMySQL/mysqlclient/blob/main/README.md)。未执行三者的本项目性能比较。

PyMySQL 提供 `begin()`、`commit()`、`rollback()` 和可配置的连接、读、写超时。连接显式设置 `autocommit=False`、`charset="utf8mb4"`，超时使用配置项；事务由操作控制，异常时显式回滚并关闭连接，不依赖上下文管理器隐含提交。[连接接口](https://pymysql.readthedocs.io/en/latest/modules/connections.html)

值通过 `cursor.execute(sql, params)` 绑定，使用 `%s` 或命名占位符，不用字符串拼接业务输入；这不等于服务端 prepared statement。表名、列名和锁子句由实现固定，不接受 Agent 指定。不要把展开后的 SQL、令牌或数据库密码写入日志。[游标接口](https://pymysql.readthedocs.io/en/latest/modules/cursors.html)

PyMySQL 的 `threadsafety=1` 表示模块可共享、连接不能在并发线程间共享。推荐每个模块操作自行取得、使用并关闭连接，不把活跃连接放在 MCP 全局变量里；第一版无需连接池。批准和执行是先后两个事务，即便同属一次批准调用，也必须先提交批准，再重新复核并开启执行事务。[固定版本声明](https://github.com/PyMySQL/PyMySQL/blob/v1.2.4/pymysql/__init__.py)、[DB-API 线程安全定义](https://peps.python.org/pep-0249/#threadsafety)、[#6 事务要求](https://github.com/zhangzhefang-github/ontology-vkg-demo/issues/6)

### 默认认证与依赖锁定

MySQL 8.4 的默认认证为 `caching_sha2_password`；完整认证需要安全连接或支持 RSA 密码交换。PyMySQL 官方要求为 SHA-2 认证安装 `rsa` extra；1.2.4 的该 extra 依赖 `cryptography>=46.0.7`。不要为了省依赖把 MySQL 改回旧认证插件，也不要只凭热缓存登录成功就判断首次认证可用。[MySQL SHA-2 认证](https://dev.mysql.com/doc/refman/8.4/en/caching-sha2-pluggable-authentication.html)、[PyMySQL 安装说明](https://pymysql.readthedocs.io/en/latest/user/installation.html)、[1.2.4 可选依赖](https://github.com/PyMySQL/PyMySQL/blob/v1.2.4/pyproject.toml)

建议新镜像的直接依赖起点如下，保留原推理依赖；这只是选型输入，不是完整锁文件：

```text
mcp==2.3.0
PyMySQL[rsa]==1.2.4
rdflib==7.1.4
owlrl==7.1.4
pyparsing==3.3.2
```

在目标 Python 3.12 slim Linux 平台解析依赖后，提交全量版本与哈希锁定；包含 `cryptography`、AnyIO、Pydantic、`mcp-types` 及 SDK 其他传递依赖。若未来自己的代码直接调用 AnyIO，应将其列为直接依赖并锁定。调研时 `cryptography` 最新稳定版为 50.0.2，AnyIO 为 4.16.0，但不把“最新”当作跨平台完整安装已验证的证据。[cryptography 发布资料](https://pypi.org/project/cryptography/50.0.2/)、[AnyIO 发布资料](https://pypi.org/project/anyio/4.16.0/)、[MCP 包依赖元数据](https://pypi.org/pypi/mcp/2.3.0/json)

## 事务、权限和部署约束

以下是针对 #6 的实施建议；其中锁行为来自 MySQL，状态和锁顺序来自规格，不能仅靠换驱动获得。

- **事务内锁定后再判断。** 先按主键对决策 `FOR UPDATE`，再依次对订单、事件 `FOR SHARE`，在持锁期间比对并写入，锁保持到事务结束。共享锁允许源表只有 SELECT 权限；排他锁还要求 UPDATE、DELETE 或 LOCK TABLES 之一。审批账号只授予允许状态列的 UPDATE，不授予证据 UPDATE，本次一次性 MySQL 8.4.8 容器已验证只授予状态列 UPDATE 也可执行 `SELECT * ... FOR UPDATE`，证据列 UPDATE 仍被拒绝；最终模式及账号仍需验收。不要为使测试通过而授予源表写权限。[MySQL 锁定读](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)、[#6](https://github.com/zhangzhefang-github/ontology-vkg-demo/issues/6)
- **可显式选择 READ COMMITTED。** InnoDB 默认是 REPEATABLE READ；本项目按唯一编号锁定现有行，以锁定读和哈希复核保证正确性。推荐 RC 使事务行为明确并减少不必要的间隙锁；它不是规格强制条件，也不能替代唯一约束、固定锁序和完整重试。死锁、锁等待超时应按技术失败处理，不能记成业务拒绝或业务失效。[隔离级别](https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-isolation-levels.html)
- **两个库指同实例的两个 schema。** `decision_loop` 与 `procurement` 的相关表都使用 InnoDB；同一连接使用限定表名写入，实现一次执行事务的原子提交，不引入跨实例事务或 XA。任务引用审批记录的跨 schema 外键、列类型、索引及初始化顺序必须真实验收；两库共同备份并按依赖恢复。[MySQL 事务](https://dev.mysql.com/doc/refman/8.4/en/commit.html)、[外键要求](https://dev.mysql.com/doc/refman/8.4/en/create-table-foreign-keys.html)、[#6](https://github.com/zhangzhefang-github/ontology-vkg-demo/issues/6)
- **一份操作实现、两个薄适配。** 身份校验、证据、事务、状态与错误语义集中在决策模块；MCP 只注册查询和提交建议，CLI 提供人工操作。公开测试面就是 #6 指定的模块操作；私有函数调用、SQL 文本、MCP 帧和 CLI 参数不作为新的业务断言面。
- **运行环境隔离要完整。** 新依赖在独立镜像；Compose 隔离须覆盖 project、卷、网络、宿主端口、镜像标签和清理范围。worktree 本身不隔离 Docker 资源。真实集成实验串行运行，管理账号只在测试夹具中模拟业务变化并恢复，不让新验收修改既有演示数据。[仓库已有环境评估](sandcastle-assessment.md)、[Compose 项目名](https://docs.docker.com/compose/how-tos/project-name/)、[#6](https://github.com/zhangzhefang-github/ontology-vkg-demo/issues/6)

这套方案不需要新增 ORM、HTTP 框架、消息队列、工作流框架或缓存。选择显式 SQL 是为了让列级授权、锁与事务和规格逐项对应，不是声称 ORM 做不到这些。

## 验证状态与实施检查项

已完成资料核实：读取 #6、仓库运行配置和依赖；查询官方文档、固定 tag 源码及 PyPI 发布元数据。

本次另做了两组可丢弃容器 smoke，由执行记录确认：

| 实验 | 结果 | 不能据此推断 |
| --- | --- | --- |
| 一次性现有推理镜像，Python 3.12.11 安装新增依赖 | `pip check` 为 `No broken requirements found`，导入 `mcp`、`pymysql`、`rdflib`、`owlrl` 成功；版本为 mcp 2.3.0、PyMySQL 1.2.4、cryptography 50.0.2、RDFLib 7.1.4、OWL-RL 7.1.4、pyparsing 3.3.2 | 未测试 MCP 握手、PyMySQL TCP 认证或完整业务运行 |
| 一次性 MySQL 8.4.8、无网络和端口暴露、无外部数据卷 | MySQL 客户端的最小权限用户凭 SELECT + `UPDATE(state)` 成功执行决策 `SELECT * ... FOR UPDATE`；源表仅 SELECT 可 `FOR SHARE`；RC 显式事务跨两个 schema 插入审批和带跨 schema 外键的任务后提交；写证据列被 1143 权限错误拒绝 | 未测试 PyMySQL 客户端、双连接竞态、完整闭环和最终 schema |

临时容器已清理，未修改既有业务库。这些结果降低基础安装与权限方案的不确定性，但不能代替以下验收。

实施时必须完成：

1. 在新的 Python 3.12 slim 镜像安装并锁定依赖，运行 `pip check`，导入 SDK、驱动和原推理库，核对全量版本；验证目标架构的 cryptography 安装。
2. 实际 stdio 握手、工具枚举和一次查询，只暴露两个授权工具；同步工具并发运行时不共享连接，应用日志不污染协议输出。
3. 使用未被认证缓存掩盖的新用户验证 SHA-2 登录；用 Agent 与审批最小权限账号分别证明允许操作可用、越权操作失败，证据不可更新、状态不能由 Agent 插入。
4. 真实 MySQL 验证跨 schema 外键及事务回滚；使用两个连接证明共享锁阻止并发业务写，决策排他锁串行化审批、执行，唯一约束兜住重复建议和任务。
5. 通过统一模块操作覆盖批准前失效、批准后阻止、技术失败后恢复、无关事实变化、双事件各一条任务、任务追溯和离线重放；不要以驱动 smoke 代替这些业务验收。
6. 按仓库贡献规范运行原有 SQL、SPARQL、第二阶段及核心/CLI 回归，并保存结果；新环境与原演示环境互不污染。

后续可从 #7 开始实施；若隔离安装或 stdio 互通发现真实不兼容，再据证据调整版本或选择，而不是预先增添第二套框架。
