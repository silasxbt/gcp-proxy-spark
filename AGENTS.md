# Agent 工作约定

## 开发

- 先读 README、docs/verification.md；更改部署行为时读 docs/deployment.md。
- 只支持显式锁定的核心版本；更改版本后同步 versions.json、能力表、原生校验与实连测试结果。
- 配置通过结构化对象生成，不用字符串替换 YAML/JSON。私钥不得进入客户端输出。
- 单元测试：`python -m unittest discover -v`；原生与实连：`python -m tests.native_check --connections`。
- Linux 计数测试只能在独立 network namespace 内运行。不要在用户主机直接创建测试防火墙规则。

## 部署与授权

- 使用 `skills/gcp-proxy-deploy/SKILL.md`。先只读检查，再展示计划；明确确认后才 apply。
- 不自动创建 GCP 资源，不自动修改 IAM/云防火墙，不接管 `/etc/s-box`，不升级旧生产服务。
- SSH、gcloud 登录令牌、IP/项目真实资料、订阅 URL、manifest、证书、历史计数都不要提交。
- 不能绕过 TLS、下载摘要、目标系统、端口冲突或所有权检查。失败应保留证据、停止扩张操作。
- 完成配置写入不等于部署完成；检查 systemd、可信 HTTPS、订阅导入、外网 TCP/UDP 连通、流量增量和续期。
- 无设备验证的 Shadowrocket 必须标“真机待验证”，不能写“全客户端兼容”。

## 发布

- 审核 Git 暂存白名单，运行 `python -m tests.release_audit`，确认没有生产数据。
- 保留 LICENSE 和 NOTICE.md。新增依赖记录许可证和用途。
- 发布后验证远端提交与 CI；测试未通过或实际未验证的场景写进 docs/verification.md。
- 不因为部署授权而推送无关项目、替换域名或改动其他服务。
