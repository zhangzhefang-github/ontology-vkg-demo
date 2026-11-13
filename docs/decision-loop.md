# 供应风险复核决策闭环

本演示复用现有 R-01 推理计算。查询命中只表示订单需要供应风险复核，不表示无法交付；没有命中也不表示安全。模块操作是业务验收入口，MCP 和命令行只是适配器。

## 独立环境

新闭环使用独立镜像和 Compose 配置，不改变阶段二推理镜像。以下命令从仓库根目录执行；宿主机只需 Git 与 Docker Compose。项目名同时用于隔离镜像标签、网络、容器和卷，无宿主机端口发布。

```bash
export DECISION_PROJECT=ontology-vkg-decision-demo
export CODE_COMMIT=$(git rev-parse HEAD)
export DECISION_GIT_COMMON_DIR=$(git rev-parse --path-format=absolute --git-common-dir)
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

运行一次实际 stdio 握手、工具枚举与查询检查（工具列表应只有 `query_supply_review` 和 `submit_supply_review`，检查会提交或返回一条建议）：

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
      "env": {
        "DECISION_PROJECT": "ontology-vkg-decision-demo",
        "CODE_COMMIT": "替换为完整代码提交SHA",
        "DECISION_GIT_COMMON_DIR": "/absolute/path/to/common-git-dir"
      }
    }
  }
}
```

替换绝对路径、完整代码提交和 Git common-dir（上面的 Git 命令可获得），并保持项目名与启动命令一致。代码提交必填，容器只读挂载 Git 对象以核对实际核心文件与该提交是否一致；Git worktree 的 `.git` 是指针文件，因此不能直接作为对象目录挂载。MCP 仅提供查询和提交建议两个工具。审批、执行和追溯不注册为 Agent 工具。日志使用 stderr，不往协议 stdout 打印诊断。

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

## 提交建议与不可变证据

提交只点名订单、事件并可附说明，系统重新计算后生成待审批决策。Agent 说明标为不是证据；动作、采购员角色、描述和证据均由系统决定。重复或并发提交返回已有占位记录，不覆盖证据或说明，也不重新复核。查询只附带占位记录，不改变状态。

```bash
docker compose -f compose.decision.yml run --rm -T --entrypoint python decision \
  -c 'import json; from decision.service import DecisionService; print(json.dumps(DecisionService().submit("101", "E01", "请主管复核；说明不是证据"), ensure_ascii=False))'
```

证据包含完整基础事实图的排序 N-Triples、快照 SHA-256、规则应用、绑定业务事实、业务前提哈希、语义版本指纹、完整代码提交和两份本体及规则原文。空白节点、核心与代码提交不符、核心加载后改变或计算期间输入变更都拒绝创建；基础事实提前包含父类仍作为技术失败。事实哈希只用于完整性，不参与业务失效判断。

## 离线重放

审计查阅者用已保存的事实快照、两份本体原文和 R-01 原文复核该决策记录的规则应用。重放只比较是否再次得到相同的规则、订单和事件绑定。它不读取当前业务事实，不写业务表或决策表，也不需要审批结论或任务已经存在。Agent 说明标为不是证据，不参与计算。重放成功不能说明 Ontop 当时加载了保存的 Mapping；事实哈希只核对快照文本有没有被改。

恢复执行环境时使用证据里的 `code_commit`，以及 `semantic_fingerprint` 中的 `rdflib` 和 `owlrl` 版本。代码、依赖或保存原文与记录不一致时，命令以退出码 1 失败，并写明重放前提未满足。不要改用当前规则、当前本体或在线事实继续计算。

```bash
git checkout "$CODE_COMMIT"
python -m venv .venv
.venv/bin/pip install "rdflib==$RDFLIB_VERSION" "owlrl==$OWLRL_VERSION"
export DECISION_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
.venv/bin/python -m decision.replay decision.json
```

`CODE_COMMIT`、`RDFLIB_VERSION` 和 `OWLRL_VERSION` 取自该决策 JSON 的证据字段，不要取自当前工作区的依赖。`decision.json` 可以是提交建议返回的整条决策，也可以只是其中的 `evidence` 对象。成功时标准输出是一条 JSON，包含 `matched`、`rule_application` 和 `code_commit`；标准错误为空，退出码为 0。快照文本与 `facts_hash` 不一致，或计算结果里没有记录的那条绑定时，退出码为 1。

