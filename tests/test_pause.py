import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from netwatch.config import Config
from netwatch.speedtest import in_pause

IST = ZoneInfo("Asia/Kolkata")


def ts(h, m):
    return datetime(2026, 11, 13, h, m, tzinfo=IST).timestamp()


class PauseTest(unittest.TestCase):
    def test_default_window(self):
        cfg = Config()
        cfg.speedtest_pause = "09:00-13:00"
        self.assertFalse(in_pause(cfg, ts(8, 59)))
        self.assertTrue(in_pause(cfg, ts(9, 0)))
        self.assertTrue(in_pause(cfg, ts(12, 2)))
        self.assertFalse(in_pause(cfg, ts(13, 0)))

    def test_cross_midnight(self):
        cfg = Config()
        cfg.speedtest_pause = "23:00-02:00"
        self.assertTrue(in_pause(cfg, ts(23, 30)))
        self.assertTrue(in_pause(cfg, ts(1, 0)))
        self.assertFalse(in_pause(cfg, ts(12, 0)))

    def test_empty_disables(self):
        cfg = Config()
        cfg.speedtest_pause = ""
        self.assertFalse(in_pause(cfg, ts(10, 0)))


if __name__ == "__main__":
    unittest.main()
