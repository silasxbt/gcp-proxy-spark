# GCP Proxy Spark

每台机器，一组节点，一个托管订阅，一张清楚的流量页。

从基于 [sing-box-yg](https://github.com/yonggekkk/sing-box-yg) 的实际 GCP 部署经验整理而来：保留五类协议与轻量部署思路，重新组织配置生成、订阅托管、双栈流量统计与 Agent 操作流程。不是上游官方版本，也不是多服务器管理面板。

**状态：0.1.0 早期版本。** 查看 [验证记录](docs/verification.md) 了解已经测试与仍需人工验收的部分。不要直接覆盖已有部署；本项目拒绝自动接管 `/etc/s-box`。

## 提供什么

- **sing-box mode**：启用已支持的节点，输出 sing-box JSON 或 Shadowrocket URI 订阅。
- **Clash mode**：针对锁定版本的 Mihomo 生成兼容节点及 YAML，整合选择组与自动选择组。
- **每机订阅托管**：同一随机令牌，三种输出格式，不经第三方转换器。
- **无需域名**：IP HTTPS 证书与自动续期；证书失败时停止，不降级 HTTP。
- **公开流量页**：单机本月上传、下载、协议占比、运行状态、更新时间与下次重置。
- **200 GB 参考额度**：前 200 GB 为高速流量参考额度，只展示和提醒，**不自动限速、不停服、不是 GCP 免费额度**。
- **部署可审阅**：先检查、展示计划，再明确确认；保留旧版本和状态，支持回退。

不包含：购买云资源、费用估算、中央面板、Cloudflare/Argo/Workers、serv00、Psiphon、广告或用户访问记录收集。

## 客户端与协议

| 协议 | 默认部署 | sing-box 1.13.19 | Mihomo 1.19.32 | Shadowrocket |
|---|---|---|---|---|
| VLESS Reality / TCP | 是 | 生成与原生验证 | 生成与原生验证 | URI，真机待验证 |
| AnyTLS / 普通 TLS | 是 | 同上 | 同上 | URI，真机待验证 |
| Hysteria2 | 是 | 同上 | 同上 | URI，真机待验证 |
| TUIC v5 | 是 | 同上 | 同上 | URI，真机待验证 |
| VMess WS / 无 TLS | 否，须显式确认 | 兼容选项 | 兼容选项 | URI，真机待验证 |

Mihomo 已支持 Hysteria2 和普通 AnyTLS，因此当前两个模式默认都有四个节点；模式区分的是**能力约束与输出格式**，不是人为删掉新协议。AnyTLS + Reality 不在本项目兼容表内。Clash Verge/Meta 的实际兼容性取决于内置核心，旧版 Clash 不受支持。详见 [兼容说明](docs/compatibility.md)。

节点标签是经验建议，不是中国大陆实测。默认 TCP 节点排前，UDP 节点标注“需 UDP”；可调整 `priority`，不随流量变化改名。

## 本地开发

Python 3.10+、curl、OpenSSL；部署目标为 Debian 12+/Ubuntu 22.04+ 的 amd64/arm64 Linux。

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -v
.venv/bin/spark fetch singbox --directory .cache/bin
.venv/bin/spark fetch mihomo --directory .cache/bin
.venv/bin/python -m tests.native_check --connections
```

下载只使用 `spark/versions.json` 锁定的 GitHub 官方发行包与 SHA256；摘要不匹配会拒绝使用。macOS arm64 支持开发验证，不支持服务器部署。

## 让 Agent 部署

把仓库交给 Agent，并说：

> 读取 AGENTS.md 和 skills/gcp-proxy-deploy/SKILL.md。先只读检查我已有的服务器，生成部署计划；不要创建实例，不要覆盖已有服务，变更前等待我确认。

Agent 使用现有 SSH 或 gcloud/IAP 连接，不把登录凭证放进仓库。登录、IAM、IP 固定、云防火墙和证书挑战失败等人工环节都有停止条件。详见 [部署手册](docs/deployment.md)。

目标机器准备依赖后，核心流程如下；所有私密文件放在仓库外：

```sh
spark fetch singbox --directory /root/spark-private/bin
spark init --address YOUR_PUBLIC_IP --region YOUR_REGION --mode singbox \
  --binary /root/spark-private/bin/sing-box --reality-host YOUR_TLS13_HANDSHAKE_HOST \
  --output /root/spark-private/manifest.json
spark plan --manifest /root/spark-private/manifest.json
sudo spark inspect --manifest /root/spark-private/manifest.json
# 确认固定公网 IP、GCP 入站规则、Reality 握手站点及计划后：
sudo spark apply --manifest /root/spark-private/manifest.json --email YOUR_EMAIL \
  --confirm --network-confirmed
```

`YOUR_TLS13_HANDSHAKE_HOST` 是你确认允许使用且从服务器可达的 TLS 1.3 握手站点，不是订阅域名。工具不替你选择第三方站点。

不要将以上占位符直接执行。首次部署须先按手册准备运行环境，`inspect` 会拒绝缺少依赖或存在冲突的机器。

## 获取订阅与流量页

部署成功不会自动把令牌打印到日志。需要时在私人终端执行：

```sh
sudo spark urls --manifest /opt/gcp-proxy-spark/current/manifest.json
```

同一令牌通过 `?format=singbox`、`?format=clash`、`?format=shadowrocket` 获取不同载荷。没有一个载荷能通用所有客户端。sing-box 输出是本地 mixed 代理配置，不自动接管设备 TUN/VPN；移动端可能需要在客户端启用 VPN/TUN 设置。

公开页面：`https://YOUR_PUBLIC_IP/traffic`。它没有登录，只公开协议汇总，不提供管理或命令执行接口。订阅地址相当于密码，不要贴在 issue、截图或公开日志里。

## 流量口径与限制

UTC 自然月，十进制 GB，上传＋下载，IPv4＋IPv6；包含隐藏但仍启用的节点。统计的是协议监听端口网络字节，包括握手、重传及未认证连接，不是 GCP 账单，也不是用户有效载荷。应用自身的订阅网页流量不计入节点统计。

每 10 秒采样；跨月间隔按时间比例分摊并标记估算。异常停机可能丢失最后一次采样后的未持久化增量，不能宣称财务级精确。超过 200 GB 不影响运行；部分客户端可能根据订阅总量自行显示“超额”。详见 [流量设计](docs/traffic.md)。

## 安全与许可证

- 真实配置、私钥、订阅令牌、云凭证、历史流量均不进入 Git。
- 不关闭证书校验；不提供不安全 HTTP 订阅回退。
- 仅操作项目自有服务、Nginx 配置和 nftables 表；不清空系统防火墙。
- 使用代理须遵守所在地区法律与云服务条款。
- GPL-3.0-only，保留 [许可证](LICENSE) 与 [来源说明](NOTICE.md)。漏洞请勿附带可用凭证；先按 [安全说明](SECURITY.md) 脱敏。
