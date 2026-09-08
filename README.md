# Ontology + Ontop + SPARQL + MySQL 教学演示

用一个可运行的采购案例理解虚拟知识图谱（VKG）、本体类型推理和显式业务规则。数据始终保留在 MySQL：Ontop 通过固定 Mapping 把 SPARQL 确定性改写为 SQL；第二阶段再用一次性 RDFLib/owlrl 容器展示本体如何新增父类型事实，以及独立 SPARQL 规则如何据此产生复核结论。

| 阶段 | 要回答的问题 | 一条命令 |
| --- | --- | --- |
| 第一阶段 | SPARQL 如何经 Ontop 查询 MySQL？ | `bash scripts/test-sparql.sh` |
| 第二阶段 | 本体如何参与推理，规则如何产生结论？ | `bash scripts/demo-phase2.sh` |
| 完整验证 | 对照实验和回归测试是否全部通过？ | `bash scripts/test-phase2.sh` |

正常场景都只返回订单 `101 / 华北钢材`。第一阶段金额为 `50000.00`；第二阶段依据有效事件 E01 将订单 101 标记为 `NeedsReviewOrder`。

## 最快开始

只要求 Windows Docker Desktop、WSL 2 Ubuntu、Docker Desktop 的 WSL Integration，以及 WSL 中可用的 `docker compose`。推理依赖全部在容器中，不需要宿主机安装 Java、Maven、MySQL、Ontop、RDFLib 或 owlrl。

```bash
git clone https://github.com/zhangzhefang-github/ontology-vkg-demo.git
cd ontology-vkg-demo
bash scripts/demo-phase2.sh
bash scripts/test-phase2.sh
```

如果你已经在本仓库中，直接执行后两行。`demo-phase2.sh` 会自动启动服务、迁移已有数据卷、构建一次性推理镜像并输出“基础事实 → 类型推理 → 规则命中 → 解释”。

项目地图：

```text
ontology-vkg-demo/
├── docker-compose.yml          # MySQL、Ontop、一次性 reasoner
├── Dockerfile                  # Ontop + MySQL JDBC
├── Dockerfile.reasoner         # RDFLib + owlrl
├── sql/                        # 首次初始化和已有卷迁移
├── ontop/                      # 基础本体、OBDA Mapping、隔离的二阶段公理
├── queries/                    # SPARQL 基础事实查询和等价 SQL
├── rules/                      # 独立 SPARQL CONSTRUCT 业务规则
├── reasoner/                   # 内存图、OWL-RL、规则执行和证据输出
└── scripts/                    # 演示、迁移和自动测试入口
```

## 1. 各部分负责什么

```text
业务问题（由人理解和编写查询）
“已停用供应商名下尚未完成的采购订单”
        ↓ 本例把“尚未完成”定义为 PENDING
Ontology：PurchaseOrder → orderedFrom → Supplier
        ↓ 用这些业务词汇编写 SPARQL
SPARQL + Ontology + 固定 OBDA Mapping
        ↓ Ontop 确定性改写、优化
SQL：purchase_order JOIN supplier + 状态过滤
        ↓ JDBC → mysql:3306/procurement
MySQL 结果 → Ontop 返回带 URI 和类型的 SPARQL 结果
```

| 部分 | 职责 | 数据仓库视角 |
| --- | --- | --- |
| Ontology | 定义 Supplier、PurchaseOrder、属性和关系的业务含义及类型 | 统一业务术语和语义模型 |
| Mapping | 把表中每行和列绑定到业务对象 URI、属性和关系 | 数据库物理结构到语义模型的映射 |
| SPARQL | 用业务对象及关系表达筛选、关联、投影 | 查询语言，角色类似 SQL |
| Ontop | 结合本体和固定 Mapping，把 SPARQL 改写为 SQL 并通过 JDBC 查询 | 查询改写和执行中间层 |
| MySQL | 存放和计算真实业务数据 | 关系数据库 |

