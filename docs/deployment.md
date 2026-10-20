# 部署、升级与回退

本工具只在**已有、专用的 Debian/Ubuntu 主机**本地安装。远程连接由操作者或 Agent 完成。已有 `/etc/s-box`、其他 Nginx 站点、端口占用都应停止，不自动覆盖。不要在当前两台生产机器上试跑安装验收。

## 1. 接入与只读检查

### 人工审阅的并行部署例外

只有用户明确确认共存方案后，才可在已有 sing-box-yg 机器上新增独立 Spark 分组。
不运行专用机准备脚本。记录旧服务 PID、配置摘要、监听端口和防火墙规则；
手工准备缺失依赖，不修改、停止或接管旧服务。私密 manifest 的
`legacy_config_sha256` 必须是已审阅 `/etc/s-box/sb.json` 的完整 SHA256，
`exporter_port` 可选一个空闲内部端口（例如 9106）。检查器仍拒绝摘要变化、
旧配置端口重叠、监听冲突、其他网站和未知自有资源；此字段不是跳过检查的开关。
云防火墙变更必须另获明确授权，只放行目标实例的新端口。
部署后再次比对旧配置摘要和 PID。面板和计数仅覆盖 Spark 新分组，旧节点仍由原系统统计。

普通 SSH：使用已有 SSH 配置、私钥或系统代理，先核对目标主机指纹。GCP/IAP：

```sh
gcloud auth list
gcloud config get-value project
gcloud compute instances list --project YOUR_PROJECT
gcloud compute ssh YOUR_INSTANCE --project YOUR_PROJECT --zone YOUR_ZONE --tunnel-through-iap
```

不要在命令行粘贴 token，不读取服务账号 JSON 内容给聊天。需重新登录、缺少 IAP/OS Login/IAM 权限时停下，请用户处理。不替用户创建实例或修改 IAM。

在目标机检查：`cat /etc/os-release`、`uname -m`、`ss -lntup`、已有 systemd 服务、Nginx 配置、系统防火墙。操作者还须检查 GCP 的网络规则与目标标签：TCP 80/443，以及 manifest 中启用节点的 TCP/UDP 端口。项目只添加**计数规则**，不会自动添加 ACCEPT 规则，不会修改 GCP 防火墙。

确认公网 IP 稳定且属于该 VM；动态 IP 改变会同时破坏 IP 证书和订阅地址。固定 IP 的操作和可能费用由用户决定。

## 2. 准备环境（明确授权后）

全新、尚未安装 Nginx 的专用机可使用 `sudo sh scripts/prepare-host.sh --confirm`。脚本拒绝已有代理、Spark 目录和网页端口冲突；只停下这次新装的默认 Nginx，并保留发行版默认站点链接。它不部署节点、不申请证书、不修改 GCP。已有依赖或上次准备中断时，按下面的手工流程核对，不删除目录来绕过检查。

以下命令改变目标机。先确认是新部署专用机：

```sh
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip curl ca-certificates openssl nginx nftables
sudo install -d -m 755 /opt/gcp-proxy-spark
sudo python3 -m venv /opt/gcp-proxy-spark/venv
sudo /opt/gcp-proxy-spark/venv/bin/pip install 'PyYAML==6.0.3'
sudo python3 -m venv /opt/gcp-proxy-spark/certbot
sudo /opt/gcp-proxy-spark/certbot/bin/pip install 'certbot==5.4.0'
```

Ubuntu 22.04+/Debian 12+ 的系统 Python 可用。Python 库使用固定直接依赖版本；其传递依赖仍由 pip 解析，尚非离线可复现供应链。核心二进制另有精确 SHA256 锁定。

安装 Nginx 可能自动启用发行版默认站点。**先检查是否仅是新安装的默认欢迎页**；确认无业务后，将 `/etc/nginx/sites-enabled/default` 移到管理员备份目录，再停止这次新装的 Nginx。不要删除其他网站，已有站点应改用另一台机器或人工设计共存方案。

在仓库创建开发 venv 并 `pip install -e .`，运行 `.venv/bin/spark`。也可在目标机单独 venv 安装项目。不要把仓库放入生产服务路径后直接用 Git pull 影响运行：apply 会复制到独立 release。