容器中的依赖由决策镜像按锁定版本安装。先用独立项目名启动 MySQL 和 Ontop，提交一条建议并把 JSON 存到宿主机，再停止 Ontop，用 `--no-deps` 离线重放。下面的项目名必须与已经在跑的决策环境区分开：

```bash
export DECISION_PROJECT=ontology-vkg-decision-replay
export CODE_COMMIT="$(git rev-parse HEAD)"
export DECISION_GIT_COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
docker compose -f compose.decision.yml up -d --build --wait mysql ontop
docker compose -f compose.decision.yml build decision
SNAPSHOT=/tmp/ontology-vkg-replay-input.json
docker compose -f compose.decision.yml run --rm -T --entrypoint python decision \
  -c 'import json; from decision.service import DecisionService; print(json.dumps(DecisionService().submit("101", "E01", "说明不是证据"), ensure_ascii=False))' \
  > "$SNAPSHOT"
docker compose -f compose.decision.yml stop ontop
docker compose -f compose.decision.yml run --no-deps --rm -T \
  -v "$SNAPSHOT:/tmp/replay-input.json:ro" \
  --entrypoint python decision -m decision.replay /tmp/replay-input.json
```

停止 Ontop 之后，上述重放仍应打印订单 101、事件 E01、规则 R-01。把快照文件里的事实文本改掉一个字符后再执行同一命令，应因完整性哈希失败而退出。把证据中的订单或事件改成未计算出来的值，应因未得到同一条规则应用而退出。这条命令不会重新连接 Ontop。确认结束后只清理本项目：

```bash
docker compose -f compose.decision.yml down --volumes --remove-orphans
```

只跑离线重放的模块测试时不需要启动容器。需要用真实 MySQL 和 Ontop 产生证据、再在断开推理端点的设置下重放时，在决策容器中设置 `REPLAY_LIVE=1`：

```bash
docker compose -f compose.decision.yml run --rm -T \
  -e REPLAY_LIVE=1 -e TEST_DB_PASSWORD=demo_root_password --entrypoint python decision \
  -B -m unittest decision.test_replay
```

## 查看与拒绝

采购主管用个人令牌查看证据并拒绝。展示、批准和拒绝共用同一身份校验：令牌哈希匹配，且用户是启用的采购主管。库内只有哈希，比较采用恒定时间；明文演示值只在 `decision/test_config.py`，不作为数据库列，也不进入应用或数据库日志。Agent 说明在结果里标为不是证据。

拒绝必须有非空理由，不先做失效复核。审批记录保存拒绝人、当时角色、理由和数据库服务器时间，写入后应用账号不能改，每条决策至多一条。拒绝后的决策保持已拒绝并继续占位，不能撤回或重开；同一订单和事件的后续建议返回这条记录。对已有终态再次批准或拒绝只返回原结论，不新增审批，也不触发执行。

命令行进程使用审批通道账号 `decision_manager`。查看和拒绝时，它读取用户与决策、插入审批记录，并只更新允许的决策状态列；不能改证据列，不能删除，也不能写供应商、订单和事件。MCP 进程仍只使用 `decision_agent`，工具仍只有查询和提交建议。下面的命令沿用本节前面的项目名、代码提交和 Git 目录。令牌从测试配置读入，避免出现在命令参数里。

```bash
docker compose -f compose.decision.yml run --rm -T \
  -e DECISION_DB_USER=decision_manager \
  -e DECISION_DB_PASSWORD=demo_manager_db_password \
  --entrypoint python decision -B -c '
from dataclasses import replace
from decision.cli import main
from decision.service import DecisionService, Settings
from decision.test_config import SUPERVISOR_TOKEN
agent = DecisionService(replace(Settings.from_env(), db_user="decision_agent", db_password="demo_agent_db_password"))
created = agent.submit("101", "E01", "请主管查看证据；这段说明不是证据")
decision_id = str(created["id"])
main(["show", "--id", decision_id, "--token", SUPERVISOR_TOKEN])
main(["reject", "--id", decision_id, "--token", SUPERVISOR_TOKEN, "--reason", "证据不足以采纳"])
main(["approve", "--id", decision_id, "--token", SUPERVISOR_TOKEN, "--comment", "不能改成批准"])
'
```

