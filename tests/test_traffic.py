from datetime import datetime, timezone
import json
import unittest

from spark.traffic import advance, counter_key, public_report
from tests.helpers import fixture


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.m = fixture()
        self.k = counter_key(self.m["nodes"][0], "upload")
        self.t = ts("2026-01-31T23:59:50")

    def test_first_counter_then_increment(self):
        s = advance(None, self.m, {self.k: 100}, "boot-1", self.t)
        s = advance(s, self.m, {self.k: 160}, "boot-1", self.t + 5)
        self.assertEqual(public_report(s, self.m, self.t + 5)["total"], 160)

    def test_counter_reset_and_reboot(self):
        s = advance(None, self.m, {self.k: 100}, "boot-1", self.t)
        s = advance(s, self.m, {self.k: 5}, "boot-1", self.t + 1)
        s = advance(s, self.m, {self.k: 200}, "boot-2", self.t + 2)
        self.assertEqual(public_report(s, self.m, self.t + 2)["total"], 305)

    def test_month_boundary_and_conservation(self):
        s = advance(None, self.m, {self.k: 100}, "boot", self.t)
        s = advance(s, self.m, {self.k: 200}, "boot", self.t + 20)
        self.assertEqual(s["months"]["2026-01"]["counters"][self.k], 150)
        self.assertEqual(s["months"]["2026-02"]["counters"][self.k], 50)
        self.assertTrue(public_report(s, self.m, self.t + 20)["boundary_estimated"])

    def test_multimonth_gap_and_rounding_conserve_bytes(self):
        s = advance(None, self.m, {self.k: 1}, "boot", self.t)
        s = advance(s, self.m, {self.k: 102}, "boot", ts("2026-04-01T00:00:00"))
        self.assertEqual(sum(b["counters"].get(self.k, 0) for b in s["months"].values()), 102)

    def test_stale_no_secret_and_quota_no_enforcement(self):
        s = advance(None, self.m, {self.k: 201_000_000_000}, "boot", self.t)
        r = public_report(s, self.m, self.t + 200)
        self.assertTrue(r["stale"])
        r = public_report(s, self.m, self.t)
        self.assertTrue(r["over_reference"])
        self.assertEqual(r["quota_action"], "display-only")
        self.assertNotIn("TEST_ONLY", json.dumps(r))
        self.assertNotIn("192.0.2", json.dumps(r))

    def test_hidden_and_removed_nodes_remain_in_history(self):
        s = advance(None, self.m, {self.k: 100}, "boot", self.t)
        self.m["nodes"][0]["enabled"] = False
        s = advance(s, self.m, {self.k: 110}, "boot", self.t + 1)
        self.assertEqual(public_report(s, self.m, self.t + 1)["protocols"]["vless"]["upload"], 110)

    def test_backwards_clock_does_not_discard_history(self):
        s = advance(None, self.m, {self.k: 100}, "boot", self.t)
        with self.assertRaises(ValueError):
            advance(s, self.m, {self.k: 110}, "boot", self.t - 1)

    def test_same_time_idempotent(self):
        s = advance(None, self.m, {self.k: 100}, "boot", self.t)
        self.assertEqual(advance(s, self.m, {self.k: 100}, "boot", self.t), s)