**SPARQL 是查询语言，不需要单独安装。** Ontop 根据固定 Mapping 将 SPARQL 确定性改写为 SQL。大模型没有参与本项目的查询生成，也没有自然语言自动解析接口。没有接入向量数据库、图数据库或复杂规则引擎。

本体声明 `PurchaseOrder orderedFrom Supplier`，但不会凭空发现数据库外键，也不会自动推断“停用供应商的订单就是风险订单”。外键关联由 Mapping 实现，风险筛选由查询显式表达。这个两表案例用于学习工作原理，直接写 SQL 同样简单；真实价值主要出现在跨系统和统一业务语义场景。跨系统仍需设计实际的联合查询/数据访问架构，本项目没有实现多库联邦。

## 2. 文件与版本

| 文件 | 作用 |
| --- | --- |
| `docker-compose.yml` | MySQL、Ontop、持久卷、端口、健康检查和启动依赖 |
| `Dockerfile` | 在官方 Ontop 镜像中补充 MySQL JDBC 驱动，校验 SHA-256 |
| `sql/init.sql` | 新数据卷初始化三张表、主外键和演示数据，给 Ontop 只读权限 |
| `sql/migrate-phase2.sql` | 幂等升级已有数据卷；保留现有订单和供应商数据 |
| `ontop/ontology.ttl` | 使用 RDF、RDFS、OWL、XSD 描述业务模型 |
| `ontop/mapping.obda` | 供应商、订单、事件三组基础事实映射，无业务规则结论 |
| `ontop/phase2-extension.ttl` | 只由推理容器加载的类型继承公理 |
| `ontop/ontop.properties` | JDBC 驱动类、容器内连接地址和演示账号 |
| `queries/risky-orders.rq` | 查询 DISABLED 供应商的 PENDING 订单 |
| `queries/risky-orders.sql` | 等价 SQL，便于独立对照 |
| `queries/phase2-base-facts.rq` | 从 Ontop 导出订单、供应商、事件基础事实 |
| `queries/needs-review-orders.sql` | 第二阶段事件判断的等价 SQL |
| `rules/R-01-needs-review.rq` | 独立维护的 SPARQL CONSTRUCT 业务规则 |
| `reasoner/run.py` | 取数、内存图、OWL-RL、规则执行、证据组织与退出码 |
| `Dockerfile.reasoner` | 一次性 RDFLib/owlrl 推理容器 |
| `scripts/migrate-phase2.sh` | 对已有卷执行迁移并让 Ontop 重新加载元数据 |
| `scripts/demo-phase2.sh` | 第二阶段的一条演示命令 |
| `scripts/test-phase2.sh` | 四组对照实验和 SQL 独立对照 |
| `scripts/test-sql.sh` | 执行 SQL 并断言只返回指定的一行 |
| `scripts/test-sparql.sh` | 等待健康、请求 HTTP Endpoint、重跑 SQL 并比较 |
| `scripts/verify-results.py` | 用 Python 标准库解析 JSON，校验 URI/类型和金额并比较结果 |
| `.gitattributes` | 保证脚本等文本使用 LF，避免 Windows CRLF 问题 |
| `.dockerignore` | 最小构建上下文，避免发送无关文件 |
| `.gitignore` | 排除本地结果、日志、驱动等生成文件 |
| `.artifacts/` | 测试生成的 SQL TSV、SPARQL JSON 等，不提交 Git |

固定版本：

- MySQL 官方镜像：`mysql:8.4.8`（8.4 LTS 系列）。
- Ontop 官方镜像：`ontop/ontop:5.5.0`。
- MySQL Connector/J：`8.4.0`；构建从 Maven Central 下载 JAR，无需安装 Maven。
- 本地构建产物：`ontology-vkg-demo-ontop:5.5.0-mysql8.4.0`。
- 推理基础镜像：`python:3.12.11-slim-bookworm`。
- 推理依赖：`rdflib==7.1.4`、`owlrl==7.1.4`、`pyparsing==3.3.2`。