单独查看或拒绝时，把上面的 `decision_id` 换成已有编号，并只调用对应子命令：`python -m decision.cli show --id <编号> --token <令牌>` 或 `python -m decision.cli reject --id <编号> --token <令牌> --reason <理由>`。容器里的数据库账号必须是 `decision_manager`。

## 批准与执行

批准复用采购主管身份校验。事务外先做推理和版本检查；事务内先排他锁定决策并确认仍是待审批，再按订单、事件顺序加共享锁，比对绑定事实和业务前提后才写入。业务前提不一致时把决策记为已失效、保存原因并释放占位，不产生审批记录。检查没有做完则保持待审批。计算失败时，若绑定的订单行或事件行已经不存在，仍按业务失效处理。

批准事务只写审批记录并把执行状态置为未开始，提交之后才在另一个事务尝试执行。执行前再次在事务外重算指纹并推理，事务内用同一锁顺序重新比对。执行必须读到结论为批准的审批记录，且执行状态是未开始或执行失败。业务前提不再成立时保留已批准、把执行记为被阻止、释放占位且不创建任务。Ontop 不可达或基础事实提前含有父类时记为执行失败并保留占位。供应商停用、改名、订单金额以及其他订单或事件的变化不阻止执行。

任务写在采购库，只包含编号、来源决策、订单、事件、采购员角色、系统生成的供应风险复核描述和创建时间。它引用审批记录、订单和事件；同一来源决策至多一条任务。任务不带优先级、截止时间或审批意见，也不修改订单。每次实际执行尝试追加不可改记录。执行成功和执行被阻止是终态，再次调用不写库；执行失败可用只接收决策编号的命令重试，每次都完整复核。

```bash
docker compose -f compose.decision.yml run --rm -T \
  -e DECISION_DB_USER=decision_manager \
  -e DECISION_DB_PASSWORD=demo_manager_db_password \
  --entrypoint python decision -B -c '
from decision.cli import main
from decision.service import DecisionService, Settings
from decision.test_config import SUPERVISOR_TOKEN
from dataclasses import replace
agent = DecisionService(replace(Settings.from_env(), db_user="decision_agent", db_password="demo_agent_db_password"))
created = agent.submit("101", "E01", "请主管批准；这段说明不是证据")
decision_id = str(created["id"])
main(["approve", "--id", decision_id, "--token", SUPERVISOR_TOKEN, "--comment", "同意创建复核任务"])
main(["execute", "--id", decision_id])
'
```

上面的第二次 `execute` 返回已经创建的任务，不再写库。审批执行账号是 `decision_manager`：只能插入审批记录、执行尝试和任务，并更新决策状态、占位和失效原因；不能改证据，不能删除，也不能写供应商、订单和事件。MCP 仍然只暴露查询和提交建议，外部 Agent 使用的 `decision_agent` 不能执行。`execute` 只接收 `--id`；若同时传入动作、订单、事件、角色、描述或新证据，这些参数被忽略，不会进入任务或证据。

技术失败后不必再次批准。下面的演示保持真实 Ontop 运行：提交和批准前检查都转发到 Ontop，执行那一次请求返回失败。随后只用决策编号重试，并故意带上会被忽略的动作参数。重试应恰好创建一条任务；再执行一次应返回同一条任务，且不新增执行尝试。演示不修改供应商、订单或事件，也不停止 Ontop。订单 101 与事件 E01 当时不能已有占位决策，否则提交会返回原记录。标准输出是一行摘要 JSON。

