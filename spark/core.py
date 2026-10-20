"""Validated manifest and structured server/client renderers. No production data."""
import base64
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import uuid
from urllib.parse import quote, urlencode

import yaml

PROTOCOLS = ("vless", "anytls", "hysteria2", "tuic", "vmess")
VERSIONS = {"singbox": "1.13.19", "clash": "1.19.32"}
# A tested target, not a claim about every older client or protocol extension.
CAPABILITIES = {"singbox": set(PROTOCOLS), "clash": set(PROTOCOLS),
                "shadowrocket": set(PROTOCOLS)}
PORTS = dict(zip(PROTOCOLS, (24443, 24444, 24445, 24446, 24447)))
CERT = "/etc/letsencrypt/live/proxy-spark/fullchain.pem"
KEY = "/etc/letsencrypt/live/proxy-spark/privkey.pem"


def atomic_json(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".spark-")
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load(path):
    return validate(json.loads(Path(path).read_text()))


def validate(m):
    if m.get("schema") != 1 or m.get("mode") not in VERSIONS:
        raise ValueError("Unsupported schema/mode")
    ipaddress.ip_address(m["address"])
    if m.get("target_versions") != VERSIONS:
        raise ValueError("Unvalidated client versions: use the pinned capability matrix")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", m.get("subscription_token", "")):
        raise ValueError("Subscription token must have at least 32 URL-safe characters")
    if not isinstance(m.get("quota_bytes"), int) or m["quota_bytes"] <= 0:
        raise ValueError("quota_bytes must be positive")
    if not isinstance(m.get("region"), str) or not 1 <= len(m["region"]) <= 40:
        raise ValueError("region must be 1..40 characters")
    ids, ports = set(), set()
    if not m.get("nodes"):
        raise ValueError("At least one node is required")
    for n in m["nodes"]:
        p = n.get("protocol")
        if p not in PROTOCOLS or not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", n.get("id", "")):
            raise ValueError("Invalid protocol/node id")
        if n["id"] in ids or n.get("port") in ports:
            raise ValueError("Duplicate node id/port")
        ids.add(n["id"])
        ports.add(n.get("port"))
        if type(n.get("port")) is not int or not 1024 <= n["port"] <= 65535 or n["port"] == 9105:
            raise ValueError("Node port must be unprivileged and not exporter port 9105")
        if type(n.get("enabled")) is not bool or type(n.get("published")) is not bool:
            raise ValueError("enabled/published must be boolean")
        if type(n.get("priority")) is not int or not 0 <= n["priority"] <= 999:
            raise ValueError("priority must be 0..999")
        if n.get("transport") != ("ws" if p == "vmess" else "tcp" if p in ("vless", "anytls") else "quic"):
            raise ValueError("Unsupported protocol/transport combination")
        if n.get("reality", p == "vless") != (p == "vless"):
            raise ValueError("Reality is supported only for VLESS in this matrix")
        if p in ("vless", "vmess", "tuic"):
            uuid.UUID(n["uuid"])
        if p in ("anytls", "hysteria2", "tuic") and not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", n.get("password", "")):
            raise ValueError("Password must have at least 24 URL-safe characters")
        if p == "vless":
            r = n["reality_config"]
            if not re.fullmatch(r"[A-Za-z0-9.-]+", r["server_name"]):
                raise ValueError("Invalid Reality handshake hostname")
            for key in ("private_key", "public_key"):
                if not re.fullmatch(r"[A-Za-z0-9_-]{43}", r[key]):
                    raise ValueError("Invalid Reality key")
            if not re.fullmatch(r"[0-9a-f]{16}", r["short_id"]):
                raise ValueError("Invalid Reality short id")
        if p == "vmess" and n["enabled"] and not m.get("allow_insecure_vmess", False):
            raise ValueError("VMess WS has no TLS; explicitly acknowledge allow_insecure_vmess")
    if not selected(m):
        raise ValueError("No enabled nodes in selected mode")
    return m


