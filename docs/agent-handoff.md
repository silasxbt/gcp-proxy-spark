# Agent 交接手册

适用于 Claude Code 与 Codex：用户把任务交给 Agent 以后，人工步骤尽量少。Agent 仍须遵守 `AGENTS.md` 和部署技能的全部停止条件。本手册只负责把人工步骤集中到最少的几个点，不放宽任何安全检查。

## 人工只做三件事

| 时机 | 用户 | Agent |
|---|---|---|
| 开始前 | 在自己的电脑登录 gcloud 或配置好 SSH，填写下方交接单 | 读取交接单；缺少的信息一次性全部列出后再问 |
| 计划确认 | 审阅一份汇总计划，回复一次“确认” | 先只读检查，再生成计划，汇总依赖安装、证书申请、端口与防火墙、短暂重启的影响 |
| 部署完成后 | 在客户端导入订阅并访问一次网页 | 完成服务端的全部验收，报告已验证项、未验证项和回退用的 release |

除这三点外，以下情况也必须停下等人：需要重新登录，缺少 IAM/IAP/SSH 权限，没有可用 VM，资源所有权冲突，证书签发失败，下载摘要不匹配。Agent 停下时要说明具体缺少什么条件，不能换账号或绕过检查。

## 交接单

复制到**仓库外**的私有文件（例如 `~/spark-private/handoff.md`），也可以直接贴在对话里。不要提交到 Git。

```markdown
## 目标机器
- 云厂商: GCP | AWS（未验证） | Oracle OCI（未验证） | 其他
- 连接方式: gcloud-iap | ssh
- gcloud: 项目=… 区域/可用区=… 实例=…        # 选 gcloud 时填写
- ssh: Host 别名=…（~/.ssh/config 中已配置）  # 选 ssh 时填写
- 公网 IP 已固定: 是 | 否 | 请 Agent 检查后告诉我
- 机器用途: 新的专用机 | 已有 sing-box-yg，需要并行部署（需人工审阅共存方案）

## 选择
- 主要客户端: sing-box/Shadowrocket（mode=singbox） | Clash Verge/Mihomo（mode=clash）
- Reality 握手站点: <主机名> | 请 Agent 提出 2–3 个候选，由我选
- 启用无 TLS VMess: 否 | 是（明确接受明文风险）
- 节点地区标签: …（例如 Seoul）
- ACME 邮箱: …（用于注册证书账号，表示同意 Let's Encrypt 条款）

## 预先授权（未勾选的一律视为未授权）
- [ ] 在专用新机运行 scripts/prepare-host.sh 安装依赖
- [ ] 为该实例修改云端入站规则（GCP 防火墙 / AWS Security Group / OCI Security List 或 NSG），只放行计划中列出的端口
- [ ] 主机防火墙（例如 Oracle Ubuntu 的 iptables）拦截时，只放行计划中列出的端口并持久化
- [ ] 计划确认后直接 apply，部署完成后再汇报
- [ ] 部署完成后在私人对话里给出订阅链接
```

## Agent 执行顺序

1. **接入**：确认 `gcloud auth list`、当前项目或 SSH 主机指纹，核对实例身份。AWS/Oracle 用交接单中的 SSH 别名，不读取云账号密钥。不读取、不打印令牌和私钥。
2. **只读检查**：查看系统、架构、已有服务、监听端口、Nginx、nftables 和 `/etc/s-box`；从操作者一侧检查 IP 是否固定，以及云端入站规则；`inspect` 会额外报告主机防火墙拦截。
3. **生成计划**：在机器上的私有目录 `spark init`，然后运行 `spark plan` 和 `sudo spark inspect`，把结果整理成一份计划。计划列出：节点与端口、需要放行的入站规则、需要安装的依赖、证书申请、重启影响、交接单中已授权和未授权的动作。不展示凭证。
4. **唯一确认**：等用户回复确认。未授权的动作（例如防火墙改动）在这一步一并请求授权，不在后续阶段另行追问。
5. **执行**：准备依赖，在已授权时放行云端和主机防火墙（方法见 deployment.md 的云厂商差异一节），从另一台机器核验网络可达后执行 `apply --confirm --network-confirmed`。任何一步失败都按部署技能处理，不扩大操作范围。
6. **验收**：检查 systemd 服务，从外部访问 HTTPS 的 `/traffic` 并确认无证书警告，订阅接口返回 200，用 Mihomo 或 sing-box 核心对每个节点发起真实请求（TCP 和 UDP 分开验证），确认流量出现增量，演练续期（`--dry-run`，会触发短暂重启，须已获授权）。
7. **报告**：说明 release ID、已验证项、需要用户在真机上验收的项（Shadowrocket 标注“真机待验证”），以及回退命令。只有交接单授权时才在私人对话中给出订阅链接。

AWS 与 Oracle 路径尚未真机验证。部署中发现与文档不符时，Agent 应保留证据、停止，并整理成可提交到 [Issues](https://github.com/silasxbt/gcp-proxy-spark/issues) 的脱敏报告。

## 给 Agent 的开场语

> 读取 AGENTS.md，按 docs/agent-handoff.md 使用我的交接单（见下方或 ~/spark-private/handoff.md）。先只读检查，汇总成一份计划；变更前等待我确认一次。

## 多 Agent 协作

Claude Code 与 Codex 共用 `AGENTS.md`、`.agents/skills/` 和本手册，可以接力完成同一次部署。交接时把当前阶段（上面的 1–7 步）、release ID 和未完成的验收项写在对话里，不要写进仓库。同一时刻只能有一个 Agent 对目标机器执行变更：`apply` 自带部署锁，但其他手工操作没有锁保护。
