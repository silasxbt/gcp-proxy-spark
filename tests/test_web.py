from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from spark.core import atomic_json
from spark.traffic import advance, counter_key
from spark.web import handler
from tests.helpers import fixture


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = fixture()
        self.manifest = Path(self.tmp.name) / "manifest.json"
        self.state = Path(self.tmp.name) / "traffic.json"
        atomic_json(self.manifest, self.m)
        s = advance(None, self.m, {counter_key(self.m["nodes"][0], "upload"): 1234}, "test", time.time())
        atomic_json(self.state, s)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.manifest, self.state, status=lambda: True))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.sub = "/sub/" + self.m["subscription_token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def get(self, path, method="GET"):
        return build_opener(ProxyHandler({})).open(Request(self.url + path, method=method), timeout=5)

    def test_all_subscription_formats_and_headers(self):
        for fmt in ("singbox", "clash", "shadowrocket"):
            with self.get(self.sub + "?format=" + fmt) as r:
                self.assertEqual(r.status, 200)
                self.assertTrue(r.read())
                self.assertIn("upload=1234", r.headers["subscription-userinfo"])
                self.assertNotIn("expire=", r.headers["subscription-userinfo"])
                self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_invalid_token_404(self):
        with self.assertRaises(HTTPError) as e:
            self.get("/sub/invalid?format=clash")
        self.assertEqual(e.exception.code, 404)
        e.exception.close()

    def test_head_has_content_length_no_body(self):
        with self.get(self.sub, "HEAD") as r:
            self.assertGreater(int(r.headers["Content-Length"]), 0)
            self.assertEqual(r.read(), b"")

    def test_public_page_and_api_sanitized(self):
        for path in ("/traffic", "/api/traffic", "/traffic.js", "/traffic.css"):
            with self.get(path) as r:
                body = r.read()
                self.assertNotIn(self.m["subscription_token"].encode(), body)
                self.assertNotIn(b"192.0.2.1", body)

    def test_not_ready_503(self):
        self.state.unlink()
        with self.assertRaises(HTTPError) as e:
            self.get(self.sub)
        self.assertEqual(e.exception.code, 503)
        e.exception.close()

    def test_stale_blocks_subscription_but_public_reports(self):
        s = json.loads(self.state.read_text())
        s["updated_at"] -= 1000
        atomic_json(self.state, s)
        with self.assertRaises(HTTPError) as e:
            self.get(self.sub)
        self.assertEqual(e.exception.code, 503)
        e.exception.close()
        with self.get("/api/traffic") as r:
            self.assertTrue(json.load(r)["stale"])

    def test_bad_format_and_traversal(self):
        for suffix, code in (("?format=unknown", 400), ("?format=clash&format=singbox", 400), ("/../../manifest.json", 404)):
            with self.subTest(suffix=suffix), self.assertRaises(HTTPError) as e:
                self.get(self.sub + suffix)
            self.assertEqual(e.exception.code, code)
            e.exception.close()

    def test_over_quota_does_not_disable_subscription(self):
        s = json.loads(self.state.read_text())
        for month in s["months"].values():
            for key in month["counters"]:
                month["counters"][key] = self.m["quota_bytes"] + 100
        atomic_json(self.state, s)
        with self.get(self.sub) as r:
            self.assertEqual(r.status, 200)
