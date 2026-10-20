"""Local UI preview using synthetic counters. Never reads production credentials."""
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time

from spark.core import atomic_json
from spark.traffic import advance, counter_key
from spark.web import handler
from tests.helpers import fixture


def main():
    with tempfile.TemporaryDirectory(prefix="spark-preview-") as tmp:
        root = Path(tmp)
        m = fixture()
        atomic_json(root / "manifest.json", m)
        counters = {}
        for n, total in zip(m["nodes"], (39.2e9, 23.1e9, 18.6e9, 5.4e9, 0)):
            counters[counter_key(n, "upload")] = int(total * 0.15)
            counters[counter_key(n, "download")] = int(total * 0.85)
        def update():
            while True:
                atomic_json(root / "traffic.json", advance(None, m, counters, "preview", time.time()))
                time.sleep(10)
        threading.Thread(target=update, daemon=True).start()
        server = ThreadingHTTPServer(("127.0.0.1", 9187), handler(root / "manifest.json", root / "traffic.json", status=lambda: True))
        print("Synthetic preview: http://127.0.0.1:9187/traffic", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