若 `spark` 不在 sudo 的 PATH，将手册中 `sudo spark` 替换为 `sudo /你的仓库绝对路径/.venv/bin/spark`，不要为了方便把整个虚拟环境改为全局可写。

## 3. 初始化与审阅

按 README 初始化 manifest；它包含私钥，必须位于仓库外的私有目录。选择 `singbox` 或 `clash`；当前锁定矩阵支持同样的四个默认协议。无 TLS VMess 只有同时启用节点和 `allow_insecure_vmess` 才可部署。

- `enabled=false`：不部署监听。
- `published=false`：仍部署、仍计数，但不在订阅显示。
- `priority`：越小越靠前。名称稳定，不根据流量改变。
- 不支持任意透传 sing-box 配置或未验证协议组合，需先开发和测试。

执行 `spark plan`，已有 Spark 使用 `--previous /opt/gcp-proxy-spark/current/manifest.json` 查看差异。plan 不显示密码，仅说明私有配置是否变化。升级须保存原 manifest，避免 init 重新生成凭证。

`spark inspect` 在目标机以 root 执行；它检查系统、依赖、已有部署、文件所有权与监听冲突。云防火墙、出口网络及公网可达性不能仅凭本机检查证明；从另一台机器核验后才能传 `--network-confirmed`。

## 4. 应用与证书

明确确认后执行 README 中的 `spark apply ... --confirm --network-confirmed`。确认同时表示同意使用提供邮箱注册 ACME 账号及其服务条款。

工具会创建私密 manifest、下载校验核心、生成 release、用 Nginx 的 HTTP-01 路径申请 IP 证书，再原生校验并切换。80 端口只提供 ACME 挑战，其他路径返回 404；订阅仅在 HTTPS。

Certbot 5.4.0 使用 `--ip-address` 与 `--preferred-profile shortlived`，证书有效期短。每小时续期检查，续期后重载 Nginx 并重启代理加载新证书，会有短暂连接中断。每小时另检查是否不足 24 小时；失败通过 systemd failed 状态和日志报警，**不包含邮件/短信通知服务**。请接入自己的外部监控。

续期手工演练（在已部署机，经授权）：

```sh
sudo /opt/gcp-proxy-spark/certbot/bin/certbot renew --cert-name proxy-spark --dry-run --run-deploy-hooks
sudo systemctl list-timers 'spark-*'
sudo systemctl status spark-cert-check.service spark-renew.service
```

dry-run 使用 ACME 测试环境，不把测试证书作为生产证书。演练可能触发 deploy hook 重启，需安排窗口。端口冲突、签发失败、DNS/网络异常时不要关闭 TLS、绕过摘要或直接改成 HTTP。

## 5. 完成验收，而非只看安装成功

1. `spark-proxy`、`spark-web`、`spark-traffic` 运行正常，计数更新时间持续前进。
2. 外部访问 `https://IP/traffic` 不出现证书警告；匿名 API 不包含凭证。
3. 用私密订阅在客户端导入，每个节点发起真实请求；TCP 与 UDP 分别验证。
4. 刷新流量页出现增量；隐藏节点仍计数，超过参考额度不改变转发。
5. 检查续期演练、到期检查和外部监控。Shadowrocket 需要实际 iOS 设备验收。

安装器会检测本机 HTTPS、证书链/IP SAN、服务和计数；外部防火墙、地区网络与客户端导入仍需外部验收。

## 6. 升级、回退、卸载

`apply` 每次创建新 release，不改旧 release 或清空历史。应用前原生校验，升级前读取旧计数增量；失败恢复旧链接与配置。保留失败版本供私下排错，安装依赖、ACME 账号/已签发证书和诊断文件不会自动删除。首次失败没有旧服务可恢复，不会留下可用的 HTTP 订阅。

```sh
sudo spark rollback RELEASE_ID --confirm
sudo spark uninstall --confirm
```

回退恢复指定 release 的代码、节点配置与服务定义，保留流量历史。跨 IP 变更不支持原地切换，需另行规划证书与订阅迁移。

卸载只移除本项目 systemd 服务、Nginx 配置和计数表；release、凭证、证书、ACME 账号、Python 环境和流量数据均保留。不会卸载共享系统软件，也不会删除管理员其他文件。手工清理留存数据前应逐项确认路径和备份。
