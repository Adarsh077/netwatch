import unittest
from datetime import date, timedelta

from netwatch import stats

I = 30  # check interval


def run(start, statuses):
    return [(start + i * I, s) for i, s in enumerate(statuses)]


class FindOutagesTest(unittest.TestCase):
    def test_single_outage(self):
        checks = run(1000, ["up", "isp", "isp", "isp", "up"])
        [o] = stats.find_outages(checks, I)
        self.assertEqual(o["start"], 1030)
        self.assertEqual(o["end"], 1120)  # first up check after the outage
        self.assertEqual(o["duration"], 90)
        self.assertEqual(o["kind"], "isp")
        self.assertFalse(o["ongoing"])

    def test_kind_is_majority(self):
        checks = run(0, ["lan", "lan", "isp", "up"])
        self.assertEqual(stats.find_outages(checks, I)[0]["kind"], "lan")

    def test_gap_ends_outage(self):
        # Monitor goes away mid-outage: the outage ends at the last failed check,
        # and the missing time is not counted as downtime.
        checks = run(0, ["isp", "isp"]) + run(3600, ["isp", "up"])
        outages = stats.find_outages(checks, I)
        self.assertEqual([(o["start"], o["end"]) for o in outages], [(0, 60), (3600, 3630)])

    def test_ongoing(self):
        checks = run(0, ["up", "isp", "isp"])
        [o] = stats.find_outages(checks, I, now=75)
        self.assertTrue(o["ongoing"])
        self.assertEqual(o["end"], 90)

    def test_trailing_outage_after_monitor_stopped(self):
        checks = run(0, ["up", "isp"])
        [o] = stats.find_outages(checks, I, now=10_000)
        self.assertFalse(o["ongoing"])
        self.assertEqual(o["end"], 60)

    def test_no_outages(self):
        self.assertEqual(stats.find_outages(run(0, ["up"] * 10), I), [])


class BuildWeekTest(unittest.TestCase):
    def setUp(self):
        self.monday = date(2026, 11, 2)
        self.start = stats.local_ts(self.monday)

    def test_hour_buckets_and_stats(self):
        # Tuesday 00:00-01:00: 100 up checks, 20 isp checks
        tue = stats.local_ts(self.monday + timedelta(days=1))
        checks = run(tue, ["up"] * 100 + ["isp"] * 20)
        outages = stats.find_outages(checks, I)
        now = tue + 7200
        tests = [{"ts": tue + 120, "download_mbps": 50.0, "upload_mbps": 40.0, "ping_ms": 10.0, "error": None}]
        week = stats.build_week(checks, tests, outages, self.monday, now, I)

        cell = week["days"][1]["hours"][0]
        self.assertEqual((cell["up"], cell["isp"], cell["total"], cell["expected"]), (100, 20, 120, 120))
        self.assertEqual(cell["speed"]["download_mbps"], 50.0)
        self.assertEqual(week["days"][1]["hours"][1]["expected"], 120)
        self.assertEqual(week["days"][1]["hours"][2]["expected"], 0)  # future
        self.assertEqual(week["days"][1]["downtime"], 600)
        self.assertEqual(week["days"][1]["outages"], 1)
        self.assertEqual(week["stats"]["outages"], 1)
        self.assertEqual(week["stats"]["speed"]["download_avg"], 50.0)
        self.assertAlmostEqual(week["stats"]["uptime"], 100 / 120)

    def test_outage_starting_last_week_is_included(self):
        checks = run(self.start - 60, ["isp", "isp", "isp", "up"])
        outages = stats.find_outages(checks, I)
        week = stats.build_week([c for c in checks if c[0] >= self.start], [], outages, self.monday, self.start + 3600, I)
        self.assertEqual(week["stats"]["outages"], 1)
        self.assertEqual(week["days"][0]["outages"], 0)  # counted on the day it started

    def test_empty_week(self):
        week = stats.build_week([], [], [], self.monday, self.start - 1, I)
        self.assertIsNone(week["stats"]["uptime"])
        self.assertEqual(sum(c["expected"] for d in week["days"] for c in d["hours"]), 0)


class BuildHourTest(unittest.TestCase):
    def test_slots(self):
        rows = [{"ts": 3600 + 60, "status": "isp", "gateway_ok": 1, "dns_ok": 0, "targets_ok": 0, "latency_ms": None}]
        slots = stats.build_hour(rows, 3600, I)
        self.assertEqual(len(slots), 120)
        self.assertIsNone(slots[0])
        self.assertEqual(slots[2]["status"], "isp")


if __name__ == "__main__":
    unittest.main()
