"""Synthetic fixtures only: documentation IPs, never deployment credentials."""
from spark.core import PORTS, PROTOCOLS, VERSIONS


def fixture():
    nodes = []
    for i, p in enumerate(PROTOCOLS):
        n = {"id": p, "protocol": p, "port": PORTS[p], "enabled": p != "vmess", "published": True,
             "priority": i * 10, "transport": "ws" if p == "vmess" else "tcp" if p in ("vless", "anytls") else "quic"}
        if p in ("vless", "vmess", "tuic"):
            n["uuid"] = "00000000-0000-4000-8000-000000000001"
        if p in ("anytls", "hysteria2", "tuic"):
            n["password"] = "TEST_ONLY_NOT_A_DEPLOYMENT_PASSWORD"
        if p == "vless":
            n["reality_config"] = {"private_key": "A" * 43, "public_key": "B" * 43,
                                   "short_id": "0123456789abcdef", "server_name": "example.com"}
        nodes.append(n)
    return {"schema": 1, "mode": "singbox", "address": "192.0.2.1", "region": "TEST",
            "target_versions": VERSIONS.copy(), "subscription_token": "TEST_ONLY_" + "0" * 32,
            "quota_bytes": 200_000_000_000, "allow_insecure_vmess": False, "nodes": nodes}
