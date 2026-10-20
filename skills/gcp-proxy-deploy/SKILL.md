---
name: gcp-proxy-deploy
description: Deploy, inspect, upgrade or roll back gcp-proxy-spark on an existing Debian/Ubuntu GCP or SSH server. Use for this repository's single-machine subscription and traffic services, not VM provisioning or adopting another proxy installation.
---

# Spark 部署技能

在仓库根目录工作。先读 `../../README.md`、`../../docs/verification.md`，连接/安装/续期操作读 `../../docs/deployment.md`；协议疑问读 `../../docs/compatibility.md`，统计疑问读 `../../docs/traffic.md`。以上路径均相对本技能目录。

## 工作流

1. 确认请求是检查还是变更；接入已有机器并核对目标身份。gcloud/IAP 或已有 SSH 均可，不读取/打印账号私钥与 access token。
2. 只读检查系统、已有服务、监听端口、IP 稳定性、GCP 网络规则。发现 `/etc/s-box`、业务网站或冲突端口就停止，不接管，不猜测哪些旧服务可以关闭。
3. 让用户选择主要客户端 sing-box/Shadowrocket 或 Mihomo 系 Clash；按锁定能力表生成，不能把 Hysteria2/普通 AnyTLS 一律排除。选择 TLS 1.3 Reality 握手站点需用户确认可用/允许使用。
4. 首次生成仓库外私密 manifest；升级沿用当前 manifest。运行 plan 与 inspect，展示节点增删、端口、依赖安装、证书申请和短暂重启影响，不展示凭证。模式变化须展示差异。
5. 明确授权后准备依赖并 apply；只有真正完成外部网络/IP 核验后才使用 `--network-confirmed`。云资源创建、IAM、云防火墙变更不属于 apply 的默认权限。
6. 验证服务、可信 HTTPS、订阅、真实 TCP/UDP 请求、流量增量、续期与过期检查；输出完成项、未验证项和回退 release。订阅链接只在用户明确要取链接时通过 urls 获取，避免进入公开日志。

## 必须停下的情况

- 需要用户登录、缺少 IAM/IAP/SSH 权限：给出具体缺少条件，由用户处理，不切换陌生账号。
- 没有已有 VM、需要新 IP 或新增付费资源：请求独立授权，不自动创建。
- 端口/站点/计数表所有权冲突：不覆盖、不停止未知服务；提出专用机或人工共存方案。
- 官方下载摘要不匹配：拒绝安装。仅针对可证明的网络截断进行有限重试，不替换锁定摘要。
- IP 证书失败：检查固定 IP、HTTP-01、80 端口和网络，解决前暂停；禁止 `-k`、关闭证书验证或 HTTP 回退。
- 状态损坏/时钟倒退：保留原文件，人工诊断，不清空流量。
- 无 iOS 设备：Shadowrocket 保留“URI 格式已测试、真机待验证”，不声称通过。

200 GB 是每机每月的显示参考，不是免费账单额度、限速器或财务账本。公开流量页无登录是当前产品取舍，用户不接受公开汇总则暂停部署，不擅自扩大成用户管理后台。

升级失败保留旧版本与统计。先读当前 release 与日志，按手册 rollback；不因“继续尝试”反复覆盖现场。卸载仅针对自有资源，默认保留数据，结束时说明移除与留存内容。