def new_manifest(address, region, mode, binary, reality_host):
    # Generate locally, never reuse live credentials or log the subprocess output.
    raw = subprocess.check_output([str(binary), "generate", "reality-keypair"], text=True)
    keys = dict(line.split(": ", 1) for line in raw.strip().splitlines())
    nodes = []
    for index, p in enumerate(PROTOCOLS):
        n = {"id": p, "protocol": p, "port": PORTS[p], "enabled": p != "vmess",
             "published": True, "priority": index * 10,
             "transport": "ws" if p == "vmess" else "tcp" if p in ("vless", "anytls") else "quic"}
        if p in ("vless", "vmess", "tuic"):
            n["uuid"] = str(uuid.uuid4())
        if p in ("anytls", "hysteria2", "tuic"):
            n["password"] = secrets.token_urlsafe(32)
        if p == "vless":
            n["reality_config"] = {"private_key": keys["PrivateKey"], "public_key": keys["PublicKey"],
                                   "short_id": secrets.token_hex(8), "server_name": reality_host}
        nodes.append(n)
    return validate({"schema": 1, "address": address, "region": region, "mode": mode,
                     "target_versions": VERSIONS.copy(), "subscription_token": secrets.token_urlsafe(32),
                     "quota_bytes": 200_000_000_000, "allow_insecure_vmess": False, "nodes": nodes})


def selected(m, client=None, published=False):
    return sorted([n for n in m["nodes"] if n["enabled"]
                   and n["protocol"] in CAPABILITIES[m["mode"]]
                   and (client is None or n["protocol"] in CAPABILITIES[client])
                   and (not published or n["published"])], key=lambda n: (n["priority"], n["id"]))


def name(m, n):
    label = "推荐·TCP" if n["protocol"] in ("vless", "anytls") else "需UDP" if n["protocol"] in ("tuic", "hysteria2") else "兼容·无TLS"
    return f'{m["region"]} | {n["protocol"]} | {label} | {n["id"]}'


def server_config(m, cert=CERT, key=KEY):
    inbounds = []
    for n in selected(m):
        p = n["protocol"]
        b = {"type": p, "tag": n["id"], "listen": "::", "listen_port": n["port"]}
        if p == "vless":
            r = n["reality_config"]
            b.update(users=[{"uuid": n["uuid"], "flow": "xtls-rprx-vision"}], tls={"enabled": True,
                "server_name": r["server_name"], "reality": {"enabled": True,
                "handshake": {"server": r["server_name"], "server_port": 443},
                "private_key": r["private_key"], "short_id": [r["short_id"]]}})
        elif p == "vmess":
            b.update(users=[{"uuid": n["uuid"]}], transport={"type": "ws", "path": "/spark"})
        else:
            user = {"password": n["password"]}
            if p == "tuic":
                user["uuid"] = n["uuid"]
                b["congestion_control"] = "bbr"
            elif p == "anytls":
                user["name"] = "spark"
            b.update(users=[user], tls={"enabled": True, "certificate_path": cert, "key_path": key})
            if p in ("tuic", "hysteria2"):
                b["tls"]["alpn"] = ["h3"]
        inbounds.append(b)
    return {"log": {"level": "warn", "timestamp": True}, "inbounds": inbounds,
            "outbounds": [{"type": "direct", "tag": "direct"}], "route": {"final": "direct"}}


def singbox_config(m):
    out = []
    for n in selected(m, "singbox", True):
        p = n["protocol"]
        b = {"type": p, "tag": name(m, n), "server": m["address"], "server_port": n["port"]}
        if p in ("vless", "vmess", "tuic"):
            b["uuid"] = n["uuid"]
        if p in ("anytls", "hysteria2", "tuic"):
            b["password"] = n["password"]
        if p != "vmess":
            b["tls"] = {"enabled": True, "server_name": m["address"]}
        if p == "vless":
            r = n["reality_config"]
            b["flow"] = "xtls-rprx-vision"
            b["tls"].update(server_name=r["server_name"], utls={"enabled": True, "fingerprint": "chrome"},
                reality={"enabled": True, "public_key": r["public_key"], "short_id": r["short_id"]})
        if p == "vmess":
            b.update(security="auto", transport={"type": "ws", "path": "/spark"})
        if p in ("tuic", "hysteria2"):
            b["tls"]["alpn"] = ["h3"]
        if p == "tuic":
            b["congestion_control"] = "bbr"
        out.append(b)
    names = [b["tag"] for b in out]
    if not names:
        raise ValueError("No published nodes")
    return {"log": {"level": "warn"}, "inbounds": [{"type": "mixed", "tag": "local", "listen": "127.0.0.1", "listen_port": 2080}],
            "outbounds": [{"type": "selector", "tag": "proxy", "outbounds": ["auto"] + names},
                          {"type": "urltest", "tag": "auto", "outbounds": names,
                           "url": "https://www.gstatic.com/generate_204", "interval": "5m"}] + out,
            "route": {"final": "proxy"}}


