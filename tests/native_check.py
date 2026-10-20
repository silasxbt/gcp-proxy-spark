"""Native parsers plus loopback data-plane tests. No production machine involved.

Usage: python -m tests.native_check --bin-dir .cache/bin [--connections]
"""
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time

import yaml

from spark.core import atomic_json, clash_config, new_manifest, server_config, singbox_config


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Echo(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b"spark-data-plane-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def checked(args, **kwargs):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kwargs)
    if result.returncode:
        # Only synthetic test configs are ever passed here.
        raise RuntimeError(result.stdout + result.stderr)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bin-dir", default=".cache/bin")
    p.add_argument("--connections", action="store_true")
    args = p.parse_args()
    binaries = Path(args.bin_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="spark-native-") as tmp:
        root = Path(tmp)
        cert, key = root / "cert.pem", root / "key.pem"
        conf = root / "openssl.cnf"
        conf.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=IP:127.0.0.1,DNS:localhost\n")
        checked(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                 "-out", cert, "-days", "1", "-config", conf])
        m = new_manifest("127.0.0.1", "LOCAL-TEST", "singbox", binaries / "sing-box", "localhost")
        m["allow_insecure_vmess"] = True
        for n in m["nodes"]:
            n["enabled"] = True
            n["port"] = free_port()
        for mode in ("singbox", "clash"):
            m["mode"] = mode
            server = server_config(m, str(cert), str(key))
            atomic_json(root / "server.json", server)
            atomic_json(root / "client.json", singbox_config(m))
            (root / "client.yaml").write_text(yaml.safe_dump(clash_config(m), allow_unicode=True))
            checked([binaries / "sing-box", "check", "-c", root / "server.json"])
            checked([binaries / "sing-box", "check", "-c", root / "client.json"])
            checked([binaries / "mihomo", "-t", "-d", root, "-f", root / "client.yaml"], timeout=45)
            print(f"PASS {mode}: native server, sing-box client and Mihomo validators", flush=True)
        if not args.connections:
            return
        http = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        tls_http = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(cert, key)
        tls_http.socket = ctx.wrap_socket(tls_http.socket, server_side=True)
        for s in (http, tls_http):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        for inbound in server["inbounds"]:
            if inbound["type"] == "vless":
                inbound["tls"]["reality"]["handshake"] = {"server": "127.0.0.1", "server_port": tls_http.server_port}
        atomic_json(root / "server.json", server)
        server_log = (root / "server.log").open("w+")
        sp = subprocess.Popen([str(binaries / "sing-box"), "run", "-c", str(root / "server.json")], stdout=server_log, stderr=server_log)
        try:
            time.sleep(1)
            for client_type in ("singbox", "clash"):
                for n in m["nodes"]:
                    one = copy.deepcopy(m)
                    for x in one["nodes"]:
                        x["published"] = x["id"] == n["id"]
                    port = free_port()
                    if client_type == "singbox":
                        c = singbox_config(one)
                        c["inbounds"][0]["listen_port"] = port
                        for o in c["outbounds"]:
                            if "tls" in o and o["type"] != "vless":
                                o["tls"]["certificate_path"] = str(cert)
                        atomic_json(root / "client.json", c)
                        command = [str(binaries / "sing-box"), "run", "-c", str(root / "client.json")]
                    else:
                        c = clash_config(one)
                        c["mixed-port"] = port
                        # Test-only CA trust, never skip certificate verification.
                        # Spelling is the upstream RawTLS YAML key in v1.19.32.
                        c["tls"] = {"custom-certifactes": [cert.read_text()]}
                        fingerprint = checked(["openssl", "x509", "-in", cert, "-noout", "-fingerprint", "-sha256"]).stdout.strip().split("=", 1)[1].replace(":", "")
                        for proxy in c["proxies"]:
                            if proxy["type"] not in ("vless", "vmess"):
                                proxy["fingerprint"] = fingerprint
                        (root / "client.yaml").write_text(yaml.safe_dump(c, allow_unicode=True))
                        command = [str(binaries / "mihomo"), "-d", str(root), "-f", str(root / "client.yaml")]
                    with (root / "client.log").open("w+") as log:
                        cp = subprocess.Popen(command, stdout=log, stderr=log)
                        try:
                            time.sleep(0.8)
                            r = checked(["curl", "--silent", "--show-error", "--fail", "--max-time", "10", "--noproxy", "",
                                         "--proxy", f"socks5h://127.0.0.1:{port}", f"http://127.0.0.1:{http.server_port}/"])
                            if r.stdout != "spark-data-plane-ok":
                                raise RuntimeError("Unexpected data-plane response")
                            print(f'PASS {client_type} → {n["protocol"]}: real loopback proxy request', flush=True)
                        except Exception:
                            log.flush()
                            log.seek(0)
                            print(log.read())
                            server_log.flush()
                            server_log.seek(0)
                            print(server_log.read())
                            raise
                        finally:
                            cp.terminate()
                            cp.wait(timeout=10)
        finally:
            sp.terminate()
            sp.wait(timeout=10)
            server_log.close()
            for s in (http, tls_http):
                s.shutdown()
                s.server_close()


if __name__ == "__main__":
    main()
