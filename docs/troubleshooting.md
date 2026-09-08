# 运行维护与故障排查

[返回项目首页](../README.md)

## 服务管理

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs --tail=100 mysql ontop

# 停止但保留容器和数据
docker compose stop

# 删除容器和网络，保留数据卷
docker compose down

# 仅在明确需要彻底重置演示数据时删除卷
docker compose down -v
```

`sql/init.sql` 只在空数据卷首次启动时执行。已有卷升级应运行：

```bash
bash scripts/migrate-phase2.sh
```

## 常见问题

| 现象 | 排查与处理 |
| --- | --- |
| 无法连接 Docker daemon | 启动 Docker Desktop，启用当前 Ubuntu 的 WSL Integration，用 `docker version` 确认 Server 可达 |
| 找不到 `docker compose` | 检查 Docker Desktop WSL Integration、Docker CLI 插件和 PATH |
| Docker socket permission denied | 重新进入 WSL，检查 Integration 和当前用户的 socket 权限 |
| 拉取镜像超时、EOF、DNS 或 TLS 错误 | 检查 Docker Desktop 代理和网络；分别执行 `docker pull mysql:8.4.8`、`docker pull ontop/ontop:5.5.0` |
| Maven Central 下载失败 | 检查构建器能否访问 `repo.maven.apache.org`；恢复网络后重新 build，保留 JDBC checksum 校验 |
| 8080 或 13306 已占用 | 启动前设置 `ONTOP_PORT=18080 MYSQL_PORT=23306`；相应调整浏览器和宿主机访问端口 |
| MySQL unhealthy | 查看 `docker compose logs mysql`；确认初始化完成，旧卷不会自动重跑 `init.sql` |
| Ontop 报表不存在 | 对已有卷运行 `bash scripts/migrate-phase2.sh`，让 Ontop 重新加载元数据 |
| `No suitable driver` | 重建 Ontop 镜像，并检查 `/opt/ontop/jdbc/mysql-connector-j-8.4.0.jar` 是否可读 |
| JDBC 通信失败 | `ontop.properties` 中主机必须是 Compose 服务名 `mysql`，容器端口为 3306 |
| Mapping 或 ontology 解析失败 | 查看 Ontop 日志中的具体行，检查 OBDA 空行、列名、URI 和数据类型 |
| SPARQL 查询为空 | 先运行 SQL 对照，再检查状态值、URI 模板和事件是否有效 |
| 推理基础事实为空 | 确认 Ontop healthy、迁移已运行，并直接执行 `queries/phase2-base-facts.rq` |
| `/ontop/reformulate` 不可用 | 确认 `ONTOP_DEV_MODE=true`，然后重新创建 Ontop 容器 |
| Windows 浏览器打不开 | 检查 Docker Desktop 端口转发、本机防火墙和 WSL localhost 转发 |
| 脚本出现 `bash\r` | 在 WSL 中执行脚本，并保留仓库 `.gitattributes` 配置的 LF 换行 |

## 本地演示配置

仓库中的数据库密码只用于本机演示，端口默认仅绑定 `127.0.0.1`。Ontop 启用了开发模式和 debug 日志以便观察 SQL。不要直接把该配置作为生产部署模板。

## 官方参考

- [Ontop Docker endpoint](https://ontop-vkg.org/tutorial/endpoint/endpoint-docker.html)
- [Ontop CLI](https://ontop-vkg.org/guide/cli)
- [Ontop OBDA Mapping Language](https://ontop-vkg.org/guide/advanced/mapping-language.html)
- [RDFLib documentation](https://rdflib.readthedocs.io/en/stable/)
- [owlrl 7.1.4](https://pypi.org/project/owlrl/7.1.4/)
