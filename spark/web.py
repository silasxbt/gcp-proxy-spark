"""Loopback-only subscription origin. Nginx terminates TLS and rate-limits."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import parse_qs, urlsplit

from .core import load, subscription
from .traffic import public_report


def handler(manifest, state_path, status=None):
    def service_status():
        if status is not None:
            return status()
        try:
            return subprocess.run(["systemctl", "is-active", "--quiet", "spark-proxy"], timeout=3).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return None

    class Handler(BaseHTTPRequestHandler):
        server_version = "Spark"
        sys_version = ""

        def log_message(self, *args):
            pass  # URL contains a bearer credential; never log it.

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, code, body=b"", mime="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlsplit(self.path)
            assets = {"/traffic": ("traffic.html", "text/html; charset=utf-8"),
                      "/traffic.js": ("traffic.js", "application/javascript"),
                      "/traffic.css": ("traffic.css", "text/css")}
            if url.path in assets:
                filename, mime = assets[url.path]
                self.reply(200, Path(__file__).with_name("assets").joinpath(filename).read_bytes(), mime)
                return
            is_sub = url.path.startswith("/sub/")
            if not is_sub and url.path != "/api/traffic":
                self.reply(404, b'{"error":"not found"}')
                return
            try:
                m = load(manifest)
                if is_sub:
                    token = url.path[len("/sub/"):]
                    if not hmac.compare_digest(token.encode(), m["subscription_token"].encode()):
                        self.reply(404, b'{"error":"not found"}')
                        return
                    query = parse_qs(url.query, keep_blank_values=True)
                    fmt = query.get("format", [m["mode"]])
                    if len(fmt) != 1 or fmt[0] not in ("singbox", "clash", "shadowrocket"):
                        self.reply(400, b'{"error":"invalid format"}')
                        return
                state = json.loads(Path(state_path).read_text())
                report = public_report(state, m, service_active=service_status())
                if not is_sub:
                    self.reply(200, json.dumps(report).encode())
                elif report["stale"] or report["service_active"] is not True:
                    self.reply(503, b'{"error":"service or accounting not ready"}')
                else:
                    body, mime = subscription(m, fmt[0])
                    self.reply(200, body, mime, {"subscription-userinfo": f'upload={report["upload"]}; download={report["download"]}; total={m["quota_bytes"]}',
                        "profile-update-interval": "1", "Content-Disposition": 'attachment; filename="spark-subscription"',
                        "X-Traffic-Reset-At": str(int(report["next_reset_at"]))})
            except (OSError, ValueError, KeyError, TypeError):
                self.reply(503, b'{"error":"not ready"}')
    return Handler


def serve(manifest, state, port=9105):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(manifest, state))
    server.daemon_threads = True
    server.serve_forever()