def clash_config(m):
    out = []
    for n in selected(m, "clash", True):
        p = n["protocol"]
        b = {"name": name(m, n), "type": p, "server": m["address"], "port": n["port"], "udp": True}
        if p in ("vless", "vmess", "tuic"):
            b["uuid"] = n["uuid"]
        if p in ("anytls", "hysteria2", "tuic"):
            b["password"] = n["password"]
        if p == "vless":
            r = n["reality_config"]
            b.update(tls=True, flow="xtls-rprx-vision", servername=r["server_name"],
                     **{"client-fingerprint": "chrome", "reality-opts": {"public-key": r["public_key"], "short-id": r["short_id"]}})
        elif p == "vmess":
            b.update(cipher="auto", alterId=0, network="ws", tls=False, **{"ws-opts": {"path": "/spark"}})
        else:
            b.update(sni=m["address"], **{"skip-cert-verify": False})
            if p in ("tuic", "hysteria2"):
                b["alpn"] = ["h3"]
            if p == "tuic":
                b["congestion-controller"] = "bbr"
        out.append(b)
    names = [b["name"] for b in out]
    if not names:
        raise ValueError("No published nodes")
    return {"mixed-port": 2080, "allow-lan": False, "mode": "rule", "log-level": "warning",
            "proxies": out, "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["Auto"] + names},
            {"name": "Auto", "type": "url-test", "proxies": names, "url": "https://www.gstatic.com/generate_204", "interval": 300}],
            "rules": ["MATCH,Proxy"]}


def shadowrocket_uris(m):
    links = []
    host = f'[{m["address"]}]' if ":" in m["address"] else m["address"]
    for n in selected(m, "shadowrocket", True):
        p, title = n["protocol"], name(m, n)
        if p == "vmess":
            data = {"v": "2", "ps": title, "add": m["address"], "port": str(n["port"]), "id": n["uuid"],
                    "aid": "0", "scy": "auto", "net": "ws", "type": "none", "host": "", "path": "/spark", "tls": ""}
            links.append("vmess://" + base64.b64encode(json.dumps(data).encode()).decode())
            continue
        auth = n["uuid"] if p == "vless" else n["uuid"] + ":" + n["password"] if p == "tuic" else n["password"]
        query = {"sni": m["address"]}
        if p == "vless":
            r = n["reality_config"]
            query.update(encryption="none", security="reality", type="tcp", flow="xtls-rprx-vision",
                         sni=r["server_name"], fp="chrome", pbk=r["public_key"], sid=r["short_id"])
        if p in ("tuic", "hysteria2"):
            query["alpn"] = "h3"
        if p == "tuic":
            query.update(congestion_control="bbr", udp_relay_mode="native")
        links.append(f'{p}://{quote(auth, safe=":")}@{host}:{n["port"]}?{urlencode(query)}#{quote(title)}')
    if not links:
        raise ValueError("No published nodes")
    return "\n".join(links) + "\n"


def subscription(m, fmt):
    if fmt == "singbox":
        return json.dumps(singbox_config(m), ensure_ascii=False, indent=2).encode(), "application/json"
    if fmt == "clash":
        return yaml.safe_dump(clash_config(m), allow_unicode=True, sort_keys=False).encode(), "text/yaml; charset=utf-8"
    if fmt == "shadowrocket":
        return base64.b64encode(shadowrocket_uris(m).encode()), "text/plain; charset=utf-8"
    raise ValueError("Unknown subscription format")


def redacted_plan(m, old=None):
    def inventory(x):
        return {n["id"]: {k: n[k] for k in ("protocol", "port", "published", "priority", "transport")} for n in selected(x)} if x else {}
    before, after = inventory(old), inventory(m)
    return {"mode": m["mode"], "versions": VERSIONS, "added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()), "changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
            "nodes": after, "quota_bytes": m["quota_bytes"], "quota_action": "display-only",
            "private_config_changed": bool(old and m != old),
            "notes": ["VMess WS disabled unless explicitly acknowledged", "No cloud provisioning or billing", "No firewall ACCEPT rules; operator must authorize network access"]}
