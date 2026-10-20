# 来源与修改说明

项目灵感和部署沿革来自 yonggekkk 的 [sing-box-yg](https://github.com/yonggekkk/sing-box-yg)，参考快照 `c727742133692820a5023bf44e007caa39274969`。上游采用 GNU GPL v3，本仓库采用 GPL-3.0-only 并保留完整许可证。

首版由用户既有 GCP 部署的行为与运维经验抽取：五协议、单机统一订阅、每协议流量计数、订阅流量信息头。未复制生产配置、真实订阅、证书、日志或凭证。

代码重新组织为 Python 包：结构化生成三类客户端配置；以独立 nftables inet 表替代旧 IPv4-only iptables 计数；稳定标签；公开汇总页；IP HTTPS；版本锁定；可审阅部署与回退；Agent 技能。旧导出器中的文本 YAML 替换、流量改名、将重置时间充当订阅到期时间均未保留。

不再包含上游的 serv00、Argo、Workers 保活和 Psiphon 等非核心内容。不声称与上游作者或客户端开发者存在官方合作。

sing-box、Mihomo、Certbot、Nginx、Python、PyYAML 为独立上游项目，遵循各自许可证。本仓库不提交其二进制；下载清单保留官方来源、版本和摘要。
