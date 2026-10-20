"""Explicit, non-interactive operations suitable for humans and Agents."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .core import atomic_json, load, new_manifest, redacted_plan, server_config, subscription


def main():
    parser = argparse.ArgumentParser(description="Spark: inspect → plan → confirm → apply → verify")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="Download a checksum-pinned core")
    f.add_argument("tool", choices=("singbox", "mihomo"))
    f.add_argument("--directory", required=True)
    n = sub.add_parser("init", help="Generate fresh private credentials; never overwrites")
    for key in ("address", "region", "binary", "output", "reality-host"):
        n.add_argument("--" + key, required=True)
    n.add_argument("--mode", choices=("singbox", "clash"), default="singbox")
    for command in ("plan", "inspect", "apply", "render", "urls", "serve", "collect"):
        p = sub.add_parser(command)
        p.add_argument("--manifest", required=True)
        if command == "plan":
            p.add_argument("--previous")
        if command == "apply":
            p.add_argument("--email", required=True)
            p.add_argument("--confirm", action="store_true")
            p.add_argument("--network-confirmed", action="store_true")
        if command == "render":
            p.add_argument("--format", choices=("server", "singbox", "clash", "shadowrocket"), required=True)
            p.add_argument("--output", required=True)
        if command in ("serve", "collect"):
            p.add_argument("--state", required=True)
        if command == "serve":
            p.add_argument("--port", type=int, default=9105)
        if command == "collect":
            p.add_argument("--once", action="store_true")
    p = sub.add_parser("rollback")
    p.add_argument("release")
    p.add_argument("--confirm", action="store_true")
    p = sub.add_parser("uninstall")
    p.add_argument("--confirm", action="store_true")
    sub.add_parser("cert-check")
    a = parser.parse_args()
    try:
        if a.command == "fetch":
            from .download import fetch
            print(fetch(a.tool, a.directory))
            return
        if a.command == "init":
            if Path(a.output).exists():
                raise ValueError("Output exists; credentials will not be overwritten")
            atomic_json(a.output, new_manifest(a.address, a.region, a.mode, a.binary, a.reality_host))
            print("Private manifest created (0600). Do not commit or share it.")
            return
        if a.command in ("rollback", "uninstall", "cert-check"):
            from . import deploy
            if a.command == "rollback":
                deploy.rollback(a.release, a.confirm)
            elif a.command == "uninstall":
                print(json.dumps(deploy.uninstall(a.confirm)))
            else:
                deploy.check_cert()
            return
        m = load(a.manifest)
        if a.command == "plan":
            print(json.dumps(redacted_plan(m, load(a.previous) if a.previous else None), ensure_ascii=False, indent=2))
        elif a.command == "inspect":
            from .deploy import inspect
            result = inspect(m)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["ready"]:
                sys.exit(1)
        elif a.command == "apply":
            from .deploy import apply
            print(json.dumps(apply(m, a.email, a.confirm, a.network_confirmed)))
        elif a.command == "render":
            from .deploy import write_text
            if a.format == "server":
                atomic_json(a.output, server_config(m))
            else:
                body, _ = subscription(m, a.format)
                write_text(a.output, body.decode(), 0o600)
            print("Private configuration written (0600).")
        elif a.command == "urls":
            # Explicit opt-in command. Never automatically print bearer URLs on deploy.
            host = f'[{m["address"]}]' if ":" in m["address"] else m["address"]
            for fmt in ("singbox", "clash", "shadowrocket"):
                print(f'https://{host}/sub/{m["subscription_token"]}?format={fmt}')
        elif a.command == "serve":
            from .web import serve
            serve(a.manifest, a.state, a.port)
        elif a.command == "collect":
            from .traffic import collect
            collect(a.manifest, a.state, a.once)
    except subprocess.CalledProcessError:
        # Native diagnostics can contain private config; do not echo stdout/stderr.
        parser.exit(1, "Operation failed. Inspect local service diagnostics privately; credentials were not printed.\n")
    except (ValueError, OSError, RuntimeError) as e:
        parser.exit(1, f"{e}\n")


if __name__ == "__main__":
    main()
