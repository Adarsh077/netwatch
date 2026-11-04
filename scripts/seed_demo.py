"""Fill a database with three weeks of fake data so the dashboard can be previewed.

    python scripts/seed_demo.py /tmp/demo.db
    NETWATCH_DB=/tmp/demo.db SPEEDTEST_ENABLED=0 python -m netwatch

Simulates a nightly ISP drop around 00:00-01:30, the odd short blip, a
router reboot, and slower evening speeds.
"""

import os
import random
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from netwatch.db import Database  # noqa: E402

INTERVAL = 30


def main(path):
    if os.path.exists(path):
        os.remove(path)
    db = Database(path)
    rng = random.Random(42)
    now = int(time.time()) // INTERVAL * INTERVAL
    start = now - 21 * 86400
    start -= start % 86400

    # (start, end, status) windows of trouble
    windows = []
    day = datetime.fromtimestamp(start).replace(hour=0, minute=0, second=0, microsecond=0)
    while day.timestamp() < now:
        if rng.random() < 0.85:  # most nights
            s = day + timedelta(minutes=rng.randint(-10, 40))
            windows.append((s.timestamp(), s.timestamp() + rng.randint(15, 75) * 60, "isp"))
        for _ in range(rng.randint(0, 2)):  # daytime blips
            s = day + timedelta(hours=rng.randint(7, 22), minutes=rng.randint(0, 59))
            windows.append((s.timestamp(), s.timestamp() + rng.choice([30, 60, 90, 240]), "isp"))
        if rng.random() < 0.12:  # router reboot
            s = day + timedelta(hours=rng.randint(9, 20))
            windows.append((s.timestamp(), s.timestamp() + rng.randint(2, 6) * 60, "lan"))
        day += timedelta(days=1)

    # the mini-PC was unplugged for a few hours once
    off_start = start + 9 * 86400 + 14 * 3600
    off_end = off_start + 5 * 3600

    def status_at(ts):
        for s, e, st in windows:
            if s <= ts < e:
                return st
        return "up"

    with db._lock:
        conn = db._conn
        conn.execute("BEGIN")
        for ts in range(start, now + 1, INTERVAL):
            if off_start <= ts < off_end:
                continue
            st = status_at(ts)
            if st == "up":
                row = (ts, "up", 1, 1, 3, round(rng.uniform(8, 25), 1))
            elif st == "isp":
                row = (ts, "isp", 1, 0, 0, None)
            else:
                row = (ts, "lan", 0, 0, 0, None)
            conn.execute("INSERT INTO checks VALUES (?, ?, ?, ?, ?, ?)", row)

        for hour in range(start + 120, now, 3600):
            if off_start <= hour < off_end:
                continue
            st = status_at(hour)
            if st != "up":
                conn.execute("INSERT INTO speedtests VALUES (?, NULL, NULL, NULL, NULL, ?)", (hour, f"offline ({st})"))
                continue
            h = datetime.fromtimestamp(hour).hour
            evening = 0.55 if 19 <= h <= 23 else 1.0
            down = round(rng.uniform(80, 100) * evening, 2)
            up = round(rng.uniform(40, 50) * (0.8 if evening < 1 else 1), 2)
            conn.execute(
                "INSERT INTO speedtests VALUES (?, ?, ?, ?, ?, NULL)",
                (hour, down, up, round(rng.uniform(6, 18), 1), "Cloudflare BOM"),
            )
        conn.execute("COMMIT")
    print(f"seeded {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/demo.db")
