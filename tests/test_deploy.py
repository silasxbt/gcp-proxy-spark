from pathlib import Path
import io
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from spark import deploy
from spark.core import atomic_json
from tests.helpers import fixture


class DeployTests(unittest.TestCase):
    def test_debian12_table_comment_fallback_is_top_level_only(self):
        from spark.traffic import nft_json
        doc = json.dumps({"nftables": [{"table": {"family": "inet", "name": "proxy_spark", "handle": 4}}]})
        for text, owned in [('table inet proxy_spark {\n\tcomment "gcp-proxy-spark-owned-v1"\n}', True),
                            ('table inet proxy_spark {\n chain x { comment "gcp-proxy-spark-owned-v1"\n}}', False)]:
            with patch('spark.traffic.subprocess.run', side_effect=[SimpleNamespace(returncode=0, stdout=doc),
                       SimpleNamespace(returncode=0, stdout=text)]):
                result = nft_json()
                self.assertEqual('comment' in result['nftables'][0]['table'], owned)

    def test_reviewed_coexistence_requires_fingerprint_and_disjoint_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.json"
            path.write_text(json.dumps({"inbounds": [{"listen_port": 65032}]}))
            m = fixture()
            self.assertTrue(deploy.legacy_issues(m, path))
            m["legacy_config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            m["exporter_port"] = 9106
            self.assertEqual(deploy.legacy_issues(m, path), [])
            self.assertIn("127.0.0.1:9106", deploy.nginx_config(m, True))
            self.assertIn("--port 9106", deploy.unit_files(m)["spark-web.service"])
            m["nodes"][0]["port"] = 65032
            self.assertTrue(deploy.legacy_issues(m, path))
            path.write_text("{}")
            self.assertTrue(deploy.legacy_issues(m, path))

    def test_health_reads_accounting_and_rejects_stale(self):
        for stale in (False, True):
            with patch.object(deploy, "check_cert"), patch.object(deploy, "active", return_value=True), \
                 patch.object(deploy, "run"), patch.object(deploy, "build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps({"stale": stale, "service_active": True}).encode())
                if stale:
                    with self.assertRaises(RuntimeError):
                        deploy.health(fixture())
                else:
                    deploy.health(fixture())

    def test_refuses_unconfirmed_apply(self):
        with self.assertRaises(ValueError):
            deploy.apply(fixture(), "test@example.com")

    def test_nginx_no_http_subscriptions_or_logging(self):
        http = deploy.nginx_config(fixture(), False)
        self.assertNotIn("proxy_pass", http)
        https = deploy.nginx_config(fixture(), True)
        self.assertIn("listen 443 ssl", https)
        self.assertIn("access_log off", https)
        self.assertIn("error_log /dev/null", https)
        self.assertIn("limit_req", https)
        self.assertNotIn("skip", https)

    def test_units_use_private_credentials_loopback_and_timers(self):
        units = deploy.unit_files()
        self.assertIn("DynamicUser=true", units["spark-web.service"])
        self.assertIn("LoadCredential=manifest:", units["spark-web.service"])
        self.assertIn("CAP_NET_ADMIN", units["spark-traffic.service"])
        self.assertIn("OnUnitActiveSec=1h", units["spark-renew.timer"])
        self.assertNotIn("--staging", units["spark-renew.service"])

    def transaction(self, fail):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "opt"
            base.mkdir()
            paths = {"BASE": base, "STATE": Path(tmp) / "state", "CURRENT": base / "current",
                     "NGINX": Path(tmp) / "nginx/spark.conf", "SYSTEMD": Path(tmp) / "systemd"}
            old = base / "releases/20260101T000000-aaaaaa"
            old.mkdir(parents=True)
            (old / "OWNED").write_text(deploy.MARKER)
            atomic_json(old / "manifest.json", fixture())
            paths["CURRENT"].symlink_to(old)
            paths["NGINX"].parent.mkdir()
            paths["NGINX"].write_text("original-nginx")
            calls = []

            def run(args, **kw):
                calls.append([str(a) for a in args])

            def fetch(tool, target):
                target.mkdir()
                (target / "sing-box").write_text("test-only")
                return target / "sing-box"

            with patch.multiple(deploy, **paths), patch.object(deploy, "fetch", side_effect=fetch), \
                 patch.object(deploy, "run", side_effect=run), patch.object(deploy, "active", return_value=True), \
                 patch.object(deploy, "check_cert"), patch("spark.traffic.collect"), \
                 patch.object(deploy.time, "sleep"), patch.object(deploy, "health", side_effect=RuntimeError("injected failure") if fail else None):
                if fail:
                    with self.assertRaises(RuntimeError):
                        deploy._apply(fixture(), "test@example.com")
                    self.assertEqual(paths["CURRENT"].resolve(), old.resolve())
                    self.assertEqual(paths["NGINX"].read_text(), "original-nginx")
                    self.assertTrue(old.exists())
                else:
                    result = deploy._apply(fixture(), "test@example.com")
                    self.assertEqual(result["status"], "healthy")
                    self.assertNotEqual(paths["CURRENT"].resolve(), old)
                    self.assertTrue(old.exists())
                    self.assertEqual((paths["CURRENT"] / "manifest.json").stat().st_mode & 0o777, 0o600)
                check = next(i for i, c in enumerate(calls) if "check" in c)
                restart = next(i for i, c in enumerate(calls) if c[:2] == ["systemctl", "restart"])
                self.assertLess(check, restart)

    def test_success_retains_old_release(self):
        self.transaction(False)

    def test_failed_health_restores_files_and_release(self):
        self.transaction(True)
