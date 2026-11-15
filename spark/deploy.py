"""Conservative local-host installer. Cloud-agnostic: never invokes cloud CLIs or provisions resources."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
from urllib.request import build_opener, ProxyHandler

from .core import CERT, atomic_json, load, selected, server_config
from .download import fetch

BASE = Path("/opt/gcp-proxy-spark")
STATE = Path("/var/lib/gcp-proxy-spark")
CURRENT = BASE / "current"
NGINX = Path("/etc/nginx/conf.d/proxy-spark.conf")
SYSTEMD = Path("/etc/systemd/system")
UNITS = ("spark-proxy.service", "spark-web.service", "spark-traffic.service",
         "spark-renew.service", "spark-renew.timer", "spark-cert-check.service", "spark-cert-check.timer")
MARKER = "# Managed by gcp-proxy-spark v1"
TABLE_NAME = "proxy_spark"


def legacy_issues(m, path=Path("/etc/s-box/sb.json")):
    if not path.exists():
        return ["Reviewed legacy config is missing"] if m.get("legacy_config_sha256") else []
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != m.get("legacy_config_sha256"):
        return ["Existing sing-box-yg deployment: explicitly review coexistence and pin its config SHA256"]
    try:
        legacy_ports = {n["listen_port"] for n in json.loads(raw)["inbounds"] if "listen_port" in n}
    except (ValueError, KeyError, TypeError):
        return ["Cannot inspect reviewed legacy listener configuration"]
    required = {80, 443, m.get("exporter_port", 9105)} | {n["port"] for n in selected(m)}
    return ["Legacy listener configuration overlaps Spark ports"] if legacy_ports & required else []


def required_ingress(m):
    return [("tcp", 80), ("tcp", 443)] + [("udp" if n["protocol"] in ("tuic", "hysteria2") else "tcp", n["port"])
                                         for n in selected(m)]


def _ports(spec):
    out = set()
    for part in spec.split(","):
        low, _, high = part.partition(":")
        out |= set(range(int(low), int(high or low) + 1))
    return out


def iptables_verdict(save_text, proto, port):
    """First-match verdict of filter/INPUT for a NEW packet from any source: allowed, blocked or unknown."""
    import shlex
    policy, rules, table = "ACCEPT", [], None
    for line in save_text.splitlines():
        if line.startswith("*"):
            table = line[1:].strip()
        elif table == "filter" and line.startswith(":INPUT "):
            policy = line.split()[1]
        elif table == "filter" and line.startswith("-A INPUT "):
            rules.append(shlex.split(line)[2:])
    uncertain = False
    for tokens in rules:
        rule_proto, dports, states, target, other = None, None, None, None, False
        i = 0
        while i < len(tokens):
            key = tokens[i]
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            if key in ("-p", "--protocol"):
                rule_proto = value
            elif key in ("--dport", "--dports", "--destination-port", "--destination-ports"):
                dports = _ports(value)
            elif key in ("--state", "--ctstate"):
                states = set(value.split(","))
            elif key in ("-j", "--jump"):
                target = value
            elif key == "-m" or key in ("--comment", "--reject-with"):
                pass
            else:
                other = True
                i += 1 if key.startswith("!") else 0
            i += 2
        if rule_proto not in (None, "all", proto) or (dports is not None and port not in dports) \
                or (states is not None and "NEW" not in states):
            continue
        if other:
            # Interface/source/negated selectors: cannot decide statically; a blocking rule makes ACCEPT uncertain.
            uncertain |= target in ("REJECT", "DROP")
            continue
        if target == "ACCEPT":
            return "unknown" if uncertain else "allowed"
        if target in ("REJECT", "DROP"):
            return "blocked"
        return "unknown"  # Jump to a user chain (e.g. ufw) or another target.
    if policy == "ACCEPT":
        return "unknown" if uncertain else "allowed"
    return "blocked" if policy == "DROP" else "unknown"


def host_firewall(m):
    """Detect host rules that would block planned ingress. Only reports; never adds ACCEPT rules."""
    import ipaddress
    issues, warnings = [], []
    tool = "ip6tables-save" if ipaddress.ip_address(m["address"]).version == 6 else "iptables-save"
    if shutil.which(tool):
        try:
            text = run([tool, "-t", "filter"]).stdout
        except subprocess.CalledProcessError:
            text = None
            warnings.append(f"Cannot read {tool}; verify host firewall manually")
        if text is not None:
            verdicts = {f"{proto}:{port}": iptables_verdict(text, proto, port) for proto, port in required_ingress(m)}
            blocked = sorted(k for k, v in verdicts.items() if v == "blocked")
            unknown = sorted(k for k, v in verdicts.items() if v == "unknown")
            if blocked:
                issues.append("Host firewall (iptables INPUT) rejects: " + ", ".join(blocked)
                              + "; allow only these ports with operator authorization (see docs/deployment.md)")
            if unknown:
                warnings.append("Host firewall verdict unknown (user chains such as ufw): " + ", ".join(unknown))
    if shutil.which("nft"):
        try:
            doc = json.loads(run(["nft", "-j", "list", "ruleset"]).stdout)
        except (subprocess.CalledProcessError, ValueError):
            doc = {"nftables": []}
        for x in doc.get("nftables", []):
            c = x.get("chain", {})
            if c.get("hook") == "input" and c.get("policy") == "drop" and c.get("table") != TABLE_NAME \
                    and c.get("table") != "filter":
                warnings.append(f'nftables chain {c.get("family")} {c.get("table")} {c.get("name")} has policy drop; verify planned ports are accepted')
    return issues, warnings


def run(args, **kw):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True, text=True, **kw)


def active(unit):
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], capture_output=True).returncode == 0


def inspect(m):
    issues = []
    if platform.system() != "Linux":
        return {"ready": False, "issues": ["Deployment requires Debian/Ubuntu Linux"], "network": "not checked"}
    os_release = Path("/etc/os-release").read_text()
    if not re.search(r'^ID=(?:"?)(debian|ubuntu)(?:"?)$', os_release, re.M):
        issues.append("Only Debian/Ubuntu supported")
    if platform.machine() not in ("x86_64", "aarch64"):
        issues.append("Only amd64/arm64 supported")
    for exe in ("nft", "nginx", "systemctl", "ss", "openssl", "curl"):
        if not shutil.which(exe):
            issues.append(f"Missing prerequisite: {exe}")
    certbot = BASE / "certbot/bin/certbot"
    if not certbot.exists():
        issues.append("Missing pinned certbot environment; see docs/deployment.md")
    elif "5.4.0" not in run([certbot, "--version"]).stdout:
        issues.append("Expected Certbot 5.4.0")
    if not (BASE / "venv/bin/python").exists():
        issues.append("Missing runtime venv")
    issues.extend(legacy_issues(m))
    if CURRENT.exists() and (not CURRENT.is_symlink() or not (CURRENT / "OWNED").is_file()):
        issues.append("Unowned current release")
    if NGINX.exists() and not NGINX.read_text().startswith(MARKER):
        issues.append("Unowned nginx configuration")
    if Path(CERT).parent.exists() and not (BASE / "OWNED").exists():
        issues.append("Existing ACME certificate name is not owned by Spark")
    if shutil.which("nft"):
        from .traffic import nft_json
        try:
            table = nft_json()
            if table and not any(x.get("table", {}).get("comment") == "gcp-proxy-spark-owned-v1" for x in table["nftables"]):
                issues.append("Unowned nftables table proxy_spark")
        except RuntimeError as error:
            issues.append(str(error))
    for unit in UNITS:
        p = SYSTEMD / unit
        if p.exists() and not p.read_text().startswith(MARKER):
            issues.append(f"Unowned systemd unit: {unit}")
    sites = Path("/etc/nginx/sites-enabled")
    if sites.exists() and list(sites.iterdir()):
        issues.append("Existing nginx sites-enabled entries: review/remove or relocate manually; no automatic overwrite")
    confs = Path("/etc/nginx/conf.d")
    if confs.exists() and any(p != NGINX for p in confs.glob("*.conf")):
        issues.append("Other nginx conf.d files exist; shared nginx requires manual integration")
    if shutil.which("ss"):
        sockets = run(["ss", "-H", "-lntup"]).stdout
        old = load(CURRENT / "manifest.json") if CURRENT.exists() and (CURRENT / "manifest.json").exists() else None
        allowed = {n["port"] for n in selected(old)} if old else set()
        ports = {80, 443, m.get("exporter_port", 9105)} | {n["port"] for n in selected(m)}
        for line in sockets.splitlines():
            cols = line.split()
            match = re.search(r":(\d+)$", cols[4]) if len(cols) > 4 else None
            port = int(match[1]) if match else None
            if port not in ports:
                continue
            owned = old and ((port in (80, 443) and '"nginx"' in line)
                             or (port == old.get("exporter_port", 9105) and active("spark-web"))
                             or (port in allowed and active("spark-proxy")))
            if not owned:
                issues.append(f"Occupied port: {port}; no process will be stopped automatically")
    firewall_issues, warnings = host_firewall(m)
    issues.extend(firewall_issues)
    return {"ready": not issues, "issues": issues, "warnings": warnings,
            "network": "Cloud ingress (GCP firewall / AWS security group / OCI security list or NSG), fixed public IP and external reachability must be checked from operator machine",
            "required_ingress": [f"{proto}:{port}" for proto, port in required_ingress(m)]}


def nginx_config(m, tls):
    # No URL logging even on errors: subscription credentials live in the path.
    head = MARKER + "\nlimit_req_zone $binary_remote_addr zone=spark_rate:10m rate=2r/s;\n"
    http = """server {
 listen 80;
 listen [::]:80;
 server_name _;
 access_log off;
 error_log /dev/null crit;
 location /.well-known/acme-challenge/ { root /var/lib/gcp-proxy-spark/acme; }
 location / { return 404; }
}
"""
    if not tls:
        return head + http
    https = """server {
 listen 443 ssl;
 listen [::]:443 ssl;
 server_name _;
 ssl_certificate /etc/letsencrypt/live/proxy-spark/fullchain.pem;
 ssl_certificate_key /etc/letsencrypt/live/proxy-spark/privkey.pem;
 ssl_protocols TLSv1.2 TLSv1.3;
 access_log off;
 error_log /dev/null crit;
 client_max_body_size 1k;
 add_header Referrer-Policy no-referrer always;
 location / {
   limit_except GET HEAD { deny all; }
   limit_req zone=spark_rate burst=20 nodelay;
   proxy_pass http://127.0.0.1:9105;
   proxy_set_header Host localhost;
   proxy_read_timeout 15s;
 }
}
"""
    return head + http + https.replace("127.0.0.1:9105", f'127.0.0.1:{m.get("exporter_port", 9105)}')


def unit_files(m=None):
    port = (m or {}).get("exporter_port", 9105)
    python = f"{BASE}/venv/bin/python"
    manifest = f"{CURRENT}/manifest.json"
    common = f"""[Service]
