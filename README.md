# netwatch

Keeps a record of when your internet is up and when it isn't, so you can see
what happened overnight and show your ISP. Runs on an always-on box (mini-PC,
Raspberry Pi) on your home network.

- **Every 30 seconds** it pings your router, pings `1.1.1.1` and `8.8.8.8`,
  makes an HTTP request to Google's connectivity check, and looks up a DNS name.
  The internet counts as up if any internet target answers, so one flaky
  target doesn't show as an outage.
- **Every hour** it runs a speed test against Cloudflare
  (`speed.cloudflare.com`): latency, then download and upload over 4 parallel
  streams for 8 seconds each.
- **Dashboard**: a GitHub-style grid with days down the side and hours across
  the top, colored by uptime. Click an hour to see all 120 checks in it. The
  page also has an outage log, daily and weekly stats, a speed chart, and CSV
  export.

Each failed check is classified:

| status | meaning |
|---|---|
| `up`  | at least one internet target answered |
| `isp` | router reachable, internet not: **your ISP's fault** |
| `lan` | router unreachable too: WiFi, cable, or router problem on your side |

Gray cells mean no data: the mini-PC itself was off or the container was down.

## Deploy on the mini-PC

Requires Docker with the compose plugin.

```sh
git clone <this repo> netwatch && cd netwatch    # or copy the folder over
docker compose up -d --build
```

The container listens on port 8080 on the Docker network. Route a domain to it
with a reverse proxy (Traefik, Dokploy), or add `ports: ["8080:8080"]` to the
compose file and open `http://<mini-pc-ip>:8080`. Data lives in the
`netwatch-data` volume (SQLite) and survives rebuilds. The container restarts on boot (`restart: unless-stopped`),
so also make sure Docker itself starts on boot: `sudo systemctl enable docker`.

Put the mini-PC on a **wired** connection to the router if you can. Then a
`lan` result really means the router is down, not just weak WiFi.

### Configuration

Set these in `docker-compose.yml`:

| variable | default | |
|---|---|---|
| `TZ` | `Asia/Kolkata` | time zone used for the grid's days and hours |
| `NETWATCH_PORT` | `8080` | dashboard port |
| `CHECK_INTERVAL` | `30` | seconds between checks |
| `PING_TARGETS` | `1.1.1.1,8.8.8.8` | internet hosts to ping |
| `HTTP_PROBE_URL` | `http://connectivitycheck.gstatic.com/generate_204` | must return 204/200 |
| `GATEWAY` | `192.168.0.1` | router IP; set it to your router |
| `SPEEDTEST_ENABLED` | `1` | set `0` to turn speed tests off |
| `SPEEDTEST_INTERVAL` | `3600` | seconds between speed tests |
| `SPEEDTEST_DURATION` | `8` | seconds per direction |
| `SPEEDTEST_STREAMS` | `4` | parallel connections |

**Data usage:** each speed test moves about `speed × duration × 2` worth of
data. On a 50 Mbps line that's ~100 MB per test, or ~2.4 GB a day at one
test per hour. On a fast or capped plan, lower `SPEEDTEST_DURATION` or
raise `SPEEDTEST_INTERVAL`.

The container uses bridge networking, so it cannot auto-detect your router:
it only sees Docker's bridge gateway. Set `GATEWAY` to your router's IP.

## Export

From the buttons on the dashboard, or directly:

- `/export/checks.csv?start=2026-11-02&end=2026-11-08`: every check
- `/export/outages.csv?start=…&end=…`: start, end, duration, cause
- `/export/speedtests.csv?start=…&end=…`

Leave out `start`/`end` to get everything.

## Development

Plain Python 3.11+ with no dependencies (`ping` must be on the PATH).

```sh
python -m netwatch                          # dashboard on :8080, data in ./data
python -m unittest discover -s tests -t .   # tests

# preview the dashboard with three weeks of fake data
python scripts/seed_demo.py /tmp/demo.db
NETWATCH_DB=/tmp/demo.db NETWATCH_PORT=8091 SPEEDTEST_ENABLED=0 python -m netwatch
```

Layout:

- `netwatch/checks.py`: connectivity check and the 30s loop
- `netwatch/speedtest.py`: Cloudflare speed test and the hourly loop
- `netwatch/stats.py`: outage detection and the week/hour aggregation
- `netwatch/server.py`: JSON API, CSV export, static files
- `netwatch/static/`: the dashboard (vanilla JS, no build step)
