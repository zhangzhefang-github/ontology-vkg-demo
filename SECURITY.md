# Security Policy

[中文](#中文) | [English](#english)

## 中文

这是本地架构验证项目，不是生产部署模板。仓库中的数据库密码是公开的演示凭据，服务端口默认只绑定 `127.0.0.1`，Ontop 开发模式和 debug 日志也只用于观察查询改写。

用于真实环境前，至少需要替换凭据、使用 Docker secrets 或等价密钥管理、启用传输加密、限制网络访问、关闭开发模式、调整日志级别，并建立镜像和依赖漏洞扫描流程。

如果发现安全漏洞，请优先使用仓库 Security 页面中的私密漏洞报告功能。不要在公开 Issue 中提交密钥、内部地址、真实业务数据或可直接利用的敏感细节。如果私密报告入口不可用，请先通过仓库所有者的 GitHub 主页联系维护者。

本项目只维护 `main` 分支的最新版本，不为旧提交提供安全补丁承诺。

## English

This is a local architecture validation project, not a production deployment template. Database passwords in the repository are public demo credentials. Service ports bind to `127.0.0.1` by default, and Ontop development mode and debug logging are enabled only to expose query rewriting behavior.

Before adapting it to a real environment, replace credentials, use Docker secrets or an equivalent secret manager, enable transport encryption, restrict network access, disable development mode, reduce logging, and establish image and dependency vulnerability scanning.

Report vulnerabilities through the repository's private vulnerability reporting feature when available. Do not include secrets, internal addresses, real business data, or directly exploitable details in a public issue. If private reporting is unavailable, contact the maintainer through the repository owner's GitHub profile first.

Only the latest version on `main` is maintained. No security fixes are promised for older commits.
