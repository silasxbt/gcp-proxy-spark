# 流量口径

独立 nftables `inet proxy_spark` 表同时统计 IPv4/IPv6：input 目标监听端口记上传，output 来源监听端口记下载。仅计数，不加入 DROP、REJECT、限速或全局 flush；系统其他表仍决定放行/拒绝。计数在其他防火墙拒绝前可能发生，因此也可能包含被拒绝、握手失败和未认证连接。

不适用于容器端口转发或 NAT 后监听的自动归属；首版仅支持主机原生 systemd 部署。UDP 统计包括协议开销，TCP 包含重传和握手。代理到目标网站的另一段出站连接不重复计入；订阅网页流量不计入。GCP 账单按不同方向、地区、服务和政策计算，本页面不能用于核算费用。

状态保存在 `/var/lib/gcp-proxy-spark/traffic.json`，包含每月计数、节点映射、上次基线和计数表代际。每 10 秒采样，原子持久化，单写者文件锁。服务重启延续基线；系统重启或表重建通过 boot id + table handle 识别；计数倒退视作重置。

月份为 UTC，自然月。跨月采样增量按时间比例分摊，整数余数保留到最后一段并标记 `boundary_estimated`。长时间停采样后的跨月分配是估算；掉电时最后一次持久化之后的字节可能无法恢复。损坏状态不会悄悄清零，应先备份并人工修复。

删除/隐藏节点不删除历史。变更端口生成新的计数身份；移除旧匹配规则但保留命名计数器及历史，便于读取最后增量。

默认 `quota_bytes=200000000000`，十进制 200 GB。公开页和订阅流量头使用上传＋下载总额；超过后仅提醒，不节流、不拒绝订阅、不停代理。订阅头不设置虚假 `expire`，重置时间在 `X-Traffic-Reset-At` 和 API 中单独表达。

`/api/traffic` 输出 month、timezone、upload、download、total、quota_bytes、over_reference、quota_action、updated_at、stale、service_active、next_reset_at、boundary_estimated、protocols。没有节点凭证、源 IP、访问目标、用户名或完整配置。超过 180 秒未采样会标 stale，订阅接口返回 503，既有已导入节点不会被服务端停用。