```bash
docker compose -f compose.decision.yml run --rm -T \
  -e DECISION_DB_USER=decision_manager \
  -e DECISION_DB_PASSWORD=demo_manager_db_password \
  --entrypoint python decision -B -c '
import json, threading, urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from contextlib import redirect_stdout
from decision.cli import main
from decision.service import DecisionService, Settings
from decision.test_config import SUPERVISOR_TOKEN

state = {"n": 0}

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        state["n"] += 1
        if state["n"] >= 3:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"unavailable")
            return
        request = urllib.request.Request(
            "http://ontop:8080/sparql", data=body, method="POST",
            headers={
                "Accept": self.headers.get("Accept", "text/turtle"),
                "Content-Type": self.headers.get("Content-Type", "application/sparql-query"),
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        self.send_response(200)
        self.send_header("Content-Type", response.headers.get("Content-Type", "text/turtle"))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass

def run(argv):
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    if code != 0:
        raise SystemExit(code)
    return json.loads(buffer.getvalue())

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
worker = threading.Thread(target=server.serve_forever, daemon=True)
worker.start()
try:
    endpoint = "http://127.0.0.1:%s/sparql" % server.server_port
    agent = DecisionService(replace(
        Settings.from_env(), endpoint=endpoint,
        db_user="decision_agent", db_password="demo_agent_db_password",
    ))
    manager = DecisionService(replace(Settings.from_env(), endpoint=endpoint))
    created = agent.submit("101", "E01", "说明不是证据")
    failed = manager.approve(created["id"], SUPERVISOR_TOKEN, "同意，执行时 Ontop 失败")
finally:
    server.shutdown()
    worker.join()

recovered = run([
    "execute", "--id", str(created["id"]),
    "--action", "cancel_order", "--order", "102", "--event", "E99",
    "--assigned-role", "采购主管", "--description", "无法交付", "--evidence", "{}",
])
again = run(["execute", "--id", str(created["id"]), "--description", "无法交付"])
print(json.dumps({
    "failed_status": failed["execution_status"],
    "failed_attempts": len(failed["attempts"]),
    "retry_status": recovered["execution_status"],
    "task_order": recovered["task"]["order"],
    "task_event": recovered["task"]["event"],
    "task_description": recovered["task"]["description"],
    "attempt_outcomes": [item["outcome"] for item in recovered["attempts"]],
    "repeat_same_task": again["task"]["id"] == recovered["task"]["id"],
    "repeat_same_attempts": len(again["attempts"]) == len(recovered["attempts"]),
}, ensure_ascii=False))
'
```

期望摘要为：`failed_status` 是 `FAILED`，`failed_attempts` 是 1，`retry_status` 是 `SUCCEEDED`，任务订单和事件是 101 与 E01，描述是「订单 101 因事件 E01 需要供应风险复核。」，`attempt_outcomes` 是 `FAILED` 与 `SUCCEEDED`，再次执行的任务和尝试条数与重试相同。

## 按任务编号追溯

审计查阅不另设业务角色，也不要求个人令牌。能运行本容器即可按供应风险复核任务编号查询。命令行 `trace` 只调用决策模块的只读操作 `DecisionService.trace`，不批准、不执行、不重试，也不修改任务、决策、证据、审批或执行尝试。已有的失败尝试和后来的成功尝试会一并返回。无效任务编号返回 `found` 为 false，以及“未找到供应风险复核任务”。MCP 不注册追溯工具。

追溯结果把完整事实快照和业务前提分开：事实快照（按行排序的 N-Triples 和事实哈希）只用来发现快照是否被改写，不参与业务失效判断；业务前提（绑定事实、语义版本指纹和业务前提哈希）用来判断绑定是否变化。规则应用单独标为绑定记录，不是通用 OWL 证明。推理版本材料保存两份本体、规则原文、语义版本指纹和代码提交，供离线重放。审批结论和标明不是证据的 Agent 说明各自独立，说明不进入事实哈希。

容器使用只读账号 `decision_reader`。它只能查询决策记录、审批记录、执行尝试和供应风险复核任务，不能插入、更新或删除。下面的命令沿用本节前面的项目名、代码提交和 Git 目录。第一段用 Agent 通道提交建议，并用审批账号批准以产生任务，只打印任务编号。第二段只用只读账号调用 `trace`。第三段查询一个不存在的编号，应打印未找到并返回退出码 1。追溯本身不调用执行或重试命令。