Ontop 镜像原生支持 `/opt/ontop/jdbc/*`，Dockerfile 将驱动加入该目录，保留官方入口、非 root 用户和健康检查脚本。构建时显式设 JDBC 目录为 0755、JAR 为 0644，保证非 root 用户可遍历目录和读取驱动。配置目录只读挂载到 `/opt/ontop/input`，通过 `ONTOP_ONTOLOGY_FILE`、`ONTOP_MAPPING_FILE`、`ONTOP_PROPERTIES_FILE` 指定三个输入文件。这些路径和变量来自 [Ontop 官方 Docker 教程](https://ontop-vkg.org/tutorial/endpoint/endpoint-docker.html) 和 [5.5.0 镜像源代码](https://github.com/ontop/ontop/tree/ontop-5.5.0/client/docker)。

## 3. 启动

在 **WSL Ubuntu 终端**进入本仓库。先启动 Docker Desktop，并启用该 Ubuntu 发行版的 WSL Integration。无需本机 Java、Maven、MySQL 或 Ontop。第一阶段的独立 HTTP 结果比较脚本使用 Bash、curl、Python 3（仅标准库，无需 pip）；Ubuntu 缺少后两者时可执行 `sudo apt-get update && sudo apt-get install -y curl python3`。第二阶段的 RDF 推理 Python 环境完全位于容器中。

```bash
docker version
docker compose version
docker compose config
docker compose up -d --build
docker compose ps
# 等待两个服务健康；首次初始化可能需一两分钟
docker compose up -d --wait --wait-timeout 240
```

第一次构建需要联网访问 Docker Hub 和 Maven Central。MySQL 的健康检查使用应用账号通过 TCP 实际读取初始化后的两张表，避免临时初始化服务器造成误判。Ontop 等 MySQL 健康后才启动，继承官方健康检查并启用 `ONTOP_HEALTHCHECK=query`，用读取映射实体类型的 SPARQL 探测。官方探测脚本直接拼接 GET URL，因此本例使用不含 `#` 的查询，避免 URI 片段截断查询。

Windows 浏览器入口：

- 页面：<http://localhost:8080/>
- SPARQL Endpoint：<http://localhost:8080/sparql>
- 可选数据库客户端：`127.0.0.1:13306`，库 `procurement`，用户 `ontop`，密码 `demo_ontop_password`。

容器之间始终使用 `mysql:3306`。`13306` 仅供宿主机访问；Ontop 的 `localhost` 代表它自己的容器。

本项目使用固定演示密码和本地 HTTP，端口仅绑定 `127.0.0.1`。为便于学习开启了开发模式及 debug 日志；这是本地演示配置。

## 4. 数据、URI 和类型

| supplier_id | supplier_name | supplier_status |
| --- | --- | --- |
| 1 | 华北钢材 | DISABLED |
| 2 | 东方电子 | ACTIVE |

| order_id | supplier_id | amount | order_status |
| --- | --- | --- | --- |
| 101 | 1 | 50000.00 | PENDING |
| 102 | 1 | 30000.00 | COMPLETED |
| 103 | 2 | 20000.00 | PENDING |

- 业务词汇命名空间：`https://example.org/procurement#`。
- 订单 101 的 URI：`https://example.org/purchase-order/101`。
- 供应商 1 的 URI：`https://example.org/supplier/1`。
- `orderedFrom` 对象 URI 用订单表的 `supplier_id` 构造，与供应商映射完全一致。
- 名称和状态为 `xsd:string`，金额为 `xsd:decimal`。状态数据不带语言标签，查询用匹配的字符串类型。
- URI 是对象标识，不要求其 HTTP 地址能打开。标签 `@zh` 仅用于本体说明。
- MySQL 使用 utf8mb4 保留中文，二进制排序规则避免状态大小写比较与 RDF 字符串语义不一致。

## 5. 执行 SQL

```bash
bash scripts/test-sql.sh
```

执行的 SQL 位于 `queries/risky-orders.sql`：

```sql
SELECT po.order_id, s.supplier_name, po.amount
FROM purchase_order AS po
JOIN supplier AS s ON po.supplier_id = s.supplier_id
WHERE s.supplier_status = 'DISABLED'
  AND po.order_status = 'PENDING'
ORDER BY po.order_id;
```

脚本用 MySQL 容器自带的客户端执行，宿主机无需安装客户端。预期：

```text
order_id  supplier_name  amount
101       华北钢材        50000.00
SQL PASS（唯一结果：订单 101）
```

## 6. 执行 SPARQL 并比较

```bash
bash scripts/test-sparql.sh
```

脚本通过 `/sparql` 请求 `queries/risky-orders.rq`，并重新执行 SQL。只有两边都恰好返回订单 101、华北钢材、金额 50000，且 URI 与 RDF 类型正确，才输出 `SQL结果 = SPARQL结果：PASS`；空结果、多余行、错误类型和 HTTP 错误都会失败。金额用 `Decimal` 比较，避免浮点误差。

原始结果分别保存为 `.artifacts/sql.tsv` 和 `.artifacts/sparql.json`。SPARQL 返回的是订单 URI，比较时校验 URI 前缀后取订单号；金额 `50000` 与 `50000.00` 按数值比较。

也可单独执行 HTTP 请求：

```bash
curl --fail-with-body --silent --show-error \
  -H 'Accept: application/sparql-results+json' \
  --data-urlencode 'query@queries/risky-orders.rq' \
  http://localhost:8080/sparql
```

或者在首页查询编辑器粘贴 `.rq` 文件内容。订单 102 因为 `COMPLETED` 被排除；订单 103 因为其供应商 `ACTIVE` 被排除。

## 7. 查看 Ontop 生成的 SQL

本例开启 `ONTOP_DEV_MODE=true`，因此可通过官方开发接口查看实际改写文本（这一步本身不等同于执行查询）：

```bash
mkdir -p .artifacts
curl --fail-with-body --silent --show-error \
  --data-urlencode 'query@queries/risky-orders.rq' \
  http://localhost:8080/ontop/reformulate \
  | tee .artifacts/reformulated.txt
```

5.5.0 的该接口返回包含 `CONSTRUCT` / `NATIVE` 的查询计划，SQL 位于 `NATIVE` 节点下；整个响应不是可以直接送给 MySQL 的纯 SQL 文件。

同时设置了 `ONTOP_LOG_LEVEL=debug`，执行 SPARQL 后查看日志中的 SQL 与查询执行信息：

```bash
docker compose logs --tail=200 ontop
docker compose logs -f ontop
# 保存完整日志后搜索（grep 是普通文本搜索工具）
docker compose logs --no-color ontop > .artifacts/ontop.log
grep -n -A 20 -B 5 -E 'SQL|Native|SELECT' .artifacts/ontop.log
```

SQL 的别名、投影和 URI 构造表达式可能与手写 SQL 不同；关键是数据库关联、状态条件与返回值一致。接口和日志设置见 [Ontop CLI 官方说明](https://ontop-vkg.org/guide/cli)。

## 8. 停止、重启与清理

```bash
# 停止但保留容器和数据
docker compose stop
# 重新启动
docker compose up -d
# 删除本项目容器和网络，保留数据库卷
docker compose down
# 彻底重置本演示：同时删除数据库数据卷（数据会丢失）
docker compose down -v
# 重新运行 init.sql，恢复最初的测试数据
docker compose up -d --build
```

`init.sql` **只在空数据卷首次启动时执行**。修改 SQL 文件后重启已有数据库不会重新导入。Ontop 输入配置变动后可执行 `docker compose restart ontop`；驱动或 Dockerfile 变动后需重新 build。

## 9. 常见错误

| 现象 | 排查与处理 |
| --- | --- |
| 无法连接 Docker daemon | 确认 Docker Desktop 已启动、WSL Integration 已启用，用 `docker version` 确认 Server 可达 |
| `docker: unknown command: docker compose` | 检查 Docker Desktop WSL Integration 及 PATH；运行 `docker compose version`，本项目使用 Compose 插件命令 |
| Docker socket permission denied | 检查 WSL Integration 与当前用户的 Docker socket 访问权限，重新打开 WSL 终端 |
| 拉取镜像超时、EOF、DNS 或 TLS 错误 | 检查 Docker Desktop 代理、网络和镜像仓库配置；分别执行 `docker pull mysql:8.4.8`、`docker pull ontop/ontop:5.5.0` 定位 |
| Maven Central 下载失败 | 检查构建器访问 `repo.maven.apache.org` 的网络；恢复网络后重跑 build，不要关闭 checksum 校验 |
| `port is already allocated` | 检查 8080/13306 占用；可 `export ONTOP_PORT=18080 MYSQL_PORT=23306` 后启动，Endpoint 改为 18080 |
| MySQL unhealthy / Access denied | `docker compose logs mysql`；检查初始化是否完成、账号是否与 properties 一致；旧卷不会应用新密码或 init.sql |
| `No suitable driver` / 驱动类找不到 | 检查 Dockerfile 是否构建，`docker compose exec ontop ls -l /opt/ontop/jdbc`，应有 Connector/J 8.4.0 |
| `Communications link failure` | JDBC 主机必须是 `mysql`、端口 3306；检查 MySQL 健康状态和 Compose 网络 |
| Mapping/ontology 解析失败 | 查看 `docker compose logs ontop` 指向的具体行，核对命名空间、列名、类型及 OBDA 映射间空行 |
| 查询为空或结果不符 | 先跑 SQL；检查数据状态，再检查两处供应商 URI 模板是否一致、状态是否为不带语言标签的字符串 |
| `/ontop/reformulate` 不可用 | 确认 `ONTOP_DEV_MODE=true` 并重新创建 Ontop 容器 |
| Windows 页面打不开，WSL 能访问 | 检查 Docker Desktop 端口转发、本机防火墙和 WSL localhost 转发；确认浏览器用宿主机端口 |
| 脚本报 `bash\r` 或权限错误 | 在 WSL 用 `bash scripts/test-sql.sh` 执行；保持 LF 换行，`.gitattributes` 已配置 |

更多规范：[OBDA Mapping 官方语法](https://ontop-vkg.org/guide/advanced/mapping-language.html)、[Ontop 5.5.0 发布](https://github.com/ontop/ontop/releases/tag/ontop-5.5.0)、[MySQL 官方镜像](https://hub.docker.com/_/mysql)。


## 10. 本次实际验证

2026-09-08，在已启动的 Docker Desktop 上完成：

- `docker compose config`：通过。
- `docker compose up -d --build`：构建和启动成功。
- `docker compose ps`：MySQL 和 Ontop 均为 `healthy`。
- `bash scripts/test-sql.sh`：唯一结果 `101 / 华北钢材 / 50000.00`。
- `bash scripts/test-sparql.sh`：唯一订单 URI `https://example.org/purchase-order/101`，名称 `华北钢材`，金额 `50000.00`（`xsd:decimal`）；与 SQL 一致。
- `/ontop/reformulate`：实际返回查询计划及如下 SQL 核心部分（保留原始别名）：

```sql
FROM `purchase_order` v1, `supplier` v2
WHERE (v1.`supplier_id` = v2.`supplier_id`
  AND 'PENDING' = v1.`order_status`
  AND 'DISABLED' = v2.`supplier_status`)
```

实际排查并修复了 JDBC 目录权限不足和健康检查 URL 的 `#` 截断问题。官方镜像内 Tomcat/JRE 会输出 reflective-access 与 canonical cache 提示，本次未阻止启动和查询；没有未解决的功能阻塞。测试原始输出保存在本地 `.artifacts/`，可重新运行脚本复验。

## 11. 第二阶段：本体推理和显式规则

第二阶段新增 `supply_disruption_event` 表。它保存事件 ID、实际受影响供应商、事件具体类型和当前是否有效。演示数据是：

| event_id | affected_supplier_id | event_type | is_current |
| --- | ---: | --- | ---: |
| E01 | 1 | DeliverySuspensionEvent | 1 |

`supplier_status=DISABLED` 仍只表示系统停用，第二阶段规则完全不使用它来判断供应中断。判断依据是明确的当前有效事件。

第二阶段的数据链路是：

```text
MySQL 当前数据
  → Ontop Mapping + phase2-base-facts.rq 导出基础 RDF
  → 新建空的 RDFLib 内存图
  → 加载 phase2-extension.ttl
  → owlrl 前向推理出事件的父类型
  → 独立执行 R-01-needs-review.rq
  → 输出 NeedsReviewOrder 和本次规则绑定证据
```

本体只负责类型语义：

```turtle
:DeliverySuspensionEvent rdfs:subClassOf :SupplyDisruptionEvent .
```

MySQL 和 Ontop 的基础事实只说 `E01 rdf:type DeliverySuspensionEvent`。继承公理不在 Ontop 加载的 `ontology.ttl` 中，而在 `phase2-extension.ttl` 中，仅由一次性推理容器加载。因此正常运行时，父类型只能由 owlrl 在内存图中产生。

业务判断由独立的 SPARQL CONSTRUCT 规则 R-01 负责：PENDING 订单实际采购自供应商 S，同时存在当前有效的 `SupplyDisruptionEvent` 影响 S，才构造 `order rdf:type NeedsReviewOrder`。规则还构造一个只存在于内存图中的 `RuleApplication`，记录 R-01、订单、事件和供应商绑定。Python 没有用 `if order_id == 101` 或等价业务分支实现规则。

实现方式准确地说是 **OWL-RL 本体推理 + 显式 SPARQL 规则执行**。本项目没有部署 Datalog 引擎。`NeedsReviewOrder` 不写回 MySQL，也不会进入下一次运行。

## 12. 升级已有数据库卷

修改 `init.sql` 不会作用到已有 MySQL 卷。使用下面的幂等迁移，无需删卷：

```bash
docker compose up -d mysql
bash scripts/migrate-phase2.sh
```

迁移使用 `CREATE TABLE IF NOT EXISTS`，并以主键 E01 做可重复插入。已有供应商和订单不会被删除。迁移脚本随后重新创建 Ontop 容器，使它重新读取数据库元数据和更新后的 Mapping。

## 13. 一条命令观看完整演示

```bash
bash scripts/demo-phase2.sh
```

输出依次展示：

1. Ontop 导出的基础事实，包括 E01 的直接类型、影响关系、有效状态，以及三个订单的状态和采购方；
2. owlrl 新增的 `E01 rdf:type SupplyDisruptionEvent`；
3. R-01 的实际绑定 `order=101, event=E01, supplier=.../supplier/1`；
4. 最终 `NeedsReviewOrder` 及依据。

解释文字由实际图中的订单状态、供应商名称、事件类型、有效性和规则绑定组织出来。正常证据示例：

```text
订单101需要供应风险复核（不是“必然无法交付”）。
  事件E01直接类型: DeliverySuspensionEvent
  本体公理推出: SupplyDisruptionEvent
  事件当前有效=True，影响供应商=华北钢材
  订单状态=PENDING，实际采购自=华北钢材
  命中规则=R-01
```

未命中只能说明 R-01 没有产生结论，不能解释为“订单安全”。订单 102 因为状态是 COMPLETED 不命中；订单 103 虽为 PENDING，但实际采购自东方电子，E01 影响的是华北钢材，所以不命中。

## 14. 自动化对照实验

```bash
bash scripts/test-phase2.sh
```

脚本依次验证：

1. 正常推理只得到订单 101，并用等价 SQL 独立得到 `101 / 华北钢材 / E01`；
2. 从本次运行的内存本体副本移除唯一继承公理，基础事实和规则不变；脚本先确认基础事实仍有 E01 的直接类型且没有父类型，然后确认 R-01 不命中；
3. 保持供应商 DISABLED 不变，把 E01 暂时设为无效，从空图重新取数和推理；SQL 与规则均为空；
4. 恢复 E01，从空图再次计算；SQL 和规则重新得到订单 101。

事件状态修改受 shell `trap` 保护，成功、测试失败或中断时都会尝试恢复 E01 为有效。测试证据保存在 `.artifacts/phase2/normal.txt`、`no-inheritance.txt`、`inactive-event.txt` 和 `restored.txt`。

推理程序区分退出状态：`2` 表示 Ontop 服务异常，`3` 表示请求成功但基础事实为空，`4` 表示实验断言失败（包括该命中却没有命中），`5` 表示其他推理程序错误。正常的反事实“不命中”在测试明确期望为空时返回 0。

## 15. 全量重算、证明范围与 SQL 对照

每次 `docker compose run --rm reasoner` 都新建容器和空内存图，重新从 Ontop 获取当前基础事实，再加载公理、运行 owlrl 和 R-01。它是全量重算，不是增量推理，也不是增量撤回算法。事件失效后没有旧的派生事实可残留。

RDFLib/owlrl 不为本项目原生提供可直接使用的通用证明记录器。本 Demo 因此做两件可核验证据：保存并检查实际加载的那条继承公理；由 R-01 同时构造 `RuleApplication`，保存该次规则的订单、事件、供应商绑定。它足够解释这个固定 Demo，但不应宣称具备任意 OWL 公理和任意规则的自动证明追踪能力。

等价 SQL 在 `queries/needs-review-orders.sql`。SQL 同样能完成当前判断，而且对这个三表小案例更直接。SQL 把已知具体事件类型 `DeliverySuspensionEvent` 写进过滤条件；本体实验则让规则只依赖抽象的 `SupplyDisruptionEvent`，新增符合该父类的事件类型时可以在本体层维护分类关系。移除本体公理的实验验证规则对类型语义的依赖，不要求固定 SQL 跟着改变。

本实验只证明：本体确实参与了计算，业务规则可以和取数 Mapping 分开维护。它不证明本体方案必然比 SQL 更快、更准确或更省成本。

增加的维护成本包括：事件数据质量和类型编码、稳定 URI 约定、Mapping、本体版本、规则版本、推理依赖和镜像、全量推理时间、证据与回归测试、变更时 Ontop 元数据重载。只有当多个数据源需要共享业务分类、多种具体事件要复用同一抽象规则、语义由跨团队统一治理，或规则需要独立于表结构演进时，这些投入才更可能值得。单库、少表、少规则且由一个团队维护时，SQL 通常更简单。

依赖依据：[RDFLib 文档](https://rdflib.readthedocs.io/en/stable/)、[RDFLib 7.1.4](https://pypi.org/project/rdflib/7.1.4/)、[owlrl 7.1.4](https://pypi.org/project/owlrl/7.1.4/)。owlrl 7.1.4 官方包说明其唯一依赖为 RDFLib 7.1.3 或更高，并支持 Python 3.9–3.13；本项目固定 RDFLib 7.1.4 和 Python 3.12.11。

## 16. 第二阶段实际验证结果

2026-09-08 实际完成：

- `docker compose config --quiet`：通过；MySQL 和 Ontop 均为 healthy；
- 第一阶段 `test-sql.sh`、`test-sparql.sh`：继续通过，仍只有订单 101；
- 正常 owlrl + R-01：E01 父类型由本体新增，只命中订单 101；
- 移除继承公理：E01 直接类型仍在，父类型与规则结论均消失；
- E01 失效：供应商仍为 DISABLED，SQL 与规则均无结果；
- E01 恢复：SQL 与规则均再次命中订单 101；
- 数据恢复核验：E01 最终 `is_current=1`；数据库中没有保存 NeedsReviewOrder 表。
