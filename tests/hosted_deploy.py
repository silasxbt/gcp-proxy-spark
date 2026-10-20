"""Real systemd/nginx lifecycle on disposable GitHub-hosted runner ONLY.

CA issuance is simulated with a local trust anchor. This does NOT verify public ACME.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from spark import deploy
from spark.core import new_manifest


def main():
    if os.geteuid() != 0 or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Refusing: only disposable GitHub-hosted runner may run this test")
    binary = (Path.cwd() / ".cache/bin/sing-box").resolve()
    if deploy.BASE.exists():
        raise RuntimeError("Refusing existing installation")
    deploy.BASE.mkdir()
    subprocess.run(["/usr/bin/python3", "-m", "venv", str(deploy.BASE / "venv")], check=True)
    subprocess.run([str(deploy.BASE / "venv/bin/pip"), "install", "PyYAML==6.0.3"], check=True)
    default = Path("/etc/nginx/sites-enabled/default")
    if default.is_symlink():
        default.unlink()  # Disposable runner's freshly installed stock site only.
    subprocess.run(["systemctl", "stop", "nginx"], check=True)
    m = new_manifest("192.0.2.1", "CI-ONLY", "singbox", binary, "example.com")
    cert_dir = Path("/etc/letsencrypt/live/proxy-spark")
    cert_dir.mkdir(parents=True)
    conf = deploy.BASE / "test-openssl.cnf"
    conf.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=CI-ONLY\n[ext]\nsubjectAltName=IP:192.0.2.1\n")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(cert_dir / "privkey.pem"),
                    "-out", str(cert_dir / "fullchain.pem"), "-days", "2", "-config", str(conf)], check=True, capture_output=True)
    os.environ["CURL_CA_BUNDLE"] = str(cert_dir / "fullchain.pem")
    real_run = deploy.run

    def run(args, **kwargs):
        if str(args[0]).endswith("certbot/bin/certbot"):
            assert "--ip-address" in args and "--preferred-profile" in args
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    def fetch(tool, dest):
        dest.mkdir()
        return Path(shutil.copy2(binary, dest / "sing-box"))

    try:
        with patch.object(deploy, "run", side_effect=run), patch.object(deploy, "fetch", side_effect=fetch):
            first = deploy._apply(m, "test@example.com")
            print("PASS real systemd + nginx installation, TLS SAN/chain, private credential loading", flush=True)
            old_release = deploy.CURRENT.resolve()
            updated = copy.deepcopy(m)
            updated["region"] = "CI-UPGRADE"
            second = deploy._apply(updated, "test@example.com")
            assert second["previous"] == first["release"]
            assert old_release.exists()
            print("PASS real upgrade preserving previous release", flush=True)
            deploy.rollback(first["release"], confirmed=True)
            assert deploy.CURRENT.resolve() == old_release
            print("PASS real explicit rollback", flush=True)
            real_health = deploy.health
            failures = 0
            def broken_candidate(manifest):
                nonlocal failures
                failures += 1
                if failures <= 10:
                    raise RuntimeError("injected candidate health failure")
                return real_health(manifest)
            with patch.object(deploy, "health", side_effect=broken_candidate):
                try:
                    deploy._apply(updated, "test@example.com")
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Expected failure")
            assert deploy.CURRENT.resolve() == old_release
            deploy.health(m)
            print("PASS failed upgrade restores running old services", flush=True)
            subprocess.run([str(deploy.BASE / "renew-hook.sh")], check=True)
            time.sleep(2)
            deploy.health(m)
            print("PASS real certificate deploy hook reload/restart (CA issuance simulated)", flush=True)
            state = deploy.STATE / "traffic.json"
            assert state.exists()
            deploy.uninstall(confirmed=True)
            assert state.exists() and old_release.exists()
            assert not deploy.NGINX.exists()
            assert not deploy.active("spark-proxy")
            print("PASS uninstall preserves private releases and statistics", flush=True)
    except Exception:
        # Only synthetic CI credentials exist. Still show status not full configs.
        subprocess.run(["systemctl", "status", "spark-proxy", "spark-traffic", "spark-web", "--no-pager"])
        subprocess.run(["journalctl", "-u", "spark-traffic", "-u", "spark-web", "--no-pager", "-n", "40"])
        raise


if __name__ == "__main__":
    main()
