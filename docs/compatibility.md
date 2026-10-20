# 兼容性与协议边界

锁定服务端/客户端基线：sing-box 1.13.19、Mihomo 1.19.32。`versions.json` 中记录官方发行包与 SHA256。未来升级须修改兼容表并重复原生校验与真实代理测试，不接受任意版本字符串。

当前模式交集包含 VLESS Reality/TCP、普通 TLS AnyTLS、Hysteria2、TUIC v5，以及显式启用的无 TLS VMess WS。两个模式节点集合相同是当前核心能力的结果；不是为制造区别而删掉可用节点。不同协议的网络可达性不能通过配置校验推断。

不支持 AnyTLS + Reality、Hysteria v1、旧 Clash 开源核心、随意传输组合。Clash Verge/Meta 是客户端名称，部署前核对实际核心版本。服务端始终使用 sing-box，Clash mode 指的是客户端兼容集合与默认 YAML 输出。

sing-box JSON 是可运行的本地 mixed 代理配置，监听 127.0.0.1:2080；没有 TUN、全局系统代理或规则数据库下载。GUI 客户端的 TUN 接管由客户端设置完成。

Shadowrocket 使用 Base64 换行 URI 列表，各协议分别编码。v1 的测试验证 URI 结构与认证字段，但未声明某个 Shadowrocket 版本已经真机导入。请实际验证 VLESS Reality、AnyTLS、Hysteria2、TUIC、VMess 后记录客户端版本；不要用“URI 能解析”替代“能联网”。

节点推荐是保守经验标签：TCP 节点优先，UDP 节点明确网络要求，VMess 无 TLS 为手工开启兼容项。没有来自中国大陆的独立探测，不能标注全国通用、必通或实测延迟排名。管理员可据自己的测试调整 priority 并在自己的运维记录注明来源和日期。

官方参考：

- https://sing-box.sagernet.org/configuration/
- https://wiki.metacubex.one/config/proxies/anytls/
- https://wiki.metacubex.one/config/proxies/hysteria2/
- https://letsencrypt.org/2026/03/11/shorter-certs-certbot/
