import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import yaml

from spark.core import (atomic_json, clash_config, load, redacted_plan, selected,
                        server_config, shadowrocket_uris, singbox_config, subscription, validate)
from tests.helpers import fixture


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.m = fixture()

    def test_default_four_protocols_and_same_mode_intersection(self):
        for mode in ("singbox", "clash"):
            self.m["mode"] = mode
            validate(self.m)
            self.assertEqual([x["type"] for x in server_config(self.m)["inbounds"]], ["vless", "anytls", "hysteria2", "tuic"])

    def test_vmess_requires_acknowledgement(self):
        self.m["nodes"][-1]["enabled"] = True
        with self.assertRaises(ValueError):
            validate(self.m)
        self.m["allow_insecure_vmess"] = True
        validate(self.m)
        self.assertEqual(len(clash_config(self.m)["proxies"]), 5)

    def test_reject_unknown_version(self):
        self.m["target_versions"]["clash"] = "old"
        with self.assertRaises(ValueError):
            validate(self.m)

    def test_reject_anytls_reality(self):
        self.m["nodes"][1]["reality"] = True
        with self.assertRaises(ValueError):
            validate(self.m)

    def test_reject_duplicates_and_bad_ids(self):
        for field, value in (("id", "../a"), ("port", 80), ("port", 9105), ("id", "anytls"), ("port", 24444)):
            m = fixture()
            m["nodes"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate(m)

    def test_hidden_still_deployed_not_subscribed(self):
        self.m["nodes"][1]["published"] = False
        self.assertEqual(len(server_config(self.m)["inbounds"]), 4)
        self.assertEqual(len(clash_config(self.m)["proxies"]), 3)

    def test_group_references(self):
        for published in (True, False):
            self.m["nodes"][0]["published"] = published
            c = clash_config(self.m)
            known = {n["name"] for n in c["proxies"]} | {n["name"] for n in c["proxy-groups"]}
            self.assertTrue(all(set(g["proxies"]) <= known for g in c["proxy-groups"]))
            s = singbox_config(self.m)
            known = {n["tag"] for n in s["outbounds"]}
            self.assertTrue(all(set(g.get("outbounds", [])) <= known for g in s["outbounds"]))

    def test_no_private_key_in_clients_or_plan(self):
        secret = self.m["nodes"][0]["reality_config"]["private_key"]
        for fmt in ("singbox", "clash", "shadowrocket"):
            data, _ = subscription(self.m, fmt)
            self.assertNotIn(secret.encode(), data)
            self.assertNotIn(self.m["subscription_token"].encode(), data)
        plan = json.dumps(redacted_plan(self.m))
        self.assertNotIn("TEST_ONLY", plan)
        self.assertNotIn(secret, plan)

    def test_formats_roundtrip_and_no_insecure_tls(self):
        s, _ = subscription(self.m, "singbox")
        c, _ = subscription(self.m, "clash")
        u, _ = subscription(self.m, "shadowrocket")
        self.assertEqual(len(json.loads(s)["outbounds"]), 6)
        self.assertEqual(len(yaml.safe_load(c)["proxies"]), 4)
        self.assertEqual(len(base64.b64decode(u).decode().splitlines()), 4)
        self.assertNotIn(b'"insecure": true', s)
        self.assertNotIn(b"skip-cert-verify: true", c)

    def test_ipv6_uri_and_escaping(self):
        self.m["address"] = "2001:db8::1"
        self.m["region"] = "测试 / &"
        for link in shadowrocket_uris(self.m).splitlines():
            u = urlsplit(link)
            self.assertEqual(u.hostname, "2001:db8::1")
            self.assertNotIn(" ", link)
            self.assertIn("sni", parse_qs(u.query))

    def test_private_files_and_idempotent_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "manifest.json"
            atomic_json(p, self.m)
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(load(p), self.m)
            self.assertEqual(subscription(self.m, "clash"), subscription(copy.deepcopy(self.m), "clash"))

    def test_plan_detects_disable_and_credential_change_without_disclosure(self):
        old = copy.deepcopy(self.m)
        self.m["nodes"][1]["enabled"] = False
        self.assertEqual(redacted_plan(self.m, old)["removed"], ["anytls"])
        self.m["subscription_token"] = "NEW_" + "x" * 32
        self.assertTrue(redacted_plan(self.m, old)["private_config_changed"])
