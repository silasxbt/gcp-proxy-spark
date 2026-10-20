"""Owned nftables inet counters: both IP families, no filtering, no shaping."""
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time

from .core import atomic_json, load, selected

TABLE = "proxy_spark"


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


def next_month(ts):
    d = utc(ts)
    return datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc).timestamp()


def counter_key(n, direction):
    # Port/protocol changes create a fresh identity instead of reusing a baseline.
    digest = hashlib.sha256(f'{n["id"]}:{n["protocol"]}:{n["port"]}'.encode()).hexdigest()[:16]
    return f"c_{digest}_{direction}"


def nft_json():
    r = subprocess.run(["nft", "-a", "-j", "list", "table", "inet", TABLE], capture_output=True, text=True)
    if r.returncode:
        # Do not confuse permission/kernel failures with an absent table.
        if "No such file or directory" in r.stderr:
            return None
        raise RuntimeError("Cannot read owned nftables table; check CAP_NET_ADMIN/kernel")
    return json.loads(r.stdout)


def ensure_counters(m):
    existing = nft_json()
    entries = existing["nftables"] if existing else []
    if existing:
        table = next(x["table"] for x in entries if "table" in x)
        if table.get("comment") != "gcp-proxy-spark-owned-v1":
            raise RuntimeError("nftables table name conflict; refusing adoption")
    counters = {x["counter"]["name"] for x in entries if "counter" in x}
    rules = [x["rule"] for x in entries if "rule" in x]
    desired, commands = {}, []
    if not existing:
        commands += [f'add table inet {TABLE} {{ comment "gcp-proxy-spark-owned-v1"; }}',
                     f'add chain inet {TABLE} ingress {{ type filter hook input priority -10; policy accept; }}',
                     f'add chain inet {TABLE} egress {{ type filter hook output priority -10; policy accept; }}']
    for n in selected(m):
        proto = "udp" if n["protocol"] in ("tuic", "hysteria2") else "tcp"
        for direction, chain, port in (("upload", "ingress", "dport"), ("download", "egress", "sport")):
            key = counter_key(n, direction)
            desired[key] = (chain, f'{proto} {port} {n["port"]} counter name {key} comment "{key}"')
            if key not in counters:
                commands.append(f"add counter inet {TABLE} {key}")
    present = set()
    for rule in rules:
        comment = rule.get("comment", "")
        if comment not in desired or comment in present:
            commands.append(f'delete rule inet {TABLE} {rule["chain"]} handle {rule["handle"]}')
        else:
            present.add(comment)
    for key, (chain, expression) in desired.items():
        if key not in present:
            commands.append(f"add rule inet {TABLE} {chain} {expression}")
    if commands:
        # One nft transaction; existing named counters are not reset.
        subprocess.run(["nft", "-f", "-"], input="\n".join(commands) + "\n", text=True, check=True, capture_output=True)


def snapshot(m):
    doc = nft_json()
    if doc is None:
        raise RuntimeError("Counters unavailable")
    table = next(x["table"] for x in doc["nftables"] if "table" in x)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    values = {}
    # Retained counters for removed nodes are still sampled to preserve their final delta.
    for x in doc["nftables"]:
        if "counter" in x:
            c = x["counter"]
            values[c["name"]] = c["bytes"]
    return values, f'{boot}:{table["handle"]}'


def month_parts(start, end):
    if start >= end:
        return [(utc(end).strftime("%Y-%m"), 1)]
    parts = []
    while start < end:
        stop = min(next_month(start), end)
        parts.append((utc(start).strftime("%Y-%m"), stop - start))
        start = stop
    return parts


def advance(previous, m, counters, generation, now):
    state = copy.deepcopy(previous) if previous else {"schema": 1, "months": {}, "last": {}, "nodes": {}, "updated_at": now}
    if state.get("schema") != 1 or now < state["updated_at"]:
        raise ValueError("Invalid state or clock moved backwards; do not discard history")
    for n in selected(m):
        for direction in ("upload", "download"):
            state["nodes"][counter_key(n, direction)] = {"id": n["id"], "protocol": n["protocol"], "direction": direction}
    parts = month_parts(state["updated_at"], now)
    total_time = sum(duration for _, duration in parts)
    reset = state.get("generation") != generation
    for key, value in counters.items():
        if key not in state["nodes"] or type(value) is not int or value < 0:
            continue
        before = state["last"].get(key, 0)
        delta = value if reset or value < before else value - before
        allocated = 0
        for index, (month, duration) in enumerate(parts):
            amount = delta - allocated if index == len(parts) - 1 else int(delta * duration / total_time)
            allocated += amount
            bucket = state["months"].setdefault(month, {"counters": {}, "boundary_estimated": False})
            bucket["counters"][key] = bucket["counters"].get(key, 0) + amount
            bucket["boundary_estimated"] |= len(parts) > 1
        state["last"][key] = value
    state.update(generation=generation, updated_at=now)
    return state


def public_report(state, m, now=None, service_active=None):
    now = time.time() if now is None else now
    month = utc(now).strftime("%Y-%m")
    bucket = state["months"].get(month, {"counters": {}, "boundary_estimated": False})
    groups = {}
    for key, count in bucket["counters"].items():
        meta = state["nodes"][key]
        group = groups.setdefault(meta["protocol"], {"upload": 0, "download": 0})
        group[meta["direction"]] += count
    upload = sum(n["upload"] for n in groups.values())
    download = sum(n["download"] for n in groups.values())
    age = max(0, now - state["updated_at"])
    return {"month": month, "timezone": "UTC", "upload": upload, "download": download,
            "total": upload + download, "quota_bytes": m["quota_bytes"], "over_reference": upload + download >= m["quota_bytes"],
            "quota_action": "display-only", "updated_at": state["updated_at"], "stale": age > 180,
            "service_active": service_active, "next_reset_at": next_month(now),
            "boundary_estimated": bucket["boundary_estimated"], "protocols": groups}


def collect(manifest, path, once=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            m = load(manifest)
            ensure_counters(m)
            values, generation = snapshot(m)
            old = json.loads(path.read_text()) if path.exists() else None
            atomic_json(path, advance(old, m, values, generation, time.time()), 0o644)
            if once:
                return
            time.sleep(10)
