"""Hourly speed test against Cloudflare's speed.cloudflare.com endpoints.

Download and upload each run several parallel streams for a fixed duration.
The first second is ignored so TCP slow start doesn't drag the number down.
"""

import http.client
import logging
import statistics
import threading
import time

log = logging.getLogger("netwatch.speedtest")

UA = {"User-Agent": "netwatch/1.0"}
DOWNLOAD_CHUNK = 25_000_000
UPLOAD_CHUNK = 64 * 1024 * 16  # 1 MiB per request
WARMUP = 1.0
TIMEOUT = 15

# Set while a test saturates the link, so the monitor can tell a real outage
# from pings dropped by a full upstream buffer.
RUNNING = threading.Event()


class _Counter:
    def __init__(self):
        self.bytes = 0
        self._lock = threading.Lock()

    def add(self, n):
        with self._lock:
            self.bytes += n


def _conn(host):
    return http.client.HTTPSConnection(host, timeout=TIMEOUT)


def measure_latency(host, samples=6):
    """Median request round trip over a warm connection (TLS handshake excluded)."""
    conn = _conn(host)
    colo = None
    rtts = []
    try:
        for i in range(samples):
            t = time.perf_counter()
            conn.request("GET", "/__down?bytes=0", headers=UA)
            resp = conn.getresponse()
            resp.read()
            if i:  # first request pays for TCP + TLS setup
                rtts.append((time.perf_counter() - t) * 1000)
            ray = resp.getheader("cf-ray") or ""
            if "-" in ray:
                colo = ray.rsplit("-", 1)[1]
    finally:
        conn.close()
    return statistics.median(rtts), colo


def _download_worker(host, deadline, counter):
    conn = _conn(host)
    try:
        while time.monotonic() < deadline:
            conn.request("GET", f"/__down?bytes={DOWNLOAD_CHUNK}", headers=UA)
            resp = conn.getresponse()
            while True:
                block = resp.read(65536)
                if not block:
                    break
                counter.add(len(block))
                if time.monotonic() >= deadline:
                    return
    except Exception as e:
        log.debug("download stream ended: %s", e)
    finally:
        conn.close()


def _upload_worker(host, deadline, counter):
    payload = b"\0" * 65536

    # Always send the full declared Content-Length; stopping mid-body would
    # leave the server waiting out the timeout. Bytes after the deadline
    # aren't counted because the sampler has already read the counter.
    def body():
        for _ in range(UPLOAD_CHUNK // len(payload)):
            yield payload
            counter.add(len(payload))

    conn = _conn(host)
    try:
        while time.monotonic() < deadline:
            headers = {**UA, "Content-Type": "application/octet-stream", "Content-Length": str(UPLOAD_CHUNK)}
            conn.request("POST", "/__up", body=body(), headers=headers)
            conn.getresponse().read()
    except Exception as e:
        log.debug("upload stream ended: %s", e)
    finally:
        conn.close()


def _throughput(worker, host, duration, streams):
    counter = _Counter()
    start = time.monotonic()
    deadline = start + duration
    threads = [threading.Thread(target=worker, args=(host, deadline, counter), daemon=True) for _ in range(streams)]
    for t in threads:
        t.start()
    time.sleep(WARMUP)
    b0, t0 = counter.bytes, time.monotonic()
    time.sleep(max(0.0, deadline - time.monotonic()))
    b1, t1 = counter.bytes, time.monotonic()
    for t in threads:
        t.join(timeout=TIMEOUT)
    if b1 <= b0:
        raise RuntimeError("no data transferred")
    return (b1 - b0) * 8 / (t1 - t0) / 1e6


def run_speedtest(cfg):
    """Returns (download_mbps, upload_mbps, ping_ms, server)."""
    ping_ms, colo = measure_latency(cfg.speedtest_host)
    down = _throughput(_download_worker, cfg.speedtest_host, cfg.speedtest_duration, cfg.speedtest_streams)
    up = _throughput(_upload_worker, cfg.speedtest_host, cfg.speedtest_duration, cfg.speedtest_streams)
    server = f"Cloudflare {colo}" if colo else "Cloudflare"
    return round(down, 2), round(up, 2), round(ping_ms, 1), server


def record_speedtest(db, cfg, ts=None):
    ts = int(ts or time.time())
    latest = db.latest_check()
    if latest and latest["status"] != "up" and ts - latest["ts"] < cfg.check_interval * 3:
        db.insert_speedtest(ts, None, None, None, None, f"offline ({latest['status']})")
        log.warning("speed test skipped: offline")
        return
    RUNNING.set()
    try:
        down, up, ping_ms, server = run_speedtest(cfg)
        db.insert_speedtest(ts, down, up, ping_ms, server, None)
        log.info("speed test: down %.1f Mbps, up %.1f Mbps, ping %.0f ms (%s)", down, up, ping_ms, server)
    except Exception as e:
        db.insert_speedtest(ts, None, None, None, None, f"failed: {e}"[:200])
        log.warning("speed test failed: %s", e)
    finally:
        RUNNING.clear()


def speedtest_loop(db, cfg, stop):
    interval, offset = cfg.speedtest_interval, cfg.speedtest_offset
    log.info("speed tests every %ss, %ss past the boundary", interval, offset)

    # On a fresh start, don't wait up to an hour for the first result.
    last = db.latest_speedtest()
    if last is None or time.time() - last["ts"] > interval:
        if stop.wait(45):  # let a couple of connectivity checks land first
            return
        record_speedtest(db, cfg)

    while not stop.is_set():
        now = time.time()
        next_t = (now - offset) // interval * interval + interval + offset
        if stop.wait(next_t - now):
            break
        record_speedtest(db, cfg, next_t)
