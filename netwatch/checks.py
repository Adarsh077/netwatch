"""One connectivity check: is the router reachable, and is the internet reachable?"""

import logging
import math
import re
import socket
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import speedtest

log = logging.getLogger("netwatch.checks")

PING_TIMEOUT = 2
HTTP_TIMEOUT = 5
DNS_TIMEOUT = 4
_RTT = re.compile(r"time[=<]([\d.]+)\s*ms")


@dataclass
class CheckResult:
    status: str  # up | isp | lan
    gateway_ok: int | None
    dns_ok: int
    targets_ok: int
    latency_ms: float | None


def default_gateway():
    """Lowest-metric IPv4 default gateway from the kernel routing table."""
    best = None
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                parts = line.split()
                dest, gw, flags, metric = parts[1], parts[2], int(parts[3], 16), int(parts[6])
                if dest != "00000000" or not flags & 0x2:  # RTF_GATEWAY
                    continue
                if best is None or metric < best[0]:
                    best = (metric, socket.inet_ntoa(bytes.fromhex(gw)[::-1]))
    except OSError:
        return None
    return best[1] if best else None


def ping(host, timeout=PING_TIMEOUT):
    """RTT in ms, or None if the host did not answer."""
    try:
        proc = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", str(timeout), host],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    match = _RTT.search(proc.stdout)
    return float(match.group(1)) if match else 0.0


def http_probe(url, timeout=HTTP_TIMEOUT):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "netwatch/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


def dns_lookup(name):
    try:
        socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def ping_gateway(gateway):
    # Some routers drop the odd ping under load, so give it a second try
    # before blaming the LAN.
    for _ in range(2):
        if ping(gateway) is not None:
            return True
    return False


def run_check(cfg):
    gateway = cfg.gateway or default_gateway()
    with ThreadPoolExecutor(max_workers=len(cfg.ping_targets) + 3) as pool:
        pings = [pool.submit(ping, t) for t in cfg.ping_targets]
        http = pool.submit(http_probe, cfg.http_probe_url)
        dns = pool.submit(dns_lookup, cfg.dns_probe_name)
        gw = pool.submit(ping_gateway, gateway) if gateway else None

        rtts = [f.result() for f in pings]
        http_ok = http.result()
        try:
            dns_ok = dns.result(timeout=DNS_TIMEOUT)
        except Exception:
            dns_ok = False
        gateway_ok = None if gw is None else int(gw.result())

    answered = [r for r in rtts if r is not None]
    targets_ok = len(answered) + int(http_ok)
    if targets_ok:
        status = "up"
    elif gateway_ok:
        status = "isp"
    else:
        status = "lan"
    return CheckResult(
        status=status,
        gateway_ok=gateway_ok,
        dns_ok=int(dns_ok),
        targets_ok=targets_ok,
        latency_ms=min(answered) if answered else None,
    )


def monitor_loop(db, cfg, stop):
    interval = cfg.check_interval
    next_t = math.ceil(time.time() / interval) * interval
    log.info("connectivity checks every %ss (gateway: %s)", interval, cfg.gateway or default_gateway() or "none")
    while not stop.is_set():
        if stop.wait(max(0.0, next_t - time.time())):
            break
        ts = int(next_t)
        try:
            r = run_check(cfg)
            if r.status != "up" and speedtest.RUNNING.is_set():
                # Probes can time out on a saturated link; judge once the test is over.
                deadline = time.time() + interval * 0.8
                while speedtest.RUNNING.is_set() and time.time() < deadline:
                    stop.wait(0.5)
                r = run_check(cfg)
            db.insert_check(ts, r.status, r.gateway_ok, r.dns_ok, r.targets_ok, r.latency_ms)
            if r.status != "up":
                log.warning("check %s: %s (gateway_ok=%s)", ts, r.status, r.gateway_ok)
        except Exception:
            log.exception("check failed")
        next_t += interval
        # After a suspend or a very slow check, skip ahead instead of firing a burst.
        if next_t < time.time():
            next_t = math.ceil(time.time() / interval) * interval
