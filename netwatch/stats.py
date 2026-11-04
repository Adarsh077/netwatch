"""Aggregation over raw checks. Pure functions on (ts, status) sequences.

All day/hour bucketing uses the server's local time zone (set TZ or mount
/etc/localtime in the container).
"""

from datetime import date, datetime, timedelta

# A check row covers [ts, ts + interval). A gap longer than this many intervals
# means the monitor wasn't running, which is "no data", not an outage.
GAP_FACTOR = 2.5


def monday_of(d):
    return d - timedelta(days=d.weekday())


def local_ts(d, hour=0):
    return int(datetime(d.year, d.month, d.day, hour).timestamp())


def utc_offset(ts):
    return int(datetime.fromtimestamp(ts).astimezone().utcoffset().total_seconds())


def find_outages(checks, interval, now=None):
    """Group consecutive non-up checks into outages.

    checks: iterable of (ts, status) sorted by ts.
    Returns dicts with start, end, duration, kind ('isp' | 'lan'), ongoing.
    """
    outages = []
    cur = None
    prev_ts = None

    def close(end, ongoing=False):
        cur["end"] = end
        cur["duration"] = end - cur["start"]
        cur["kind"] = "isp" if cur.pop("isp") >= cur.pop("lan") else "lan"
        cur["ongoing"] = ongoing
        outages.append(cur)

    for ts, status in checks:
        gap = prev_ts is not None and ts - prev_ts > interval * GAP_FACTOR
        if cur is not None and (gap or status == "up"):
            close(prev_ts + interval if gap else ts)
            cur = None
        if status != "up":
            if cur is None:
                cur = {"start": ts, "isp": 0, "lan": 0}
            cur[status] += 1
        prev_ts = ts

    if cur is not None:
        ongoing = now is not None and now - prev_ts <= interval * GAP_FACTOR
        close(max(prev_ts + interval, int(now)) if ongoing else prev_ts + interval, ongoing)
    return outages


def _speed_summary(tests):
    ok = [t for t in tests if t["error"] is None]
    if not ok:
        return None
    return {
        "count": len(ok),
        "failed": len(tests) - len(ok),
        "download_avg": round(sum(t["download_mbps"] for t in ok) / len(ok), 1),
        "upload_avg": round(sum(t["upload_mbps"] for t in ok) / len(ok), 1),
        "download_min": min(t["download_mbps"] for t in ok),
        "download_max": max(t["download_mbps"] for t in ok),
        "ping_avg": round(sum(t["ping_ms"] for t in ok) / len(ok), 1),
    }


def build_week(checks, speedtests, outages, monday, now, interval):
    """7x24 grid of hour cells plus per-day and per-week summaries.

    checks: sequence of (ts, status); speedtests: dicts with ts, download_mbps,
    upload_mbps, ping_ms, error; outages: output of find_outages.
    """
    per_hour = 3600 // interval
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        hours = []
        for h in range(24):
            start = local_ts(d, h)
            elapsed = min(3600, max(0, now - start))
            hours.append({
                "h": h, "start": start,
                "up": 0, "isp": 0, "lan": 0, "total": 0,
                "expected": min(per_hour, int(elapsed // interval)),
                "speed": None,
            })
        days.append({"date": d.isoformat(), "start": local_ts(d), "hours": hours, "tests": []})

    def cell_for(ts):
        dt = datetime.fromtimestamp(ts)
        i = (dt.date() - monday).days
        return (days[i], days[i]["hours"][dt.hour]) if 0 <= i < 7 else (None, None)

    for ts, status in checks:
        _, cell = cell_for(ts)
        if cell is not None:
            cell[status] += 1
            cell["total"] += 1
            cell["expected"] = max(cell["expected"], cell["total"])

    for t in speedtests:
        day, cell = cell_for(t["ts"])
        if cell is not None:
            cell["speed"] = {k: t[k] for k in ("ts", "download_mbps", "upload_mbps", "ping_ms", "error")}
            day["tests"].append(t)

    week_start = days[0]["start"]
    week_end = local_ts(monday + timedelta(days=7))
    for i, day in enumerate(days):
        day_end = days[i + 1]["start"] if i < 6 else week_end
        up = sum(c["up"] for c in day["hours"])
        total = sum(c["total"] for c in day["hours"])
        expected = sum(c["expected"] for c in day["hours"])
        day_outages = [o for o in outages if day["start"] <= o["start"] < day_end]
        day.update({
            "up": up, "total": total, "expected": expected,
            "uptime": up / total if total else None,
            "coverage": total / expected if expected else None,
            "downtime": (total - up) * interval,
            "outages": len(day_outages),
            "longest": max((o["duration"] for o in day_outages), default=0),
            "speed": _speed_summary(day.pop("tests")),
        })

    up = sum(d["up"] for d in days)
    total = sum(d["total"] for d in days)
    expected = sum(d["expected"] for d in days)
    week_outages = [o for o in outages if o["end"] > week_start and o["start"] < week_end]
    return {
        "week_start": monday.isoformat(),
        "start": week_start,
        "end": week_end,
        "interval": interval,
        "utc_offset": utc_offset(week_start + 43200),
        "days": days,
        "stats": {
            "uptime": up / total if total else None,
            "coverage": total / expected if expected else None,
            "downtime": (total - up) * interval,
            "outages": len(week_outages),
            "longest": max((o["duration"] for o in week_outages), default=0),
            "isp_outages": sum(1 for o in week_outages if o["kind"] == "isp"),
            "lan_outages": sum(1 for o in week_outages if o["kind"] == "lan"),
            "speed": _speed_summary(speedtests),
        },
        "outages": sorted(week_outages, key=lambda o: o["start"], reverse=True),
        "speedtests": [dict(t) for t in speedtests],
    }


def build_days(counts, first_day, n, now, interval):
    """One summary per day for the long uptime strip. counts: db.daily_counts()."""
    days = []
    for i in range(n):
        d = first_day + timedelta(days=i)
        start, end = local_ts(d), local_ts(d + timedelta(days=1))
        c = counts.get(d.isoformat(), {})
        up, isp, lan = c.get("up", 0), c.get("isp", 0), c.get("lan", 0)
        total = up + isp + lan
        expected = max(total, int(min(end - start, max(0, now - start)) // interval))
        days.append({
            "date": d.isoformat(), "start": start,
            "up": up, "isp": isp, "lan": lan, "total": total, "expected": expected,
        })
    up = sum(d["up"] for d in days)
    total = sum(d["total"] for d in days)
    return {"days": days, "uptime": up / total if total else None, "interval": interval,
            "utc_offset": utc_offset(now)}


def build_hour(rows, start, interval):
    slots = [None] * (3600 // interval)
    for r in rows:
        i = (r["ts"] - start) // interval
        if 0 <= i < len(slots):
            slots[i] = {k: r[k] for k in ("ts", "status", "gateway_ok", "dns_ok", "targets_ok", "latency_ms")}
    return slots


def parse_date(value, default=None):
    if not value:
        return default
    return date.fromisoformat(value)
