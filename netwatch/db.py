import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    ts          INTEGER PRIMARY KEY,  -- unix seconds, aligned to the check interval
    status      TEXT    NOT NULL,     -- up | isp (router ok, no internet) | lan (router unreachable)
    gateway_ok  INTEGER,              -- 1 / 0 / NULL when there is no default route
    dns_ok      INTEGER NOT NULL,
    targets_ok  INTEGER NOT NULL,     -- how many internet targets answered
    latency_ms  REAL                  -- best ping RTT to an internet target
);
CREATE TABLE IF NOT EXISTS speedtests (
    ts            INTEGER PRIMARY KEY,
    download_mbps REAL,
    upload_mbps   REAL,
    ping_ms       REAL,
    server        TEXT,
    error         TEXT                -- set when the test could not run
);
"""


class Database:
    def __init__(self, path):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def _query(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql, params=()):
        with self._lock:
            self._conn.execute(sql, params)

    def insert_check(self, ts, status, gateway_ok, dns_ok, targets_ok, latency_ms):
        self._execute(
            "INSERT OR REPLACE INTO checks VALUES (?, ?, ?, ?, ?, ?)",
            (ts, status, gateway_ok, dns_ok, targets_ok, latency_ms),
        )

    def insert_speedtest(self, ts, download_mbps, upload_mbps, ping_ms, server, error):
        self._execute(
            "INSERT OR REPLACE INTO speedtests VALUES (?, ?, ?, ?, ?, ?)",
            (ts, download_mbps, upload_mbps, ping_ms, server, error),
        )

    def checks_between(self, start, end):
        return self._query("SELECT * FROM checks WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end))

    def speedtests_between(self, start, end):
        return self._query("SELECT * FROM speedtests WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end))

    def latest_check(self):
        rows = self._query("SELECT * FROM checks ORDER BY ts DESC LIMIT 1")
        return rows[0] if rows else None

    def latest_speedtest(self):
        rows = self._query("SELECT * FROM speedtests ORDER BY ts DESC LIMIT 1")
        return rows[0] if rows else None

    def latest_successful_speedtest(self):
        rows = self._query("SELECT * FROM speedtests WHERE error IS NULL ORDER BY ts DESC LIMIT 1")
        return rows[0] if rows else None

    def status_since(self, status):
        """Timestamp of the first check in the current run of `status`."""
        rows = self._query(
            "SELECT MIN(ts) AS since FROM checks WHERE ts > "
            "(SELECT COALESCE(MAX(ts), 0) FROM checks WHERE status != ?)",
            (status,),
        )
        return rows[0]["since"] if rows else None

    def daily_counts(self, start):
        """{'YYYY-MM-DD': {status: count}} in the process's local time zone."""
        rows = self._query(
            "SELECT date(ts, 'unixepoch', 'localtime') AS day, status, COUNT(*) AS n "
            "FROM checks WHERE ts >= ? GROUP BY day, status",
            (start,),
        )
        out = {}
        for r in rows:
            out.setdefault(r["day"], {})[r["status"]] = r["n"]
        return out

    def bounds(self):
        rows = self._query("SELECT MIN(ts) AS first, MAX(ts) AS last FROM checks")
        return (rows[0]["first"], rows[0]["last"]) if rows else (None, None)