WorkingDirectory={CURRENT}
UMask=0077
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
PrivateTmp=true
"""
    files = {}
    commands = {"spark-proxy.service": f"{CURRENT}/bin/sing-box run -c {CURRENT}/server.json",
                "spark-web.service": f"{python} -m spark.cli serve --manifest {manifest} --state {STATE}/traffic.json",
                "spark-traffic.service": f"{python} -m spark.cli collect --manifest {manifest} --state {STATE}/traffic.json"}
    for unit, command in commands.items():
        extra = ""
        if unit == "spark-traffic.service":
            extra = f"ReadWritePaths={STATE}\nCapabilityBoundingSet=CAP_NET_ADMIN\n"
        elif unit == "spark-web.service":
            # DynamicUser gets its manifest as a systemd credential, not world-readable config.
            extra = f"DynamicUser=true\nLoadCredential=manifest:{manifest}\n"
            command = f"{python} -m spark.cli serve --manifest %d/manifest --state {STATE}/traffic.json --port {port}"
        else:
            extra = "CapabilityBoundingSet=\n"
        files[unit] = MARKER + "\n[Unit]\nDescription=Spark " + unit + "\nAfter=network-online.target\nWants=network-online.target\n" + common + extra + f"ExecStart={command}\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n"
    files["spark-renew.service"] = MARKER + f"\n[Unit]\nDescription=Renew Spark IP certificate\n[Service]\nType=oneshot\nExecStart={BASE}/certbot/bin/certbot renew --cert-name proxy-spark --quiet\n"
    files["spark-cert-check.service"] = MARKER + f"\n[Unit]\nDescription=Check Spark certificate expiry\n[Service]\nType=oneshot\nWorkingDirectory={CURRENT}\nExecStart={python} -m spark.cli cert-check\n"
    for stem in ("spark-renew", "spark-cert-check"):
        files[stem + ".timer"] = MARKER + f"\n[Unit]\nDescription={stem} every hour\n[Timer]\nOnBootSec=5m\nOnUnitActiveSec=1h\nRandomizedDelaySec=5m\nPersistent=true\n[Install]\nWantedBy=timers.target\n"
    return files


def write_text(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), mode)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def switch(target):
    tmp = BASE / ".current-next"
    if tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, CURRENT)


def check_cert():
    # Fail before expiry so operator can act; never disable client certificate checking.
    run(["openssl", "x509", "-in", CERT, "-noout", "-checkend", "86400"])


def health(m):
    check_cert()
    for service in ("spark-proxy", "spark-web", "spark-traffic"):
        if not active(service):
            raise RuntimeError(f"Service not active: {service}")
    # Verify local Nginx, certificate chain AND IP SAN without depending on NAT hairpin.
    host = f'[{m["address"]}]' if ":" in m["address"] else m["address"]
    run(["curl", "--fail", "--silent", "--show-error", "--noproxy", "*", "--connect-to",
         f"{host}:443:127.0.0.1:443", f"https://{host}/api/traffic"], timeout=15)
    with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{m.get("exporter_port", 9105)}/api/traffic', timeout=5) as r:
        report = json.load(r)
    if report["stale"] or not report["service_active"]:
        raise RuntimeError("Accounting/service is not healthy")


def wait_healthy(m):
    for attempt in range(10):
        try:
            health(m)
            return
        except Exception:
            if attempt == 9:
                raise
            time.sleep(2)


def apply(m, email, confirmed=False, network_confirmed=False):
    if not confirmed or not network_confirmed:
        raise ValueError("Require --confirm and --network-confirmed after reviewing plan, cloud and host ingress and fixed public IP")
    if os.geteuid() != 0:
        raise ValueError("Run deployment as root on the target machine")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Valid ACME email required")
    import ipaddress
    if not ipaddress.ip_address(m["address"]).is_global:
        raise ValueError("Production certificate requires a public IP")
    checks = inspect(m)
    if not checks["ready"]:
        raise ValueError("; ".join(checks["issues"]))
    BASE.mkdir(parents=True, exist_ok=True)
    with (BASE / ".deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _apply(m, email)


def _apply(m, email):
    old = CURRENT.resolve() if CURRENT.exists() else None
    old_m = load(old / "manifest.json") if old else None
    if old_m and old_m["address"] != m["address"]:
        raise ValueError("IP change requires separate certificate migration; not supported in-place")
    write_text(BASE / "OWNED", MARKER)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o755)
    (STATE / "acme").mkdir(exist_ok=True, mode=0o755)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    release = BASE / "releases" / (stamp + "-" + os.urandom(3).hex())
    release.mkdir(parents=True, mode=0o755)
    shutil.copytree(Path(__file__).parent, release / "spark", ignore=shutil.ignore_patterns("__pycache__"))
    write_text(release / "OWNED", MARKER)
    atomic_json(release / "manifest.json", m)
    atomic_json(release / "server.json", server_config(m))
    binary = fetch("singbox", release / "bin")
    original_files = {NGINX: NGINX.read_bytes() if NGINX.exists() else None}
    for unit in UNITS:
        p = SYSTEMD / unit
        original_files[p] = p.read_bytes() if p.exists() else None
    backup = release / "backup"
    backup.mkdir(mode=0o700)
    for i, (p, data) in enumerate(original_files.items()):
        if data is not None:
            write_text(backup / str(i), data.decode(), 0o600)
    atomic_json(backup / "index.json", [str(p) for p in original_files])
    previous_nginx_active = active("nginx")
    activated = False
    try:
        if not old:
            write_text(NGINX, nginx_config(m, False))
            run(["nginx", "-t"])
            run(["systemctl", "reload" if active("nginx") else "start", "nginx"])
        hook = BASE / "renew-hook.sh"
        write_text(hook, "#!/bin/sh\nset -eu\n/usr/sbin/nginx -t\n/bin/systemctl reload nginx\nif /bin/systemctl is-active --quiet spark-proxy; then /bin/systemctl restart spark-proxy; fi\n", 0o700)
        run([BASE / "certbot/bin/certbot", "certonly", "--non-interactive", "--agree-tos", "--email", email,
             "--cert-name", "proxy-spark", "--preferred-profile", "shortlived", "--webroot", "--webroot-path", STATE / "acme",
             "--ip-address", m["address"], "--deploy-hook", hook], timeout=180)
        check_cert()
        # Never switch active release before the native server validator succeeds.
        run([binary, "check", "-c", release / "server.json"])
        write_text(NGINX, nginx_config(m, True))
        run(["nginx", "-t"])
        write_text(release / "nginx.conf", nginx_config(m, True))
        for unit, body in unit_files(m).items():
            write_text(release / "units" / unit, body)
            write_text(SYSTEMD / unit, body)
        # Snapshot old counters before changing ports/configs.
        if old:
            run(["systemctl", "stop", "spark-traffic"])
            from .traffic import collect
            collect(old / "manifest.json", STATE / "traffic.json", once=True)
        switch(release)
        activated = True
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "spark-proxy", "spark-web", "spark-traffic", "spark-renew.timer", "spark-cert-check.timer"])
        run(["systemctl", "restart", "spark-proxy", "spark-traffic", "spark-web"])
        run(["systemctl", "start", "spark-renew.timer", "spark-cert-check.timer"])
        run(["systemctl", "reload", "nginx"])
        wait_healthy(m)
        return {"release": release.name, "status": "healthy", "previous": old.name if old else None}
    except Exception:
        # Keep failed release + private backups for diagnosis; don't delete data.
        if activated:
            run(["systemctl", "stop", "spark-proxy", "spark-web", "spark-traffic"])
            if not old:
                run(["systemctl", "disable", "--now", "spark-renew.timer", "spark-cert-check.timer",
                     "spark-proxy", "spark-web", "spark-traffic"])
        for p, data in original_files.items():
            if data is None:
                p.unlink(missing_ok=True)
            else:
                write_text(p, data.decode())
        if old:
            switch(old)
        elif CURRENT.is_symlink():
            CURRENT.unlink()
        run(["systemctl", "daemon-reload"])
        if old:
            run(["systemctl", "restart", "spark-proxy", "spark-traffic", "spark-web"])
        if previous_nginx_active:
            run(["nginx", "-t"])
            run(["systemctl", "reload", "nginx"])
        else:
            run(["systemctl", "stop", "nginx"])
        if old:
            wait_healthy(old_m)
        raise


def rollback(release_name, confirmed=False):
    if not confirmed or os.geteuid() != 0 or not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{6}", release_name):
        raise ValueError("Root, valid release id and --confirm required")
    target = BASE / "releases" / release_name
    if not (target / "OWNED").exists() or target.is_symlink():
        raise ValueError("Not an owned release")
    old = CURRENT.resolve()
    if load(old / "manifest.json")["address"] != load(target / "manifest.json")["address"]:
        raise ValueError("Cannot rollback across IP changes")
    with (BASE / ".deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run([target / "bin/sing-box", "check", "-c", target / "server.json"])
        run(["systemctl", "stop", "spark-traffic"])
        from .traffic import collect
        collect(old / "manifest.json", STATE / "traffic.json", once=True)
        try:
            switch(target)
            for unit in UNITS:
                write_text(SYSTEMD / unit, (target / "units" / unit).read_text())
            write_text(NGINX, (target / "nginx.conf").read_text())
            run(["nginx", "-t"])
            run(["systemctl", "daemon-reload"])
            run(["systemctl", "restart", "spark-proxy", "spark-web", "spark-traffic"])
            run(["systemctl", "reload", "nginx"])
            wait_healthy(load(target / "manifest.json"))
        except Exception:
            switch(old)
            for unit in UNITS:
                write_text(SYSTEMD / unit, (old / "units" / unit).read_text())
            write_text(NGINX, (old / "nginx.conf").read_text())
            run(["systemctl", "daemon-reload"])
            run(["systemctl", "restart", "spark-proxy", "spark-web", "spark-traffic"])
            run(["systemctl", "reload", "nginx"])
            wait_healthy(load(old / "manifest.json"))
            raise


def uninstall(confirmed=False):
    if not confirmed or os.geteuid() != 0 or not (CURRENT / "OWNED").exists():
        raise ValueError("Root, owned installation and --confirm required")
    with (BASE / ".deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _uninstall()


def _uninstall():
    # No recursive deletion; preserve releases, certificates and historical data.
    for unit in UNITS:
        p = SYSTEMD / unit
        if p.exists() and not p.read_text().startswith(MARKER):
            raise ValueError("Refusing to remove unowned unit")
    if NGINX.exists() and not NGINX.read_text().startswith(MARKER):
        raise ValueError("Refusing to remove unowned nginx config")
    if active("spark-traffic"):
        run(["systemctl", "stop", "spark-traffic"])
        from .traffic import collect
        collect(CURRENT / "manifest.json", STATE / "traffic.json", once=True)
    for unit in UNITS:
        run(["systemctl", "disable", "--now", unit])
        (SYSTEMD / unit).unlink(missing_ok=True)
    NGINX.unlink(missing_ok=True)
    from .traffic import nft_json
    table = nft_json()
    if table and any(x.get("table", {}).get("comment") == "gcp-proxy-spark-owned-v1" for x in table["nftables"]):
        run(["nft", "delete", "table", "inet", "proxy_spark"])
    run(["systemctl", "daemon-reload"])
    run(["nginx", "-t"])
    if active("nginx"):
        run(["systemctl", "reload", "nginx"])
    return {"removed": "owned units, nginx site and counter table", "preserved": "all releases, credentials, certificates and traffic history"}