```bash
TASK_ID=$(docker compose -f compose.decision.yml run --rm -T \
  -e DECISION_DB_USER=decision_manager \
  -e DECISION_DB_PASSWORD=demo_manager_db_password \
  --entrypoint python decision -B -c '
from dataclasses import replace
from decision.service import DecisionService, Settings
from decision.test_config import SUPERVISOR_TOKEN
agent = DecisionService(replace(Settings.from_env(), db_user="decision_agent", db_password="demo_agent_db_password"))
manager = DecisionService(Settings.from_env())
created = agent.submit("101", "E01", "请查阅；这段说明不是证据")
approved = manager.approve(created["id"], SUPERVISOR_TOKEN, "同意创建复核任务")
print(approved["task"]["id"])
')
docker compose -f compose.decision.yml run --rm -T \
  -e DECISION_DB_USER=decision_reader \
  -e DECISION_DB_PASSWORD=demo_reader_db_password \
  --entrypoint python decision -m decision.cli trace --task-id "$TASK_ID"
docker compose -f compose.decision.yml run --rm -T \
  -e DECISION_DB_USER=decision_reader \
  -e DECISION_DB_PASSWORD=demo_reader_db_password \
  --entrypoint python decision -m decision.cli trace --task-id 999999999
```

单独查询已有任务时：`python -m decision.cli trace --task-id <任务编号>`。容器里的数据库账号使用 `decision_reader`。

两个库必须一起备份：决策库 `decision_loop` 和采购库 `procurement`。供应风险复核任务在采购库，其外键指向决策库的审批记录，也指向采购库的订单和供应中断事件。执行尝试在决策库，外键同时指向决策记录和采购库里的任务。只还原其中一个库、或先写入仍引用未还原表的行，跨库外键都会失败。这些外键没有级联删除。

备份示例（项目名与上面一致；演示口令只用于本独立环境）：

```bash
mkdir -p /tmp/vkg-decision-backup
docker compose -f compose.decision.yml exec -T mysql \
  mysqldump -uroot -pdemo_root_password --single-transaction --no-tablespaces \
  decision_loop > /tmp/vkg-decision-backup/decision_loop.sql
docker compose -f compose.decision.yml exec -T mysql \
  mysqldump -uroot -pdemo_root_password --single-transaction --no-tablespaces \
  procurement > /tmp/vkg-decision-backup/procurement.sql
```

还原时先让被引用的表就绪，再写入引用它们的表，顺序是：

1. 采购库中的供应商、采购订单、供应中断事件，以及其他不引用决策库的表。
2. 决策库中的用户、决策记录、审批记录。此时还不要写入执行尝试。
3. 采购库中的供应风险复核任务。它依赖审批记录、订单和事件都已存在。
4. 决策库中的执行尝试。它依赖决策记录，成功尝试还依赖已经还原的任务编号。

不要先恢复带有任务行的完整采购库，除非决策库的审批记录已经就位；也不要先恢复带有执行尝试的完整决策库，除非任务表已经就位。测试会把 E01、订单和供应商恢复到演示初始行，并删掉测试插入的事件。确认结束后只清理本项目：

```bash
docker compose -f compose.decision.yml down --volumes --remove-orphans
```

MCP 进程仅收到 `decision_agent` 数据库账号，它可读取决策并插入证据、绑定编号、系统描述和说明列；状态、占位和时间由数据库默认值产生。它没有更新、删除或源表写权限。推理查询和建议提交共享的推理调用在进程内串行执行，避免 RDFLib/pyparsing 共享解析器的线程竞争；数据库唯一约束独立保证跨请求、跨进程占位去重，每次操作仍独占自己的连接。批准和执行复用这把推理锁。

完整新模块验收包含真实并发提交、参数忽略、过时规则、版本核验、哈希排除项、空白节点、计算期间文件变化和权限探针：

```bash
docker compose -f compose.decision.yml run --rm -T \
  -e TEST_DB_PASSWORD=demo_root_password --entrypoint python decision \
  -B -m unittest discover -s decision -p 'test_*.py'
```

上面的管理密码仅用于测试夹具；正常 MCP 启动和 stdio smoke 不注入它。若本机下载 Debian 索引过慢，可在镜像构建时设置 `DECISION_DEBIAN_MIRROR` 为可达的 Debian 镜像根地址（包含 `/debian` 和 `/debian-security` 的父地址）；默认仍为 `https://deb.debian.org`，APT 的 Release 签名校验保持启用。后续构建沿用同一镜像参数可复用 Git 安装层。
