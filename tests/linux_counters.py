"""Run ONLY in an isolated Linux network namespace: sudo unshare -n ..."""
import copy
import json
import os
import socket
import subprocess
import time

from spark.traffic import advance, counter_key, ensure_counters, public_report, snapshot
from tests.helpers import fixture


def main():
    if os.environ.get("SPARK_ISOLATED_TEST") != "1":
        raise RuntimeError("Require an explicitly isolated test namespace")
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    m = fixture()
    for n in m["nodes"]:
        n["enabled"] = n["protocol"] == "anytls"
    ensure_counters(m)
    first, gen = snapshot(m)
    ensure_counters(m)
    again, gen2 = snapshot(m)
    assert first == again and gen == gen2, "Repeated installation changed counters"
    n = m["nodes"][1]
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        with socket.socket(family) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((address, n["port"]))
            server.listen()
            with socket.socket(family) as client:
                client.connect((address, n["port"]))
                connection, _ = server.accept()
                with connection:
                    client.sendall(b"upload" * 200)
                    connection.recv(4096)
                    connection.sendall(b"download" * 200)
                    client.recv(4096)
        values, generation = snapshot(m)
        assert values[counter_key(n, "upload")] > first[counter_key(n, "upload")]
        assert values[counter_key(n, "download")] > first[counter_key(n, "download")]
        first = values
        print(f"PASS real {'IPv4' if family == socket.AF_INET else 'IPv6'} nftables accounting")
    state = advance(None, m, values, generation, time.time())
    total = public_report(state, m)["total"]
    subprocess.run(["nft", "delete", "table", "inet", "proxy_spark"], check=True)
    ensure_counters(m)
    values, new_generation = snapshot(m)
    state = advance(state, m, values, new_generation, time.time())
    assert public_report(state, m)["total"] == total, "Counter recreation lost history"
    assert new_generation != generation, "Table recreation must change generation"
    rules = subprocess.check_output(["nft", "list", "ruleset"], text=True)
    assert "drop" not in rules and "reject" not in rules, "Accounting must not filter traffic"
    print("PASS table recreation and non-filtering invariant")


if __name__ == "__main__":
    main()
