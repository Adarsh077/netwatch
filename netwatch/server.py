import csv
import io
import json
import logging
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import stats
from .checks import default_gateway

log = logging.getLogger("netwatch.server")

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class BadRequest(Exception):
    pass


def make_handler(db, cfg):
    interval = cfg.check_interval

    def week(params):
        today = date.today()
        try:
            day = stats.parse_date(params.get("start"), today)
        except ValueError:
            raise BadRequest("start must be YYYY-MM-DD")
        monday = stats.monday_of(day)
        start = stats.local_ts(monday)
        end = stats.local_ts(monday + timedelta(days=7))
        now = int(time.time())
        # Look back a day so an outage that began before Monday is reported whole.
        rows = db.checks_between(start - 86400, end)
        pairs = [(r["ts"], r["status"]) for r in rows]
        outages = stats.find_outages(pairs, interval, now)
        tests = [dict(t) for t in db.speedtests_between(start, end)]
        data = stats.build_week([p for p in pairs if p[0] >= start], tests, outages, monday, now, interval)
        first, _ = db.bounds()
        data["prev"] = (monday - timedelta(days=7)).isoformat()
        data["next"] = (monday + timedelta(days=7)).isoformat() if monday < stats.monday_of(today) else None
        data["has_earlier"] = first is not None and first < start
        data["now"] = now
        return data

    def days(params):
        try:
            n = max(1, min(366, int(params.get("days", 90))))
        except ValueError:
            raise BadRequest("days must be a number")
        first = date.today() - timedelta(days=n - 1)
        counts = db.daily_counts(stats.local_ts(first))
        data = stats.build_days(counts, first, n, int(time.time()), interval)
        data["first_check"] = db.bounds()[0]
        return data

    def hour(params):
        try:
            start = int(params["start"])
        except (KeyError, ValueError):
            raise BadRequest("start must be a unix timestamp")
        rows = db.checks_between(start, start + 3600)
        tests = [dict(t) for t in db.speedtests_between(start, start + 3600)]
        pairs = [(r["ts"], r["status"]) for r in db.checks_between(start - 3600, start + 7200)]
        outages = [o for o in stats.find_outages(pairs, interval, time.time()) if o["end"] > start and o["start"] < start + 3600]
        return {
            "start": start,
            "interval": interval,
            "utc_offset": stats.utc_offset(start),
            "slots": stats.build_hour(rows, start, interval),
            "speedtests": tests,
            "outages": outages,
        }

    def status(_params):
        now = time.time()
        latest = db.latest_check()
        speed = db.latest_successful_speedtest()
        last_test = db.latest_speedtest()
        state = "nodata"
        since = None
        if latest is not None:
            state = latest["status"] if now - latest["ts"] <= interval * 3 else "stale"
            since = db.status_since(latest["status"])
        return {
            "now": int(now),
            "state": state,
            "since": since,
            "latest": dict(latest) if latest else None,
            "speed": dict(speed) if speed else None,
            "last_speedtest": dict(last_test) if last_test else None,
            "gateway": cfg.gateway or default_gateway(),
            "interval": interval,
            "speedtest_interval": cfg.speedtest_interval if cfg.speedtest_enabled else None,
            "utc_offset": stats.utc_offset(now),
        }

    def date_range(params):
        try:
            start_d = stats.parse_date(params.get("start"))
            end_d = stats.parse_date(params.get("end"))
        except ValueError:
            raise BadRequest("start/end must be YYYY-MM-DD")
        start = stats.local_ts(start_d) if start_d else 0
        end = stats.local_ts(end_d + timedelta(days=1)) if end_d else 2**40
        return start, end

    def iso(ts):
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")

    def checks_csv(params):
        start, end = date_range(params)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["time", "unix_ts", "status", "gateway_ok", "dns_ok", "targets_ok", "latency_ms"])
        for r in db.checks_between(start, end):
            w.writerow([iso(r["ts"]), r["ts"], r["status"], r["gateway_ok"], r["dns_ok"], r["targets_ok"], r["latency_ms"]])
        return out.getvalue()

    def speedtests_csv(params):
        start, end = date_range(params)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["time", "unix_ts", "download_mbps", "upload_mbps", "ping_ms", "server", "error"])
        for r in db.speedtests_between(start, end):
            w.writerow([iso(r["ts"]), r["ts"], r["download_mbps"], r["upload_mbps"], r["ping_ms"], r["server"], r["error"]])
        return out.getvalue()

    def outages_csv(params):
        start, end = date_range(params)
        pairs = [(r["ts"], r["status"]) for r in db.checks_between(start, end)]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["start", "end", "duration_seconds", "cause", "ongoing"])
        for o in stats.find_outages(pairs, interval, time.time()):
            cause = "ISP (router reachable, no internet)" if o["kind"] == "isp" else "Router/WiFi unreachable"
            w.writerow([iso(o["start"]), iso(o["end"]), o["duration"], cause, o["ongoing"]])
        return out.getvalue()

    json_routes = {"/api/week": week, "/api/days": days, "/api/hour": hour, "/api/status": status}
    csv_routes = {
        "/export/checks.csv": checks_csv,
        "/export/speedtests.csv": speedtests_csv,
        "/export/outages.csv": outages_csv,
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "netwatch/1.0"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, code, body, content_type, extra=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in json_routes:
                    self._send(200, json.dumps(json_routes[url.path](params)), "application/json")
                elif url.path in csv_routes:
                    name = url.path.rsplit("/", 1)[1]
                    self._send(200, csv_routes[url.path](params), "text/csv; charset=utf-8",
                               {"Content-Disposition": f'attachment; filename="netwatch-{name}"'})
                elif url.path in STATIC_FILES:
                    filename, ctype = STATIC_FILES[url.path]
                    self._send(200, (STATIC / filename).read_bytes(), ctype)
                elif url.path == "/healthz":
                    self._send(200, "ok", "text/plain")
                else:
                    self._send(404, "not found", "text/plain")
            except BadRequest as e:
                self._send(400, json.dumps({"error": str(e)}), "application/json")
            except Exception:
                log.exception("error serving %s", self.path)
                self._send(500, json.dumps({"error": "internal error"}), "application/json")

    return Handler


def serve(db, cfg):
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(db, cfg))
    httpd.daemon_threads = True
    log.info("dashboard on http://%s:%s", cfg.host, cfg.port)
    return httpd
