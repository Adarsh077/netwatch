import os
from dataclasses import dataclass, field


def _env(name, default):
    return os.environ.get(name, default)


def _env_int(name, default):
    return int(os.environ.get(name, default))


def _env_bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    db_path: str = field(default_factory=lambda: _env("NETWATCH_DB", "data/netwatch.db"))
    host: str = field(default_factory=lambda: _env("NETWATCH_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("NETWATCH_PORT", 8080))

    # Connectivity checks
    check_interval: int = field(default_factory=lambda: _env_int("CHECK_INTERVAL", 30))
    ping_targets: list = field(
        default_factory=lambda: [t.strip() for t in _env("PING_TARGETS", "1.1.1.1,8.8.8.8").split(",") if t.strip()]
    )
    http_probe_url: str = field(
        default_factory=lambda: _env("HTTP_PROBE_URL", "http://connectivitycheck.gstatic.com/generate_204")
    )
    dns_probe_name: str = field(default_factory=lambda: _env("DNS_PROBE_NAME", "www.google.com"))
    # Empty = auto-detect the default gateway from /proc/net/route
    gateway: str = field(default_factory=lambda: _env("GATEWAY", ""))

    # Speed tests
    speedtest_enabled: bool = field(default_factory=lambda: _env_bool("SPEEDTEST_ENABLED", True))
    speedtest_interval: int = field(default_factory=lambda: _env_int("SPEEDTEST_INTERVAL", 3600))
    # Seconds past the start of each interval to run at (avoids the :00 check)
    speedtest_offset: int = field(default_factory=lambda: _env_int("SPEEDTEST_OFFSET", 120))
    speedtest_duration: int = field(default_factory=lambda: _env_int("SPEEDTEST_DURATION", 8))
    speedtest_streams: int = field(default_factory=lambda: _env_int("SPEEDTEST_STREAMS", 4))
    speedtest_host: str = field(default_factory=lambda: _env("SPEEDTEST_HOST", "speed.cloudflare.com"))
